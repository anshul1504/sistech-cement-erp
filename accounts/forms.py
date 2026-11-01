from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm, SetPasswordForm, PasswordChangeForm
from django.contrib.auth.models import User, Group
from django.db.models import Q
from erp.models import Site
from .security import allow_attempt


def style_fields(form):
    for name, field in form.fields.items():
        if isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs['class'] = 'form-check-input'
        elif not isinstance(field.widget, forms.CheckboxSelectMultiple):
            field.widget.attrs['class'] = 'form-control'
        field.widget.attrs.setdefault('placeholder', field.label)


class SignInForm(AuthenticationForm):
    username = forms.CharField(label='Email address or username', max_length=254, widget=forms.TextInput(attrs={'autocomplete': 'username', 'placeholder': 'you@company.com', 'autofocus': True}))
    remember_me = forms.BooleanField(required=False, label='Keep me signed in for 8 hours')
    error_messages = {'invalid_login': 'We couldn’t sign you in. Check your details and try again.', 'inactive': 'We couldn’t sign you in. Contact your administrator for help.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields['password'].widget.attrs.update(placeholder='Enter your password', autocomplete='current-password')

    def clean(self):
        if self.is_bound and not allow_attempt(self.request, 'login', self.data.get('username', '')):
            raise forms.ValidationError('Too many sign-in attempts. Please wait 15 minutes and try again.')
        return super().clean()


class RecoveryForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields['email'].widget.attrs.update(placeholder='you@company.com', autocomplete='email')

    def get_users(self, email):
        users = list(super().get_users(email))
        return users if len(users) == 1 else []


class NewPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields['new_password1'].label = 'New password'
        self.fields['new_password2'].label = 'Confirm new password'


class ChangePasswordForm(PasswordChangeForm):
    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        style_fields(self)

    def clean_old_password(self):
        if self.request and not allow_attempt(self.request, 'password-change', str(self.user.pk)):
            raise forms.ValidationError('Too many attempts. Please wait 15 minutes before trying again.')
        return super().clean_old_password()


class MemberForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(queryset=Group.objects.order_by('name'), required=False, widget=forms.CheckboxSelectMultiple)
    sites = forms.ModelMultipleChoiceField(queryset=Site.objects.order_by('code'), required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['email'].required = True
        style_fields(self)
        if self.instance.pk:
            self.fields['roles'].initial = self.instance.groups.all()
            self.fields['sites'].initial = Site.objects.filter(siteaccess__user=self.instance)

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.exclude(pk=self.instance.pk).filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise forms.ValidationError('This email address is already associated with another account.')
        return email


class NewMemberForm(MemberForm):
    username = forms.CharField(max_length=150, label='Username', validators=User._meta.get_field('username').validators)
    password1 = forms.CharField(label='Initial password', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))
    password2 = forms.CharField(label='Confirm initial password', strip=False, widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order_fields(['first_name', 'last_name', 'username', 'email', 'password1', 'password2', 'is_active', 'roles', 'sites'])

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(Q(username__iexact=username) | Q(email__iexact=username)).exists():
            raise forms.ValidationError('This username is already in use.')
        return username

    def clean(self):
        data = super().clean()
        if data.get('password1') != data.get('password2'):
            self.add_error('password2', 'The passwords do not match.')
        return data

    def _post_clean(self):
        super()._post_clean()
        from django.contrib.auth.password_validation import validate_password
        if self.cleaned_data.get('password1'):
            self.instance.username = self.cleaned_data.get('username', '')
            try:
                validate_password(self.cleaned_data['password1'], self.instance)
            except forms.ValidationError as error:
                self.add_error('password1', error)
