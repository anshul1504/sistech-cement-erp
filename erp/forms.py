from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory
import re
from .models import Site, Product, Partner, Order, OrderLine, Production, QualityTest, Organization, OrganizationDocument, Influencer, InfluencerActivity, PriceRule, PurchaseRequest, PurchaseRequestLine, SupplierQuote, CUSTOMER_KINDS, LEAD_KINDS, clean_place

class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs['class'] = 'form-check-input' if isinstance(f.widget, forms.CheckboxInput) else 'form-select' if isinstance(f.widget, forms.Select) else 'form-control'
            if isinstance(f.widget, forms.Textarea): f.widget.attrs['rows'] = 3

class SiteForm(StyledForm):
    """Plants, depots and warehouses. GSTIN is checked against the state and the company PAN."""
    SECTIONS = [
        ('Basics', 'What this location is and what it does.', ['kind', 'facility_type', 'code', 'name', 'active']),
        ('Address', 'Where goods are produced, stored or dispatched from.', ['address', 'city', 'district', 'state', 'pincode']),
        ('Registration & contact', 'State GST registration and the people to reach.', ['gstin', 'manager', 'phone', 'email']),
        ('Dispatch settings', 'How trucks leave this location.', ['uses_weighbridge', 'stock_post_event', 'weight_tolerance_pct', 'scale_id', 'loading_bays']),
        ('Capacity & notes', 'Installed capacity and anything the team should know.', ['capacity_tpa', 'notes']),
    ]

    class Meta:
        model = Site
        fields = ['kind', 'facility_type', 'code', 'name', 'active', 'address', 'city', 'district', 'state', 'pincode',
                  'gstin', 'manager', 'phone', 'email', 'uses_weighbridge', 'stock_post_event', 'weight_tolerance_pct', 'scale_id', 'loading_bays', 'capacity_tpa', 'notes']
        widgets = {'address': forms.Textarea(attrs={'rows': 2}), 'notes': forms.Textarea(attrs={'rows': 3})}

    def _dispatch_defaults(self):
        for name in ['stock_post_event', 'weight_tolerance_pct']:
            self.fields[name].required = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth.models import User
        self.fields['manager'].queryset = User.objects.filter(is_active=True).order_by('first_name', 'username')
        self.fields['manager'].label_from_instance = lambda u: f'{u.get_full_name() or u.username} (@{u.username})'
        self.fields['manager'].empty_label = 'Not assigned'
        self.fields['kind'].choices = [('', 'Select type')] + list(self.fields['kind'].choices)[1:]
        self.fields['facility_type'].choices = [('', 'Select facility')] + list(self.fields['facility_type'].choices)[1:]
        self.fields['state'].choices = [('', 'Select state')] + list(self.fields['state'].choices)[1:]
        self.fields['code'].widget.attrs.update(placeholder='e.g. PLT-SATNA or DPT-BPL', style='text-transform:uppercase')
        self.fields['gstin'].widget.attrs.update(placeholder='e.g. 23ABCDE1234F1Z5', style='text-transform:uppercase', autocomplete='off')
        self.fields['pincode'].widget.attrs.update(inputmode='numeric', maxlength='6')
        self.fields['phone'].widget.attrs.update(inputmode='tel', placeholder='e.g. +91 98765 43210')
        self.fields['capacity_tpa'].widget.attrs.update(step='any', placeholder='e.g. 1000000')
        self._dispatch_defaults()
        self.fields['weight_tolerance_pct'].widget.attrs.update(step='0.05', min='0', max='5')
        self.fields['loading_bays'].widget.attrs.update(placeholder='Bay 1, Bay 2, Bulk 1')

    def clean_stock_post_event(self):
        return self.cleaned_data.get('stock_post_event') or 'gate_out'

    def clean_weight_tolerance_pct(self):
        v = self.cleaned_data.get('weight_tolerance_pct')
        if v is None:
            return Decimal('0.50')
        if v > 5:
            raise forms.ValidationError('Keep tolerance at 5% or less.')
        return v

    def sections(self):
        return [(title, note, [self[name] for name in names]) for title, note, names in self.SECTIONS]

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()

    def clean(self):
        from .models import PLANT_FACILITIES
        data = super().clean()
        kind, facility = data.get('kind'), data.get('facility_type')
        if facility:
            if kind == 'Plant' and facility not in PLANT_FACILITIES:
                self.add_error('facility_type', 'Choose a plant facility (cement unit, packing plant or rolling mill).')
            elif kind in ('Depot', 'Warehouse') and facility in PLANT_FACILITIES:
                self.add_error('facility_type', 'Depots and warehouses cannot be a manufacturing facility.')
        pincode = (data.get('pincode') or '').strip()
        if pincode and not re.fullmatch(r'[1-9]\d{5}', pincode):
            self.add_error('pincode', 'Enter a 6-digit PIN code.')
        phone = (data.get('phone') or '').strip()
        if phone and not re.fullmatch(r'\+?[0-9][0-9 \-]{8,17}[0-9]', phone):
            self.add_error('phone', 'Enter a valid phone number, e.g. +91 98765 43210.')
        gstin = (data.get('gstin') or '').strip().upper()
        data['gstin'] = gstin
        if gstin:
            if not re.fullmatch(r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', gstin):
                self.add_error('gstin', 'Enter a valid 15-character GSTIN, e.g. 23ABCDE1234F1Z5.')
            else:
                state = data.get('state')
                if state and gstin[:2] != state:
                    self.add_error('gstin', f'GSTIN starts with {gstin[:2]}, but this location is in state code {state}.')
                pan = Organization.objects.filter(pk=1).values_list('pan', flat=True).first()
                if pan and gstin[2:12] != pan:
                    self.add_error('gstin', f'GSTIN must contain the company PAN {pan} (characters 3–12).')
        return data

class ProductForm(StyledForm):
    """Products & materials, with cement- and TMT-specific rules. Unit and bag weight lock once used (see views)."""
    SECTIONS = [
        ('Basics', 'Identity and where it is used.', ['category', 'code', 'name', 'brand', 'active']),
        ('Specification', 'Grade and physical details used on orders, dispatch and test certificates.',
         ['grade', 'standard', 'bag_weight_kg', 'bar_diameter_mm', 'weight_per_metre_kg', 'bar_length_m', 'bars_per_bundle']),
        ('Stock & tax', 'Unit for stock and orders, reorder alert, HSN and GST.', ['unit', 'reorder_level', 'reorder_quantity', 'max_level', 'hsn', 'gst_rate']),
        ('Notes', 'Optional description for the team.', ['description']),
    ]
    # Which category each specification field belongs to (used by the form's JavaScript and validation).
    FIELD_SCOPE = {'bag_weight_kg': 'Cement', 'bar_diameter_mm': 'TMT', 'weight_per_metre_kg': 'TMT', 'bar_length_m': 'TMT', 'bars_per_bundle': 'TMT'}

    class Meta:
        model = Product
        fields = ['category', 'code', 'name', 'brand', 'active', 'grade', 'standard', 'bag_weight_kg', 'bar_diameter_mm',
                  'weight_per_metre_kg', 'bar_length_m', 'bars_per_bundle', 'unit', 'reorder_level', 'reorder_quantity', 'max_level', 'hsn', 'gst_rate', 'description']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import CEMENT_GRADES, TMT_GRADES
        self.fields['grade'].choices = [('', 'Select grade'), ('Cement grades', CEMENT_GRADES), ('TMT grades', TMT_GRADES)]
        self.fields['category'].choices = [('', 'Select category')] + list(self.fields['category'].choices)[1:]
        self.fields['bar_diameter_mm'].choices = [('', 'Select size')] + list(self.fields['bar_diameter_mm'].choices)[1:]
        self.fields['gst_rate'].choices = [('', 'Not set')] + list(self.fields['gst_rate'].choices)[1:]
        self.fields['code'].widget.attrs.update(placeholder='e.g. CEM-OPC53-50 or TMT-500D-12', style='text-transform:uppercase')
        self.fields['hsn'].widget.attrs.update(inputmode='numeric', placeholder='e.g. 2523 or 7214')
        self.fields['standard'].widget.attrs['placeholder'] = 'e.g. IS 269:2015'
        for name in ['bag_weight_kg', 'weight_per_metre_kg', 'bar_length_m', 'reorder_level', 'reorder_quantity']:
            self.fields[name].widget.attrs['step'] = 'any'
        self.fields['reorder_quantity'].required = False
        self.fields['max_level'].required = False
        if not self.instance.pk and not self.is_bound:
            # New products start from the organisation's standard cement bag weight.
            weight = Organization.objects.filter(pk=1).values_list('default_bag_weight', flat=True).first()
            self.initial.setdefault('bag_weight_kg', weight or 50)
            self.initial.setdefault('bar_length_m', 12)

    def sections(self):
        return [(title, note, [self[name] for name in names]) for title, note, names in self.SECTIONS]

    def clean_reorder_quantity(self):
        return self.cleaned_data.get('reorder_quantity') or Decimal('0')

    def clean_max_level(self):
        value = self.cleaned_data.get('max_level') or Decimal('0')
        level = self.cleaned_data.get('reorder_level') or Decimal('0')
        if value and value <= level:
            raise forms.ValidationError('Maximum stock must be above the reorder level.')
        return value

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()

    def clean_hsn(self):
        value = self.cleaned_data['hsn'].strip()
        if value and not re.fullmatch(r'\d{4}|\d{6}|\d{8}', value):
            raise forms.ValidationError('HSN must be 4, 6 or 8 digits.')
        return value

    def clean(self):
        from .models import CEMENT_GRADES, TMT_GRADES, GRADE_STANDARDS, DEFAULT_HSN
        data = super().clean()
        category, grade, unit = data.get('category'), data.get('grade'), data.get('unit')
        cement_grades, tmt_grades = {g for g, _ in CEMENT_GRADES}, {g for g, _ in TMT_GRADES}
        if category == 'Cement':
            if not grade: self.add_error('grade', 'Select the cement grade.')
            elif grade not in cement_grades: self.add_error('grade', 'Choose a cement grade (OPC, PPC, PSC or PCC).')
        elif category == 'TMT':
            if not grade: self.add_error('grade', 'Select the TMT grade.')
            elif grade not in tmt_grades: self.add_error('grade', 'Choose a steel grade (Fe 415 to Fe 600).')
            if not data.get('bar_diameter_mm'): self.add_error('bar_diameter_mm', 'Select the bar size.')
            if unit == 'BAG': self.add_error('unit', 'TMT bars are stocked in MT, KG or pieces, not bags.')
        elif grade:
            data['grade'] = ''  # grades only apply to finished cement and TMT
        if unit == 'BAG' and not data.get('bag_weight_kg') and 'bag_weight_kg' not in self.errors:
            self.add_error('bag_weight_kg', 'Enter the SKU-specific bag weight.')
        # Clear fields that do not apply to this category, and fill sensible defaults.
        for name, scope in self.FIELD_SCOPE.items():
            if category != scope and not self.fields[name].disabled:
                data[name] = None
        if category == 'TMT' and data.get('bar_diameter_mm') and not data.get('weight_per_metre_kg'):
            d = Decimal(data['bar_diameter_mm'])
            data['weight_per_metre_kg'] = (d * d / Decimal('162')).quantize(Decimal('0.001'))
        if data.get('grade') and not (data.get('standard') or '').strip():
            data['standard'] = GRADE_STANDARDS.get(data['grade'], '')
        if category in DEFAULT_HSN and not data.get('hsn') and 'hsn' not in self.errors:
            data['hsn'] = DEFAULT_HSN[category]
        return data

class PartnerForm(StyledForm):
    """Dealers, distributors, retailers, institutional buyers, suppliers and transporters."""
    SECTIONS = [
        ('Basics', 'Who this partner is and where they operate.', ['kind', 'code', 'name', 'territory', 'active']),
        ('Contact', 'The person and numbers to reach.', ['contact_person', 'phone', 'alt_phone', 'email']),
        ('Tax & address', 'GST registration and billing / delivery addresses.', ['gstin', 'pan', 'address', 'city', 'district', 'state', 'pincode', 'shipping_address']),
        ('Commercial terms', 'Credit and payment terms used when approving sales orders.', ['credit_limit', 'payment_terms_days', 'security_deposit']),
        ('Bank details', 'Used to pay suppliers and transporters.', ['bank_name', 'bank_account_number', 'bank_ifsc']),
        ('Notes', 'Anything the team should know.', ['notes']),
    ]
    TEXT_FORMATS = {
        'gstin': (r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', 'Enter a valid 15-character GSTIN, e.g. 23ABCDE1234F1Z5.'),
        'pan': (r'[A-Z]{5}\d{4}[A-Z]', 'Enter a valid 10-character PAN, e.g. ABCDE1234F.'),
        'bank_ifsc': (r'[A-Z]{4}0[A-Z0-9]{6}', 'Enter a valid 11-character IFSC, e.g. SBIN0001234.'),
        'pincode': (r'[1-9]\d{5}', 'Enter a 6-digit PIN code.'),
        'bank_account_number': (r'\d{9,18}', 'Account number must be 9–18 digits.'),
    }

    class Meta:
        model = Partner
        fields = ['kind', 'code', 'name', 'territory', 'active', 'contact_person', 'phone', 'alt_phone', 'email', 'gstin', 'pan', 'address',
                  'city', 'district', 'state', 'pincode', 'shipping_address', 'credit_limit', 'payment_terms_days', 'security_deposit',
                  'bank_name', 'bank_account_number', 'bank_ifsc', 'notes']
        widgets = {'address': forms.Textarea(attrs={'rows': 2}), 'shipping_address': forms.Textarea(attrs={'rows': 2}), 'notes': forms.Textarea(attrs={'rows': 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['kind'].choices = [('', 'Select type'), ('Customers', [c for c in Partner._meta.get_field('kind').choices if c[0] in ('Dealer', 'Distributor', 'Retailer', 'Institutional')]),
                                       ('Vendors', [c for c in Partner._meta.get_field('kind').choices if c[0] in ('Supplier', 'Transporter')])]
        self.fields['state'].choices = [('', 'Select state')] + list(self.fields['state'].choices)[1:]
        if 'code' in self.fields:
            self.fields['code'].widget.attrs.update(placeholder='e.g. DLR-BPL-001', style='text-transform:uppercase')
        for name in ['gstin', 'pan', 'bank_ifsc']:
            self.fields[name].widget.attrs.update(style='text-transform:uppercase', autocomplete='off')
        for name in ['phone', 'alt_phone']:
            self.fields[name].widget.attrs.update(inputmode='tel', placeholder='e.g. +91 98765 43210')
        self.fields['pincode'].widget.attrs.update(inputmode='numeric', maxlength='6')
        self.fields['credit_limit'].widget.attrs.update(step='any')
        self.fields['security_deposit'].widget.attrs.update(step='any')

    def sections(self):
        return [(title, note, [self[name] for name in names]) for title, note, names in self.SECTIONS]

    def clean_code(self):
        return self.cleaned_data['code'].strip().upper()

    def clean(self):
        data = super().clean()
        for name, (pattern, message) in self.TEXT_FORMATS.items():
            value = (data.get(name) or '').strip()
            value = value.upper() if name in ('gstin', 'pan', 'bank_ifsc') else value.replace(' ', '')
            data[name] = value
            if value and not re.fullmatch(pattern, value):
                self.add_error(name, message)
        for name in ['phone', 'alt_phone']:
            value = (data.get(name) or '').strip()
            data[name] = value
            if value and not re.fullmatch(r'\+?[0-9][0-9 \-]{8,17}[0-9]', value):
                self.add_error(name, 'Enter a valid phone number, e.g. +91 98765 43210.')
        gstin, pan, state = data.get('gstin'), data.get('pan'), data.get('state')
        if gstin and 'gstin' not in self.errors:
            if pan and 'pan' not in self.errors and gstin[2:12] != pan:
                self.add_error('gstin', 'GSTIN characters 3–12 must match the PAN.')
            if state and gstin[:2] != state:
                self.add_error('gstin', f'GSTIN starts with {gstin[:2]}, but the state code is {state}.')
            if not pan:
                data['pan'] = gstin[2:12]  # PAN is embedded in every GSTIN
        if data.get('kind') in ('Supplier', 'Transporter'):
            data['credit_limit'] = data.get('credit_limit') or 0
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        for name in list(self.TEXT_FORMATS) + ['phone', 'alt_phone']:
            setattr(obj, name, self.cleaned_data.get(name, getattr(obj, name)))
        if commit:
            obj.save()
        return obj



class OrderForm(StyledForm):
    class Meta:
        model = Order
        fields = ['site', 'partner', 'due_date', 'place_of_supply', 'freight_terms', 'customer_ref', 'payment_terms_days', 'receipt_tolerance_pct', 'advance_amount',
                  'delivery_terms', 'ship_to', 'notes']
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'}), 'ship_to': forms.Textarea(attrs={'rows': 2}), 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].label = 'Plant / depot'
        self.fields['site'].empty_label = 'Choose plant / depot'
        self.fields['partner'].empty_label = 'Choose ' + ('supplier' if self.instance.kind == 'Purchase' else 'customer')
        self.fields['place_of_supply'].choices = [('', "Partner's state")] + list(self.fields['place_of_supply'].choices)[1:]
        if self.instance.kind == 'Purchase':
            for name in ['place_of_supply', 'freight_terms', 'ship_to']:
                del self.fields[name]
            self.fields['customer_ref'].label = 'Supplier quotation / reference'
            self.fields['partner'].label = 'Supplier'
            self.fields['due_date'].label = 'Delivery by'
            self.fields['receipt_tolerance_pct'].widget.attrs.update(step='0.1', min='0', max='10')
        else:
            self.fields['partner'].label = 'Customer'
            for name in ['payment_terms_days', 'receipt_tolerance_pct', 'advance_amount', 'delivery_terms']:
                del self.fields[name]

    def clean_receipt_tolerance_pct(self):
        v = self.cleaned_data.get('receipt_tolerance_pct') or Decimal('0')
        if v > 10:
            raise forms.ValidationError('Keep over-receipt tolerance at 10% or less.')
        return v

    def clean_due_date(self):
        due = self.cleaned_data['due_date']
        from django.utils import timezone
        if due and due < timezone.localdate() and not self.instance.pk:
            raise forms.ValidationError('Required-by date cannot be in the past.')
        return due


class LineForm(StyledForm):
    class Meta:
        model = OrderLine
        fields = ['product', 'quantity', 'rate']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(active=True).order_by('category', 'name')  # inactive SKUs stay off new orders
        self.fields['product'].empty_label = 'Choose product'
        self.fields['rate'].required = False
        self.fields['rate'].widget.attrs.update(step='0.01', placeholder='List price')
        self.fields['quantity'].widget.attrs.update(step='any', min='0')

class PurchaseRequestForm(StyledForm):
    class Meta:
        model = PurchaseRequest
        fields = ['site', 'needed_by', 'priority', 'source', 'purpose']
        widgets = {'needed_by': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].empty_label = 'Choose plant / depot'
        self.fields['purpose'].widget.attrs.update(placeholder='e.g. Coal for kiln, November')


class PRLineForm(StyledForm):
    class Meta:
        model = PurchaseRequestLine
        fields = ['product', 'quantity', 'estimated_rate', 'specification']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(active=True).order_by('category', 'name')
        self.fields['product'].empty_label = 'Choose item'
        self.fields['quantity'].widget.attrs.update(step='any', min='0')
        self.fields['estimated_rate'].widget.attrs.update(step='0.01', min='0', placeholder='Optional')
        self.fields['specification'].widget.attrs.update(placeholder='Grade, size, make…')


PRLines = inlineformset_factory(PurchaseRequest, PurchaseRequestLine, form=PRLineForm, extra=0, min_num=1, validate_min=True, max_num=40, validate_max=True, can_delete=True)


class QuoteForm(StyledForm):
    class Meta:
        model = SupplierQuote
        fields = ['reference', 'quote_date', 'valid_until', 'freight', 'lead_time_days', 'payment_terms_days', 'notes']
        widgets = {'quote_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'), 'valid_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}


Lines = inlineformset_factory(Order, OrderLine, form=LineForm, extra=0, min_num=1, validate_min=True, max_num=50, validate_max=True, can_delete=True)

class ProductionForm(StyledForm):
    class Meta:
        model = Production
        fields = ['site', 'product', 'batch', 'shift', 'planned_quantity', 'actual_quantity', 'notes']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(active=True, category__in=['Cement', 'TMT', 'Raw material']).order_by('category', 'name')  # finished goods + intermediates (clinker, billets)

class QualityForm(StyledForm):
    class Meta:
        model = QualityTest
        fields = ['production', 'test_name', 'result', 'disposition', 'notes']

class OpeningForm(forms.Form):
    site = forms.ModelChoiceField(queryset=Site.objects.filter(active=True), widget=forms.Select(attrs={'class': 'form-select'}))
    product = forms.ModelChoiceField(queryset=Product.objects.filter(active=True), widget=forms.Select(attrs={'class': 'form-select'}))
    quantity = forms.DecimalField(min_value=Decimal('0.001'), max_digits=14, decimal_places=3, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '.001'}))
    reference = forms.CharField(max_length=60, help_text='Unique opening reference. Reusing it will not post stock twice.', widget=forms.TextInput(attrs={'class': 'form-control'}))
    unit_cost = forms.DecimalField(label='Rate (₹ per unit)', required=False, min_value=Decimal('0'), max_digits=14, decimal_places=4,
                                   help_text='Cost for stock valuation. Leave empty to keep the current average cost.',
                                   widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}))
    notes = forms.CharField(widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}), required=True)


