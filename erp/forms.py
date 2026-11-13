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
    class Meta:
        model = Site
        fields = ['code', 'name', 'kind', 'address', 'active']

class ProductForm(StyledForm):
    class Meta:
        model = Product
        fields = ['code', 'name', 'category', 'unit', 'bag_weight_kg', 'hsn', 'reorder_level', 'active']
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.is_bound:
            # New products start from the organisation's standard cement bag weight.
            weight = Organization.objects.filter(pk=1).values_list('default_bag_weight', flat=True).first()
            self.initial.setdefault('bag_weight_kg', weight or 50)
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
