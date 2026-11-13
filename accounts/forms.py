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
    username = forms.CharField(label='Email address or username', max_length=254, widget=forms.TextInput(attrs={'autocomplete': 'username', 'placeholder': 'Your email or username', 'autofocus': True}))
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


class OTPForm(forms.Form):
    code = forms.RegexField(regex=r'^\d{6}$', label='6-digit verification code', max_length=6,
        error_messages={'invalid': 'Enter the 6-digit code from your email.'},
        widget=forms.TextInput(attrs={'class': 'form-control otp-input', 'inputmode': 'numeric', 'autocomplete': 'one-time-code', 'pattern': '[0-9]{6}', 'placeholder': '000000', 'autofocus': True}))


class RecoveryForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)
        self.fields['email'].widget.attrs.update(placeholder='Your registered email', autocomplete='email')

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
        self.fields['old_password'].label = 'Current password'
        self.fields['new_password1'].label = 'New password'
        self.fields['new_password2'].label = 'Confirm new password'
        style_fields(self)
        self.fields['old_password'].widget.attrs['placeholder'] = 'Enter your current password'
        self.fields['new_password1'].widget.attrs['placeholder'] = 'At least 10 characters'
        self.fields['new_password2'].widget.attrs['placeholder'] = 'Type the new password again'

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


class ProfileForm(forms.Form):
    """Self-service details. Email, roles and sites remain admin-managed (they control sign-in and access)."""
    first_name = forms.CharField(label='First name', max_length=150, required=False)
    last_name = forms.CharField(label='Last name', max_length=150, required=False)
    phone = forms.CharField(label='Mobile number', max_length=20, required=False,
                            widget=forms.TextInput(attrs={'inputmode': 'tel', 'autocomplete': 'tel', 'placeholder': 'e.g. +91 98765 43210'}))
    designation = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={'placeholder': 'e.g. Plant manager'}))
    department = forms.CharField(max_length=80, required=False, widget=forms.TextInput(attrs={'placeholder': 'e.g. Production'}))
    photo = forms.FileField(label='Profile photo', required=False, help_text='Square PNG, JPG or WebP, up to 1 MB.',
                            widget=forms.FileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}))
    remove_photo = forms.BooleanField(label='Remove current photo', required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.FileInput)):
                field.widget.attrs['class'] = 'form-control'
        self.fields['first_name'].widget.attrs['autocomplete'] = 'given-name'
        self.fields['last_name'].widget.attrs['autocomplete'] = 'family-name'

    def clean_first_name(self):
        return self.cleaned_data['first_name'].strip()

    def clean_phone(self):
        import re
        value = self.cleaned_data['phone'].strip()
        if value and not re.fullmatch(r'\+?[0-9][0-9 \-]{8,17}[0-9]', value):
            raise forms.ValidationError('Enter a valid mobile number, e.g. +91 98765 43210.')
        return value

    def clean_photo(self):
        from erp.forms import read_image
        upload = self.cleaned_data.get('photo')
        return read_image(upload) if upload else None


class EmergencyContactForm(forms.ModelForm):
    class Meta:
        from .models import EmergencyContact
        model = EmergencyContact
        fields = ['name', 'relationship', 'phone', 'alt_phone', 'address', 'is_primary']
        widgets = {'phone': forms.TextInput(attrs={'inputmode': 'tel', 'placeholder': '+91 98765 43210'}),
                   'alt_phone': forms.TextInput(attrs={'inputmode': 'tel'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
        self.fields['relationship'].choices = [('', 'Select relationship')] + list(self.fields['relationship'].choices)[1:]

    def _clean_phone(self, name, required):
        import re
        value = (self.cleaned_data.get(name) or '').strip()
        if value and not re.fullmatch(r'\+?[0-9][0-9 \-]{8,17}[0-9]', value):
            raise forms.ValidationError('Enter a valid phone number, e.g. +91 98765 43210.')
        return value

    def clean_phone(self):
        return self._clean_phone('phone', True)

    def clean_alt_phone(self):
        return self._clean_phone('alt_phone', False)