IMAGE_SIGNATURES = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
)
MAX_LOGO_BYTES = 1024 * 1024


def read_image(upload):
    """Return (bytes, content_type) for a PNG/JPEG/WebP upload, checked by content not extension.
    SVG and other formats are rejected (SVG can carry scripts)."""
    if upload.size > MAX_LOGO_BYTES:
        raise forms.ValidationError('Image must be 1 MB or smaller.')
    data = upload.read()
    for signature, content_type in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return data, content_type
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return data, 'image/webp'
    raise forms.ValidationError('Upload a PNG, JPG or WebP image.')


FORMATS = {
    'gstin': (r'\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]', 'Enter a valid 15-character GSTIN, e.g. 23ABCDE1234F1Z5.'),
    'pan': (r'[A-Z]{5}\d{4}[A-Z]', 'Enter a valid 10-character PAN, e.g. ABCDE1234F.'),
    'tan': (r'[A-Z]{4}\d{5}[A-Z]', 'Enter a valid 10-character TAN, e.g. BPLA12345B.'),
    'cin': (r'([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})|([A-Z]{3}-\d{4})', 'Enter a valid 21-character CIN (or LLPIN like AAB-1234).'),
    'udyam': (r'UDYAM-[A-Z]{2}-\d{2}-\d{7}', 'Enter the Udyam number as UDYAM-MP-12-1234567.'),
    'iec': (r'[A-Z0-9]{10}', 'IEC is 10 letters/digits.'),
    'bank_ifsc': (r'[A-Z]{4}0[A-Z0-9]{6}', 'Enter a valid 11-character IFSC, e.g. SBIN0001234.'),
    'pincode': (r'[1-9]\d{5}', 'Enter a 6-digit PIN code.'),
    'bank_account_number': (r'\d{9,18}', 'Account number must be 9–18 digits.'),
}
PHONE_RE = r'\+?[0-9][0-9 \-]{8,17}[0-9]'


