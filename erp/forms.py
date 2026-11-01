from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory
from .models import Site, Product, Partner, Order, OrderLine, Production, QualityTest

class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs['class'] = 'form-check-input' if isinstance(f.widget, forms.CheckboxInput) else 'form-select' if isinstance(f.widget, forms.Select) else 'form-control'
            if isinstance(f.widget, forms.Textarea): f.widget.attrs['rows'] = 3

class SiteForm(StyledForm):
    class Meta:
        model = Site
        fields = ['code', 'name', 'kind', 'address', 'active']

class ProductForm(StyledForm):
    class Meta:
        model = Product
        fields = ['code', 'name', 'category', 'unit', 'bag_weight_kg', 'hsn', 'reorder_level', 'active']
    def clean(self):
        data = super().clean()
        if data.get('unit') == 'BAG' and not data.get('bag_weight_kg'): self.add_error('bag_weight_kg', 'Enter the SKU-specific bag weight.')
        return data

class PartnerForm(StyledForm):
    class Meta:
        model = Partner
        fields = ['code', 'name', 'kind', 'gstin', 'phone', 'territory', 'address', 'credit_limit', 'active']

class OrderForm(StyledForm):
    class Meta:
        model = Order
        fields = ['site', 'partner', 'due_date', 'notes']
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'})}

class LineForm(StyledForm):
    class Meta:
        model = OrderLine
        fields = ['product', 'quantity', 'rate']

Lines = inlineformset_factory(Order, OrderLine, form=LineForm, extra=0, min_num=1, validate_min=True, max_num=50, validate_max=True, can_delete=True)

class ProductionForm(StyledForm):
    class Meta:
        model = Production
        fields = ['site', 'product', 'batch', 'shift', 'planned_quantity', 'actual_quantity', 'notes']

class QualityForm(StyledForm):
    class Meta:
        model = QualityTest
        fields = ['production', 'test_name', 'result', 'disposition', 'notes']

class OpeningForm(forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.filter(active=True), widget=forms.Select(attrs={'class': 'form-select'}))
    product = forms.ModelChoiceField(queryset=Product.objects.filter(active=True), widget=forms.Select(attrs={'class': 'form-select'}))
    quantity = forms.DecimalField(min_value=Decimal('0.001'), max_digits=14, decimal_places=3, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '.001'}))
    reference = forms.CharField(max_length=60, help_text='Unique opening reference. Reusing it will not post stock twice.', widget=forms.TextInput(attrs={'class': 'form-control'}))
    notes = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}), required=True)
