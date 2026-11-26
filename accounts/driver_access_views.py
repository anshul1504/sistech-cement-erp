"""Administrator-managed driver app logins: one login ↔ one driver record, with a retained reason and session revocation."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache

from erp.models import Driver
from .views import require_admin, editable_member, audit, _end_sessions


class DriverAccessForm(forms.Form):
    driver = forms.ModelChoiceField(queryset=Driver.objects.none(), label='Driver', required=False)
    active = forms.BooleanField(required=False, label='Enable the driver app for this login')
    reason = forms.CharField(min_length=8, max_length=250, widget=forms.Textarea(attrs={'rows': 3}), label='Reason for this access change')

    def __init__(self, *args, member, current=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.member, self.current = member, current
        eligible = Q(active=True, user__isnull=True)
        if current:
            eligible |= Q(pk=current.pk)
        self.fields['driver'].queryset = Driver.objects.filter(eligible).order_by('name')
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean(self):
        data = super().clean()
        if data.get('active'):
            d = data.get('driver')
            if not d or not d.active:
                self.add_error('driver', 'Choose an active driver record.')
            elif d.user_id and d.user_id != self.member.pk:
                self.add_error('driver', 'This driver is already linked to another login.')
            if not self.member.is_active or not self.member.email.strip():
                raise forms.ValidationError('An enabled login with an email address is required for the driver app and email OTP.')
            if self.member.is_staff or self.member.is_superuser:
                raise forms.ValidationError('Use a dedicated non-staff driver login.')
        return data


@login_required
@never_cache
def driver_access(request, pk):
    require_admin(request)
    member = editable_member(pk)
    current = Driver.objects.filter(user=member).first()
    form = DriverAccessForm(request.POST if request.method == 'POST' else None, member=member, current=current,
                            initial={'driver': current.pk if current else None, 'active': current.app_enabled if current else False})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            member = type(member).objects.select_for_update().get(pk=member.pk)
            old = Driver.objects.select_for_update().filter(user=member).first()
            candidate = form.cleaned_data['driver']
            if candidate:
                Driver.objects.select_for_update().get(pk=candidate.pk)
            locked = DriverAccessForm(request.POST, member=member, current=old)
            if not locked.is_valid():
                form = locked
            else:
                data = locked.cleaned_data
                previous = f'driver={old.pk}, enabled={old.app_enabled}' if old else 'No link'
                target = data['driver'] or old
                if old and target and old.pk != target.pk:
                    Driver.objects.filter(pk=old.pk).update(user=None, app_enabled=False)
                if target:
                    Driver.objects.filter(pk=target.pk).update(user=member, app_enabled=data['active'])
                _end_sessions(member)
                from accounts.models import ApiToken
                from django.utils import timezone
                ApiToken.objects.filter(user=member, revoked_at__isnull=True).update(revoked_at=timezone.now())
                audit(request, 'Driver app access changed', f'user:{member.pk}',
                      f'{previous} → driver={target.pk if target else None}, enabled={data["active"]}. Reason: {data["reason"]}')
                messages.success(request, 'Driver app access saved. Existing sessions and app sign-ins were ended; the driver must sign in again.')
                return redirect('member_detail', pk=member.pk)
    return render(request, 'accounts/driver_access.html', {'title': 'Driver app access', 'member': member, 'form': form, 'link': current})
