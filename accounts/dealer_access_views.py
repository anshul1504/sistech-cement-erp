"""Administrator-managed dealer login links with retained reason and session revocation."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from erp.models import DealerLedgerAccess, Partner
from .views import require_admin, editable_member, audit, _end_sessions


class DealerAccessForm(forms.Form):
    partner = forms.ModelChoiceField(queryset=Partner.objects.none(), label='Dealer', required=False)
    active = forms.BooleanField(required=False, label='Enable dealer self-service')
    reason = forms.CharField(min_length=8, max_length=250, widget=forms.Textarea(attrs={'rows': 3}), label='Reason for this access change')

    def __init__(self, *args, member, access=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.member, self.access = member, access
        from django.db.models import Q
        eligible = Q(kind='Dealer', active=True)
        if access:
            eligible |= Q(pk=access.partner_id)
        self.fields['partner'].queryset = Partner.objects.filter(eligible).order_by('name')
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'

    def clean(self):
        data = super().clean()
        if data.get('active'):
            p = data.get('partner')
            if not p or p.kind != 'Dealer' or not p.active:
                self.add_error('partner', 'Choose an active Dealer record.')
            if not self.member.is_active or not self.member.email.strip():
                raise forms.ValidationError('An enabled login with an email address is required for dealer self-service and email OTP.')
            if self.member.is_staff or self.member.is_superuser:
                raise forms.ValidationError('Use a dedicated non-staff dealer login.')
        return data


@login_required
@never_cache
def dealer_access(request, pk):
    require_admin(request)
    member = editable_member(pk)
    access = DealerLedgerAccess.objects.filter(user=member).select_related('partner').first()
    form = DealerAccessForm(request.POST if request.method == 'POST' else None, member=member, access=access,
                            initial={'partner': access.partner_id if access else None, 'active': access.active if access else False})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            # Serialize link creation and reassignment on the account, including first grant.
            member = type(member).objects.select_for_update().get(pk=member.pk)
            old = DealerLedgerAccess.objects.filter(user=member).first()
            # Revalidate under the lock: user or dealer may have changed while the form was open.
            candidate = form.cleaned_data['partner']
            if candidate:
                Partner.objects.select_for_update().get(pk=candidate.pk)
            locked = DealerAccessForm(request.POST, member=member, access=old)
            if not locked.is_valid():
                form = locked
            else:
                data = locked.cleaned_data
                previous = f'partner={old.partner_id}, active={old.active}' if old else 'No link'
                partner = data['partner'] or (old.partner if old else None)
                if partner:
                    DealerLedgerAccess.objects.update_or_create(user=member, defaults={'partner': partner, 'active': data['active']})
                _end_sessions(member)
                audit(request, 'Dealer self-service access changed', f'user:{member.pk}',
                      f'{previous} → partner={partner.pk if partner else None}, active={data["active"]}. Reason: {data["reason"]}')
                messages.success(request, 'Dealer self-service access saved. Existing sessions were signed out; the user must sign in again.')
                return redirect('member_detail', pk=member.pk)
    return render(request, 'accounts/dealer_access.html', {'title': 'Dealer self-service access', 'member': member, 'form': form, 'link': access})