class OrganizationForm(StyledForm):
    logo_file = forms.FileField(label='Full logo', required=False, help_text='PNG, JPG or WebP, up to 1 MB. Wide logo for the sign-in page and phone header.',
                                widget=forms.FileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}))
    icon_file = forms.FileField(label='Logo icon', required=False, help_text='Square PNG, JPG or WebP, up to 1 MB. Shown in the sidebar and browser tab.',
                                widget=forms.FileInput(attrs={'accept': 'image/png,image/jpeg,image/webp'}))
    remove_logo = forms.BooleanField(label='Remove uploaded full logo', required=False)
    remove_icon = forms.BooleanField(label='Remove uploaded icon', required=False)

    SECTIONS = [
        ('Company identity', 'How the company appears across the ERP and on documents.',
         ['legal_name', 'trade_name', 'constitution', 'incorporation_date', 'display_name', 'subtitle', 'tagline']),
        ('Registered office', 'Registered address used on invoices and statutory documents.',
         ['address', 'city', 'district', 'state', 'pincode']),
        ('Tax & statutory registrations', 'GSTIN is checked against the PAN and the registered state.',
         ['gstin', 'pan', 'tan', 'cin', 'udyam', 'iec', 'gst_registration_type', 'einvoice_applicable', 'eway_bill_applicable']),
        ('Business & licences', 'Product lines, BIS certification, financial year and operating defaults.',
         ['deals_cement', 'deals_tmt', 'bis_cement_licence', 'bis_tmt_licence', 'fy_start_month', 'default_bag_weight']),
        ('Bank details', 'Printed on invoices for customer payments.',
         ['bank_name', 'bank_branch', 'bank_account_name', 'bank_account_number', 'bank_ifsc']),
        ('Contact & authorised signatory', 'Company contact points and the person who signs documents.',
         ['phone', 'alt_phone', 'customer_care', 'whatsapp', 'email', 'website', 'signatory_name', 'signatory_designation']),
        ('Printing on documents', 'Default text printed on sales orders and invoices.',
         ['terms_conditions', 'document_footer']),
        ('Purchase controls', 'Approval limit for purchase orders and the tolerance for matching supplier invoices.',
         ['po_approval_limit', 'match_qty_tolerance_pct', 'match_price_tolerance_pct']),
    ]

    class Meta:
        model = Organization
        fields = ['legal_name', 'trade_name', 'constitution', 'incorporation_date', 'display_name', 'subtitle', 'tagline',
                  'address', 'city', 'district', 'state', 'pincode',
                  'gstin', 'pan', 'tan', 'cin', 'udyam', 'iec', 'gst_registration_type', 'einvoice_applicable', 'eway_bill_applicable',
                  'deals_cement', 'deals_tmt', 'bis_cement_licence', 'bis_tmt_licence', 'fy_start_month', 'default_bag_weight',
                  'bank_name', 'bank_branch', 'bank_account_name', 'bank_account_number', 'bank_ifsc',
                  'phone', 'alt_phone', 'customer_care', 'whatsapp', 'email', 'website', 'signatory_name', 'signatory_designation',
                  'terms_conditions', 'document_footer', 'po_approval_limit', 'match_qty_tolerance_pct', 'match_price_tolerance_pct']
        widgets = {'incorporation_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'address': forms.Textarea(attrs={'rows': 3}), 'terms_conditions': forms.Textarea(attrs={'rows': 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ['po_approval_limit', 'match_qty_tolerance_pct', 'match_price_tolerance_pct']:
            self.fields[name].required = False
        for name in ['gstin', 'pan', 'tan', 'cin', 'udyam', 'iec', 'bank_ifsc']:
            self.fields[name].widget.attrs.update(style='text-transform:uppercase', autocomplete='off', spellcheck='false')
        self.fields['pincode'].widget.attrs.update(inputmode='numeric', maxlength='6')
        self.fields['bank_account_number'].widget.attrs.update(inputmode='numeric', autocomplete='off')
        for name in ['phone', 'alt_phone', 'customer_care', 'whatsapp']:
            self.fields[name].widget.attrs.update(inputmode='tel', autocomplete='tel')
        self.fields['default_bag_weight'].widget.attrs.update(step='0.5', min='1')

    def sections(self):
        from django.utils.text import slugify
        return [(title, note, [self[name] for name in names], slugify(title)) for title, note, names in self.SECTIONS]

    def _keep(self, name):
        value = self.cleaned_data.get(name)
        return getattr(self.instance, name) if value is None else value

    def clean_po_approval_limit(self): return self._keep('po_approval_limit')
    def clean_match_qty_tolerance_pct(self): return self._keep('match_qty_tolerance_pct')
    def clean_match_price_tolerance_pct(self): return self._keep('match_price_tolerance_pct')

    def clean_logo_file(self):
        upload = self.cleaned_data.get('logo_file')
        return read_image(upload) if upload else None

    def clean_icon_file(self):
        upload = self.cleaned_data.get('icon_file')
        return read_image(upload) if upload else None

    def clean(self):
        data = super().clean()
        for name, (pattern, message) in FORMATS.items():
            value = (data.get(name) or '').strip()
            value = value.upper() if name not in ('pincode', 'bank_account_number') else value.replace(' ', '')
            data[name] = value
            if value and not re.fullmatch(pattern, value):
                self.add_error(name, message)
        for name in ['phone', 'alt_phone', 'customer_care', 'whatsapp']:
            value = (data.get(name) or '').strip()
            data[name] = value
            if value and not re.fullmatch(PHONE_RE, value):
                self.add_error(name, 'Enter a valid phone number, e.g. +91 98765 43210.')
        gstin, pan, state = data.get('gstin'), data.get('pan'), data.get('state')
        if gstin and 'gstin' not in self.errors:
            if pan and 'pan' not in self.errors and gstin[2:12] != pan:
                self.add_error('gstin', 'GSTIN characters 3–12 must match the PAN.')
            if state and gstin[:2] != state:
                self.add_error('gstin', f'GSTIN starts with {gstin[:2]}, but the registered state code is {state}.')
        weight = data.get('default_bag_weight')
        if weight is not None and not (1 <= weight <= 100):
            self.add_error('default_bag_weight', 'Bag weight must be between 1 and 100 kg.')
        if data.get('gst_registration_type') == 'unregistered' and data.get('gstin'):
            self.add_error('gst_registration_type', 'An unregistered business cannot have a GSTIN. Clear the GSTIN or change the type.')
        if not data.get('deals_cement') and not data.get('deals_tmt'):
            self.add_error('deals_tmt', 'Select at least one business line.')
        return data

    def save(self, commit=True):
        org = super().save(commit=False)
        for name in list(FORMATS) + ['phone', 'alt_phone', 'customer_care', 'whatsapp']:
            setattr(org, name, self.cleaned_data.get(name, getattr(org, name)))
        logo, icon = self.cleaned_data.get('logo_file'), self.cleaned_data.get('icon_file')
        if logo:
            org.logo, org.logo_type = logo
        elif self.cleaned_data.get('remove_logo'):
            org.logo, org.logo_type = None, ''
        if icon:
            org.icon, org.icon_type = icon
        elif self.cleaned_data.get('remove_icon'):
            org.icon, org.icon_type = None, ''
        if commit:
            org.save()
        return org


MAX_DOCUMENT_BYTES = 5 * 1024 * 1024


class OrganizationDocumentForm(StyledForm):
    upload = forms.FileField(label='File', help_text='PDF, PNG, JPG or WebP, up to 5 MB.',
                             widget=forms.FileInput(attrs={'accept': 'application/pdf,image/png,image/jpeg,image/webp'}))

    class Meta:
        model = OrganizationDocument
        fields = ['doc_type', 'title', 'number', 'site', 'issued_on', 'valid_until', 'notes']
        widgets = {'issued_on': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'valid_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site'].queryset = Site.objects.filter(active=True).order_by('code')
        self.fields['site'].empty_label = 'Company-wide'
        self.fields['doc_type'].choices = [('', 'Select document type')] + list(self.fields['doc_type'].choices)[1:]
        if self.instance.pk:
            self.fields['upload'].required = False
            self.fields['upload'].label = 'Replace file'
            self.fields['upload'].help_text = 'Optional. Leave empty to keep the current file.'

    def clean_upload(self):
        upload = self.cleaned_data.get('upload')
        if not upload:
            return None
        if upload.size > MAX_DOCUMENT_BYTES:
            raise forms.ValidationError('File must be 5 MB or smaller.')
        head = upload.read(16); upload.seek(0)
        if head.startswith(b'%PDF-'):
            kind = 'application/pdf'
        elif head.startswith(b'\x89PNG\r\n\x1a\n'):
            kind = 'image/png'
        elif head.startswith(b'\xff\xd8\xff'):
            kind = 'image/jpeg'
        elif head[:4] == b'RIFF' and head[8:12] == b'WEBP':
            kind = 'image/webp'
        else:
            raise forms.ValidationError('Upload a PDF, PNG, JPG or WebP file.')
        name = re.sub(r'[^A-Za-z0-9._ -]', '_', upload.name)[-150:] or 'document'
        return upload.read(), kind, name

    def clean(self):
        data = super().clean()
        issued, until = data.get('issued_on'), data.get('valid_until')
        if issued and until and until < issued:
            self.add_error('valid_until', 'Validity cannot end before the issue date.')
        return data

    def save(self, commit=True):
        doc = super().save(commit=False)
        if self.cleaned_data.get('upload'):
            doc.file, doc.content_type, doc.file_name = self.cleaned_data['upload']
            doc.size = len(doc.file)
        if commit:
            doc.save()
        return doc


class PartnerDocumentForm(StyledForm):
    upload = forms.FileField(label='File', help_text='PDF, PNG, JPG or WebP, up to 5 MB.',
                             widget=forms.FileInput(attrs={'accept': 'application/pdf,image/png,image/jpeg,image/webp'}))

    class Meta:
        from .models import PartnerDocument
        model = PartnerDocument
        fields = ['doc_type', 'number', 'valid_until']
        widgets = {'valid_until': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['doc_type'].choices = [('', 'Select document type')] + list(self.fields['doc_type'].choices)[1:]

    clean_upload = OrganizationDocumentForm.clean_upload

    def save(self, commit=True):
        doc = super().save(commit=False)
        doc.file, doc.content_type, doc.file_name = self.cleaned_data['upload']
        doc.size = len(doc.file)
        if commit:
            doc.save()
        return doc


# ---------- Masons & contractors ----------

def normalise_mobile(value):
    """'+91 98765-43210', '098765 43210' → '9876543210'. Returns '' when it is not a valid Indian mobile."""
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 12 and digits.startswith('91'): digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'): digits = digits[1:]
    return digits if re.fullmatch(r'[6-9]\d{9}', digits) else ''


class InfluencerForm(StyledForm):
    SECTIONS = [
        ('Basics', 'Who this person is.', ['kind', 'name', 'firm_name', 'mobile', 'alt_mobile', 'whatsapp', 'active']),
        ('Area', 'Where they work. District drives the district-wise view.', ['state', 'district', 'tehsil', 'village', 'pincode', 'address']),
        ('Dealers', 'Dealers they buy through or recommend.', ['primary_dealer', 'dealers']),
        ('Work & volume', 'Typical work and monthly consumption decide the loyalty tier.', ['specialisation', 'experience_years', 'team_size', 'monthly_cement_bags', 'monthly_tmt_mt', 'brand_preference']),
        ('KYC & payouts', 'Needed before scheme rewards are paid.', ['aadhaar_last4', 'pan', 'upi_id', 'date_of_birth', 'kyc_verified']),
        ('Notes', 'Anything the field team should know.', ['notes']),
    ]
    WIDE = {'name', 'address', 'notes', 'dealers'}

    class Meta:
        model = Influencer
        fields = ['kind', 'name', 'firm_name', 'mobile', 'alt_mobile', 'whatsapp', 'active', 'state', 'district', 'tehsil', 'village', 'pincode', 'address',
                  'primary_dealer', 'dealers', 'specialisation', 'experience_years', 'team_size', 'monthly_cement_bags', 'monthly_tmt_mt', 'brand_preference',
                  'aadhaar_last4', 'pan', 'upi_id', 'date_of_birth', 'kyc_verified', 'notes']
        widgets = {'address': forms.Textarea(attrs={'rows': 2}), 'notes': forms.Textarea(attrs={'rows': 3}), 'date_of_birth': forms.DateInput(attrs={'type': 'date'}),
                   'dealers': forms.SelectMultiple(attrs={'size': 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        customers = Partner.objects.filter(kind__in=CUSTOMER_KINDS).filter(models_active_or(self.instance)).distinct().order_by('name')
        self.fields['primary_dealer'].queryset = customers
        self.fields['dealers'].queryset = customers
        self.fields['primary_dealer'].empty_label = 'No dealer yet'
        self.fields['dealers'].widget.attrs['class'] = 'form-select'
        self.fields['dealers'].help_text = 'Hold Ctrl / ⌘ to pick more than one.'
        self.fields['kind'].choices = [('', 'Select type')] + list(self.fields['kind'].choices)[1:]
        self.fields['district'].widget.attrs.update(list='district-options', autocomplete='off', placeholder='e.g. Bhopal')
        for name in ['mobile', 'alt_mobile']:
            self.fields[name] = forms.CharField(label=self.fields[name].label, required=name == 'mobile', help_text=self.fields[name].help_text,
                                                widget=forms.TextInput(attrs={'class': 'form-control', 'inputmode': 'tel', 'placeholder': 'e.g. 98765 43210'}))
        self.fields['pincode'].widget.attrs.update(inputmode='numeric', maxlength='6')
        self.fields['aadhaar_last4'].widget.attrs.update(inputmode='numeric', maxlength='4', placeholder='e.g. 4821')
        self.fields['pan'].widget.attrs.update(style='text-transform:uppercase', autocomplete='off')
        self.fields['monthly_tmt_mt'].widget.attrs.update(step='any')
        self.duplicate = None

    def sections(self):
        return [(title, note, [self[name] for name in names]) for title, note, names in self.SECTIONS]

    def clean_mobile(self):
        mobile = normalise_mobile(self.cleaned_data['mobile'])
        if not mobile:
            raise forms.ValidationError('Enter a valid 10-digit Indian mobile number.')
        existing = Influencer.objects.filter(mobile=mobile).exclude(pk=self.instance.pk).first()
        if existing:
            self.duplicate = existing
            raise forms.ValidationError(f'This mobile is already registered to {existing.name} ({existing.code}).')
        return mobile

    def clean_alt_mobile(self):
        raw = self.cleaned_data.get('alt_mobile', '').strip()
        if not raw:
            return ''
        mobile = normalise_mobile(raw)
        if not mobile:
            raise forms.ValidationError('Enter a valid 10-digit Indian mobile number.')
        return mobile

    def clean(self):
        data = super().clean()
        checks = {'pincode': (r'[1-9]\d{5}', 'Enter a 6-digit PIN code.'), 'aadhaar_last4': (r'\d{4}', 'Enter only the last 4 digits.'),
                  'pan': (r'[A-Z]{5}\d{4}[A-Z]', 'Enter a valid 10-character PAN, e.g. ABCDE1234F.'),
                  'upi_id': (r'[\w.\-]{2,}@[A-Za-z]{2,}', 'Enter a valid UPI ID, e.g. name@okaxis.')}
        for name, (pattern, message) in checks.items():
            value = (data.get(name) or '').strip()
            value = value.upper() if name == 'pan' else value.replace(' ', '')
            data[name] = value
            if value and not re.fullmatch(pattern, value):
                self.add_error(name, message)
        if data.get('alt_mobile') and data.get('alt_mobile') == data.get('mobile'):
            self.add_error('alt_mobile', 'Alternate mobile is the same as the main mobile.')
        if data.get('kyc_verified') and not (data.get('aadhaar_last4') or data.get('pan')):
            self.add_error('kyc_verified', 'Record Aadhaar (last 4) or PAN before marking KYC verified.')
        return data


def models_active_or(instance):
    """Active dealers, plus any inactive dealer already linked to this record (so editing never drops a link)."""
    from django.db.models import Q
    q = Q(active=True)
    if instance and instance.pk:
        q |= Q(pk=instance.primary_dealer_id) | Q(influencers=instance)
    return q


class InfluencerActivityForm(StyledForm):
    class Meta:
        model = InfluencerActivity
        fields = ['kind', 'date', 'title', 'location', 'dealer', 'expected_bags', 'expected_tmt_mt', 'status', 'follow_up_on', 'notes']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'}), 'follow_up_on': forms.DateInput(attrs={'type': 'date'}), 'notes': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, influencer=None, **kwargs):
        super().__init__(*args, **kwargs)
        dealers = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).order_by('name')
        if influencer:
            linked = [d.pk for d in influencer.all_dealers()]
            if linked:
                dealers = Partner.objects.filter(pk__in=linked).order_by('name') | dealers
        self.fields['dealer'].queryset = dealers.distinct()
        self.fields['dealer'].empty_label = 'Not through a dealer'
        self.fields['kind'].choices = [('', 'Choose activity')] + list(self.fields['kind'].choices)[1:]
        self.fields['status'].choices = [('', 'Not a lead')] + list(self.fields['status'].choices)[1:]
        self.fields['expected_tmt_mt'].widget.attrs.update(step='any')
        self.fields['notes'].widget.attrs['rows'] = 2

    def clean(self):
        data = super().clean()
        if data.get('kind') in LEAD_KINDS and not data.get('status'):
            data['status'] = 'open'
        elif data.get('kind') and data.get('kind') not in LEAD_KINDS:
            data['status'] = ''
        if data.get('kind') in LEAD_KINDS and not (data.get('title') or '').strip():
            self.add_error('title', 'Name the site or project, e.g. "G+2 house, Kolar Road".')
        if data.get('follow_up_on') and data.get('date') and data['follow_up_on'] < data['date']:
            self.add_error('follow_up_on', 'Follow-up date cannot be before the activity date.')
        return data


class InfluencerImportForm(forms.Form):
    file = forms.FileField(label='CSV file', help_text='Save the Excel sheet as "CSV UTF-8". Max 2 MB, 2,000 rows.',
                           widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.csv,text/csv'}))

    def clean_file(self):
        f = self.cleaned_data['file']
        if f.size > 2 * 1024 * 1024:
            raise forms.ValidationError('File is larger than 2 MB. Split it into smaller files.')
        if not f.name.lower().endswith('.csv'):
            raise forms.ValidationError('Upload a .csv file. In Excel use File → Save As → CSV UTF-8.')
        return f


# ---------- Price list ----------

class PriceRuleForm(StyledForm):
    class Meta:
        model = PriceRule
        fields = ['product', 'rate', 'min_quantity', 'site', 'district', 'partner_kind', 'partner', 'valid_from', 'valid_to', 'active', 'notes']
        widgets = {'valid_from': forms.DateInput(attrs={'type': 'date'}), 'valid_to': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(active=True, category__in=['Cement', 'TMT']).order_by('category', 'name')
        self.fields['site'].queryset = Site.objects.filter(active=True).order_by('code')
        self.fields['site'].empty_label = 'All plants & depots'
        self.fields['partner'].queryset = Partner.objects.filter(active=True, kind__in=CUSTOMER_KINDS).order_by('name')
        self.fields['partner'].empty_label = 'Any buyer'
        self.fields['partner_kind'].choices = [('', 'Any buyer type')] + list(self.fields['partner_kind'].choices)[1:]
        self.fields['district'].widget.attrs.update(list='district-options', autocomplete='off', placeholder='Any district')
        self.fields['rate'].widget.attrs.update(step='0.01')
        self.fields['min_quantity'].widget.attrs.update(step='any')
        self.revising = None  # the older version being replaced; it ends the day before the new one starts

    def clean(self):
        data = super().clean()
        data['district'] = clean_place(data.get('district'))
        vf, vt = data.get('valid_from'), data.get('valid_to')
        if vf and vt and vt < vf:
            self.add_error('valid_to', 'End date cannot be before the start date.')
        if data.get('partner') and data.get('partner_kind') and data['partner'].kind != data['partner_kind']:
            self.add_error('partner_kind', f"{data['partner'].name} is a {data['partner'].get_kind_display()} — leave buyer type empty or match it.")
        if data.get('product') and vf and data.get('active', True) and not self.errors:
            clash = PriceRule.objects.filter(product=data['product'], site=data.get('site'), district__iexact=data['district'], partner_kind=data.get('partner_kind') or '',
                                             partner=data.get('partner'), min_quantity=data.get('min_quantity') or 0, active=True).exclude(pk=self.instance.pk)
            if self.revising:
                clash = clash.exclude(pk=self.revising.pk)
            clash = clash.filter(models_q_overlap(vf, vt))
            if clash.exists():
                c = clash.first()
                raise forms.ValidationError(f'A price for the same product, scope and slab already covers these dates (₹{c.rate} from {c.valid_from:%d %b %Y}). Revise that price instead.')
        return data


def models_q_overlap(start, end):
    from django.db.models import Q
    q = Q(valid_to__isnull=True) | Q(valid_to__gte=start)
    if end:
        q &= Q(valid_from__lte=end)
    return q
