from django.contrib.auth import views as auth, login
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views.generic import FormView
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.core.exceptions import PermissionDenied
from erp.models import AuditEvent
from .forms import SignInForm, RecoveryForm, NewPasswordForm, ChangePasswordForm, OTPForm
from .security import clear_identity_limit, allow_attempt
from .otp import pending_challenge, issue_challenge, verify_challenge, masked_email

def otp_required_for(identifier):
    """Email sign-ins always need the email code; username sign-ins skip it when configured."""
    return '@' in (identifier or '') or not settings.EMAIL_OTP_SKIP_FOR_USERNAME


class SignInView(auth.LoginView):
    template_name = 'registration/login.html'
    authentication_form = SignInForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        if request.path.startswith('/admin/') and request.user.is_authenticated and not request.user.is_staff:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        if settings.EMAIL_OTP_ENABLED and otp_required_for(form.cleaned_data['username']):
            user = form.get_user()
            existing = pending_challenge(self.request)
            if existing and existing.user_id == user.pk and existing.delivered and existing.expires_at > timezone.now():
                return redirect('otp_verify')
            self.request.session.cycle_key()
            try:
                challenge = issue_challenge(self.request, user)
            except ValidationError as error:
                form.add_error(None, error)
                return self.form_invalid(form)
            self.request.session['otp_challenge'] = str(challenge.pk)
            self.request.session['otp_next'] = self.get_success_url()
            self.request.session['otp_remember'] = bool(form.cleaned_data.get('remember_me'))
            self.request.session['otp_identifier'] = form.cleaned_data['username']
            self.request.session.set_expiry(15 * 60)
            return redirect('otp_verify')
        response = super().form_valid(form)
        if settings.EMAIL_OTP_ENABLED:
            # Username sign-in without OTP (allowed by EMAIL_OTP_SKIP_FOR_USERNAME).
            self.request.session['password_login_user'] = str(form.get_user().pk)
        self.request.session.set_expiry(8 * 60 * 60 if form.cleaned_data.get('remember_me') else 0)
        clear_identity_limit(form.cleaned_data['username'])
        return response


@method_decorator(never_cache, name='dispatch')
@method_decorator(sensitive_post_parameters('code'), name='dispatch')
class OTPVerifyView(FormView):
    template_name = 'registration/otp_verify.html'
    form_class = OTPForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('dashboard')
        self.challenge = pending_challenge(request)
        if not settings.EMAIL_OTP_ENABLED or not self.challenge:
            return redirect('login')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.challenge
        context.update(email_hint=masked_email(challenge.email), expired=challenge.expires_at <= timezone.now(),
                       resend_wait=max(0, settings.EMAIL_OTP_RESEND_SECONDS-int((timezone.now()-challenge.created_at).total_seconds())))
        return context

    def form_valid(self, form):
        challenge = pending_challenge(self.request)
        if not challenge:
            return redirect('login')
        if not allow_attempt(self.request, 'otp-verify', str(challenge.user_id), limit=10):
            form.add_error('code', 'Too many verification attempts. Wait 15 minutes and start again.')
            return self.form_invalid(form)
        user, error = verify_challenge(self.request, form.cleaned_data['code'])
        if not user:
            if not pending_challenge(self.request):
                return redirect('login')
            form.add_error('code', error)
            return self.form_invalid(form)
        destination = self.request.session.get('otp_next', '/')
        remember = self.request.session.get('otp_remember', False)
        identifier = self.request.session.get('otp_identifier', '')
        for key in ['otp_challenge', 'otp_next', 'otp_remember', 'otp_identifier']:
            self.request.session.pop(key, None)
        login(self.request, user, backend='accounts.backends.EmailOrUsernameBackend')
        self.request.session['email_otp_verified_user'] = str(user.pk)
        self.request.session['email_otp_verified_email'] = user.email.strip().casefold()
        self.request.session.set_expiry(8 * 60 * 60 if remember else 0)
        clear_identity_limit(identifier)
        if not url_has_allowed_host_and_scheme(destination, {self.request.get_host()}, require_https=self.request.is_secure()):
            destination = '/'
        return redirect(destination)


@require_POST
@never_cache
def otp_resend(request):
    challenge = pending_challenge(request)
    if not settings.EMAIL_OTP_ENABLED or not challenge:
        return redirect('login')
    try:
        replacement = issue_challenge(request, challenge.user, challenge.password_verified_at)
        request.session['otp_challenge'] = str(replacement.pk)
        messages.success(request, 'A new code has been sent. Use only the latest code.')
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
        if not pending_challenge(request):
            return redirect('login')
    return redirect('otp_verify')

class RecoveryView(auth.PasswordResetView):
    template_name = 'registration/password_reset_form.html'
    form_class = RecoveryForm
    email_template_name = 'registration/password_reset_email.txt'
    subject_template_name = 'registration/password_reset_subject.txt'

    def form_valid(self, form):
        if not allow_attempt(self.request, 'recovery', form.cleaned_data['email']):
            return redirect('password_reset_done')
        return super().form_valid(form)

class ResetConfirmView(auth.PasswordResetConfirmView):
    template_name = 'registration/password_reset_confirm.html'
    form_class = NewPasswordForm

    def form_valid(self, form):
        response = super().form_valid(form)
        from .models import UserProfile
        profile = UserProfile.for_user(form.user)
        profile.password_changed_at = timezone.now()
        profile.save(update_fields=['password_changed_at', 'updated_at'])
        AuditEvent.objects.create(actor=form.user, action='Password reset completed', reference=f'user:{form.user.pk}')
        return response
