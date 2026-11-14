from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory
import re
from .models import Site, Product, Partner, Order, OrderLine, Production, QualityTest, Organization, OrganizationDocument

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
        ('Capacity & notes', 'Installed capacity and anything the team should know.', ['capacity_tpa', 'notes']),
    ]

    class Meta:
        model = Site
        fields = ['kind', 'facility_type', 'code', 'name', 'active', 'address', 'city', 'district', 'state', 'pincode',
                  'gstin', 'manager', 'phone', 'email', 'capacity_tpa', 'notes']
        widgets = {'address': forms.Textarea(attrs={'rows': 2}), 'notes': forms.Textarea(attrs={'rows': 3})}

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
        ('Stock & tax', 'Unit for stock and orders, reorder alert, HSN and GST.', ['unit', 'reorder_level', 'hsn', 'gst_rate']),
        ('Notes', 'Optional description for the team.', ['description']),
    ]
    # Which category each specification field belongs to (used by the form's JavaScript and validation).
    FIELD_SCOPE = {'bag_weight_kg': 'Cement', 'bar_diameter_mm': 'TMT', 'weight_per_metre_kg': 'TMT', 'bar_length_m': 'TMT', 'bars_per_bundle': 'TMT'}

    class Meta:
        model = Product
        fields = ['category', 'code', 'name', 'brand', 'active', 'grade', 'standard', 'bag_weight_kg', 'bar_diameter_mm',
                  'weight_per_metre_kg', 'bar_length_m', 'bars_per_bundle', 'unit', 'reorder_level', 'hsn', 'gst_rate', 'description']
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
        for name in ['bag_weight_kg', 'weight_per_metre_kg', 'bar_length_m', 'reorder_level']:
            self.fields[name].widget.attrs['step'] = 'any'
        if not self.instance.pk and not self.is_bound:
            # New products start from the organisation's standard cement bag weight.
            weight = Organization.objects.filter(pk=1).values_list('default_bag_weight', flat=True).first()
            self.initial.setdefault('bag_weight_kg', weight or 50)
            self.initial.setdefault('bar_length_m', 12)

    def sections(self):
        return [(title, note, [self[name] for name in names]) for title, note, names in self.SECTIONS]

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
        fields = ['site', 'partner', 'due_date', 'notes']
        widgets = {'due_date': forms.DateInput(attrs={'type': 'date'})}

class LineForm(StyledForm):
    class Meta:
        model = OrderLine
        fields = ['product', 'quantity', 'rate']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(active=True).order_by('category', 'name')  # inactive SKUs stay off new orders

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
    ]

    class Meta:
        model = Organization
        fields = ['legal_name', 'trade_name', 'constitution', 'incorporation_date', 'display_name', 'subtitle', 'tagline',
                  'address', 'city', 'district', 'state', 'pincode',
                  'gstin', 'pan', 'tan', 'cin', 'udyam', 'iec', 'gst_registration_type', 'einvoice_applicable', 'eway_bill_applicable',
                  'deals_cement', 'deals_tmt', 'bis_cement_licence', 'bis_tmt_licence', 'fy_start_month', 'default_bag_weight',
                  'bank_name', 'bank_branch', 'bank_account_name', 'bank_account_number', 'bank_ifsc',
                  'phone', 'alt_phone', 'customer_care', 'whatsapp', 'email', 'website', 'signatory_name', 'signatory_designation',
                  'terms_conditions', 'document_footer']
        widgets = {'incorporation_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
                   'address': forms.Textarea(attrs={'rows': 3}), 'terms_conditions': forms.Textarea(attrs={'rows': 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
