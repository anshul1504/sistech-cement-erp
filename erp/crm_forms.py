"""CRM forms reuse the existing partner tax/address validation."""
from django import forms
from django.contrib.auth import get_user_model
from .forms import PartnerForm, PartnerDocumentForm, normalise_mobile
from .models import CRMLead, CRMActivity, CRMPolicy, SalesTarget, CUSTOMER_KINDS, PARTNER_DOCUMENT_TYPES

PROFILE_FIELDS = ['kind', 'name', 'territory', 'contact_person', 'phone', 'alt_phone', 'email', 'gstin', 'pan', 'address', 'city', 'district', 'state',
                  'pincode', 'shipping_address', 'credit_limit', 'payment_terms_days', 'security_deposit', 'bank_name', 'bank_account_number', 'bank_ifsc', 'notes']
PROTECTED_CUSTOMER_FIELDS = ['kind', 'name', 'gstin', 'pan', 'address', 'city', 'district', 'state', 'pincode', 'shipping_address',
                             'credit_limit', 'payment_terms_days', 'security_deposit', 'bank_name', 'bank_account_number', 'bank_ifsc']


def style(form):
    for field in form.fields.values():
        field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)) else 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')
    return form


class LeadForm(forms.ModelForm):
    mobile = forms.CharField(max_length=25)
    class Meta:
        model = CRMLead
        fields = ['site', 'owner', 'business_name', 'contact_name', 'mobile', 'email', 'kind', 'source', 'district', 'territory', 'estimated_value', 'next_follow_up', 'notes']
        widgets = {'next_follow_up': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from .crm import sites_for, owners_for
        self.fields['site'].queryset = sites_for(user).filter(active=True)
        self.fields['owner'].queryset = owners_for(user)
        if self.instance.pk:
            self.fields['site'].disabled = True
        if not user.has_perm('erp.assign_crmlead'):
            self.fields['owner'].queryset = get_user_model().objects.filter(pk=user.pk)
            self.fields['owner'].initial = user.pk
            self.fields['owner'].disabled = True
        self.fields['mobile'].widget.attrs.update(inputmode='tel', placeholder='10-digit mobile')
        style(self)

    def clean_mobile(self):
        mobile = normalise_mobile(self.cleaned_data['mobile'])
        if not mobile:
            raise forms.ValidationError('Enter a valid 10-digit Indian mobile number.')
        return mobile


class ActivityForm(forms.ModelForm):
    class Meta:
        model = CRMActivity
        fields = ['kind', 'happened_on', 'outcome', 'note', 'next_follow_up']
        widgets = {'happened_on': forms.DateInput(attrs={'type': 'date'}), 'next_follow_up': forms.DateInput(attrs={'type': 'date'}), 'note': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self)


class DealerProfileForm(PartnerForm):
    class Meta(PartnerForm.Meta):
        fields = PROFILE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['kind'].choices = [(k, k) for k in CUSTOMER_KINDS]
        self.fields['credit_limit'].label = 'Requested credit limit (₹)'
        self.fields['payment_terms_days'].label = 'Requested payment terms (days)'
        self.fields['security_deposit'].label = 'Declared security deposit (₹)'
        self.fields['security_deposit'].help_text = 'Master-data declaration; this does not record a payment or create an accounting receipt.'

    def sections(self):
        return [(title, note, [self[name] for name in names if name in self.fields]) for title, note, names in self.SECTIONS]


class DealerUploadForm(PartnerDocumentForm):
    pass


class PolicyForm(forms.ModelForm):
    required_documents = forms.MultipleChoiceField(choices=PARTNER_DOCUMENT_TYPES, widget=forms.CheckboxSelectMultiple, required=False)

    class Meta:
        model = CRMPolicy
        fields = ['required_documents', 'require_gstin']
        labels = {'require_gstin': 'Require GSTIN for all new submissions'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self)


class TargetForm(forms.ModelForm):
    class Meta:
        model = SalesTarget
        fields = ['site', 'owner', 'month', 'sales_amount', 'new_dealers', 'note']
        widgets = {'month': forms.DateInput(attrs={'type': 'date'}), 'note': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from .crm import sites_for, owners_for
        self.fields['site'].queryset = sites_for(user).filter(active=True)
        self.fields['owner'].queryset = owners_for(user)
        self.fields['month'].help_text = 'Choose any day in the target month.'
        self.fields['sales_amount'].widget.attrs.update(step='0.01', min='0')
        style(self)
