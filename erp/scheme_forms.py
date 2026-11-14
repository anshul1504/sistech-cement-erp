"""Incentive-rule editing and explicit retained calculation ranges."""
from django import forms
from django.forms import formset_factory
from .crm_forms import style
from .models import IncentiveScheme, SchemeVersion, CUSTOMER_KINDS, Partner, Product


class SchemeForm(forms.ModelForm):
    class Meta:
        model = IncentiveScheme
        fields = ['code', 'name', 'site']

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        from .crm import sites_for
        self.fields['site'].queryset = sites_for(user).filter(active=True)
        style(self)

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()


class VersionForm(forms.ModelForm):
    class Meta:
        model = SchemeVersion
        fields = ['starts_on', 'ends_on', 'product', 'partner', 'customer_kind', 'district', 'territory', 'basis', 'dealer_cap', 'notes']
        widgets = {'starts_on': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'), 'ends_on': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'), 'notes': forms.Textarea(attrs={'rows': 3})}
        labels = {'product': 'Product scope', 'partner': 'Customer scope', 'customer_kind': 'Customer type', 'district': 'District scope'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.order_by('name')
        self.fields['partner'].queryset = Partner.objects.filter(kind__in=CUSTOMER_KINDS).order_by('name')
        self.fields['product'].empty_label = 'All products'
        self.fields['partner'].empty_label = 'All customers'
        self.fields['customer_kind'].choices = [('', 'All customer types')] + [(k, k) for k in CUSTOMER_KINDS]
        self.fields['district'].help_text = 'Exact district match, ignoring case and repeated spaces. Uses the current customer master when calculating.'
        self.fields['dealer_cap'].help_text = 'Leave empty for no cap. Zero caps the incentive at zero.'
        style(self)


class SlabForm(forms.Form):
    minimum_sales = forms.DecimalField(label='Minimum eligible sales (₹)', max_digits=16, decimal_places=2, min_value=0)
    percentage = forms.DecimalField(label='Incentive (%)', max_digits=5, decimal_places=2, min_value=0, max_value=100)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self)


Slabs = formset_factory(SlabForm, extra=0, max_num=20, validate_max=True, absolute_max=25, can_delete=True)


class EvaluationForm(forms.Form):
    starts_on = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    ends_on = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    submission_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style(self)
