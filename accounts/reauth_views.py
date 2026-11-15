from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from erp.models import AuditEvent
from .forms import ReauthenticateForm
from .reauth import confirm, safe_destination
from .security import access_allowed


@login_required
@never_cache
@sensitive_post_parameters('password')
def reauthenticate(request):
    destination = safe_destination(request.POST.get('next') if request.method == 'POST' else request.GET.get('next'))
    form = ReauthenticateForm(request.POST or None, request=request)
    if request.method == 'POST' and form.is_valid():
        # Recheck current credentials/policy under the same user lock as OTP issuance.
        with transaction.atomic():
            user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
            if not access_allowed(user) or user.get_session_auth_hash() != request.user.get_session_auth_hash():
                logout(request)
                return redirect('login')
            if not user.check_password(form.cleaned_data['password']):
                form.add_error('password', 'We could not confirm your password. Try again.')
                AuditEvent.objects.create(actor=user, action='Sensitive confirmation failed', reference=f'user:{user.pk}')
            else:
                request.session.cycle_key()
                confirm(request.session, user)
                AuditEvent.objects.create(actor=user, action='Sensitive identity confirmed', reference=f'user:{user.pk}')
                return redirect(destination)
    return render(request, 'accounts/reauthenticate.html', {
        'title': 'Confirm your identity', 'form': form, 'next': destination,
    })
