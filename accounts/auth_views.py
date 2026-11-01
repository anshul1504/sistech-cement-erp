from django.contrib.auth import views as auth
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.core.exceptions import PermissionDenied
from erp.models import AuditEvent
from .forms import SignInForm, RecoveryForm, NewPasswordForm, ChangePasswordForm
from .security import clear_identity_limit, allow_attempt

class SignInView(auth.LoginView):
    template_name = 'registration/login.html'
    authentication_form = SignInForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        if request.path.startswith('/admin/') and request.user.is_authenticated and not request.user.is_staff:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        self.request.session.set_expiry(8 * 60 * 60 if form.cleaned_data.get('remember_me') else 0)
        clear_identity_limit(form.cleaned_data['username'])
        return response

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
        AuditEvent.objects.create(actor=form.user, action='Password reset completed', reference=f'user:{form.user.pk}')
        return response

class ChangePasswordView(auth.PasswordChangeView):
    template_name = 'registration/password_change_form.html'
    form_class = ChangePasswordForm
    success_url = reverse_lazy('password_change_done')

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'request': self.request}

    def form_valid(self, form):
        response = super().form_valid(form)
        AuditEvent.objects.create(actor=self.request.user, action='Password changed', reference=f'user:{self.request.user.pk}')
        return response

@login_required
def profile(request):
    return render(request, 'accounts/profile.html', {'title': 'My account', 'roles': request.user.groups.order_by('name'), 'assigned_sites': request.user.siteaccess_set.select_related('site')})
