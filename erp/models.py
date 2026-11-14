import uuid
from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
POSITIVE = [MinValueValidator(Decimal('0.001'))]
MONEY = [MinValueValidator(Decimal('0'))]
def document_number():
    return timezone.localdate().strftime('%y%m%d') + '-' + uuid.uuid4().hex[:8].upper()

INDIAN_STATES = [
    ('01', 'Jammu and Kashmir'), ('02', 'Himachal Pradesh'), ('03', 'Punjab'), ('04', 'Chandigarh'), ('05', 'Uttarakhand'),
    ('06', 'Haryana'), ('07', 'Delhi'), ('08', 'Rajasthan'), ('09', 'Uttar Pradesh'), ('10', 'Bihar'), ('11', 'Sikkim'),
    ('12', 'Arunachal Pradesh'), ('13', 'Nagaland'), ('14', 'Manipur'), ('15', 'Mizoram'), ('16', 'Tripura'), ('17', 'Meghalaya'),
    ('18', 'Assam'), ('19', 'West Bengal'), ('20', 'Jharkhand'), ('21', 'Odisha'), ('22', 'Chhattisgarh'), ('23', 'Madhya Pradesh'),
    ('24', 'Gujarat'), ('26', 'Dadra and Nagar Haveli and Daman and Diu'), ('27', 'Maharashtra'), ('29', 'Karnataka'), ('30', 'Goa'),
    ('31', 'Lakshadweep'), ('32', 'Kerala'), ('33', 'Tamil Nadu'), ('34', 'Puducherry'), ('35', 'Andaman and Nicobar Islands'),
    ('36', 'Telangana'), ('37', 'Andhra Pradesh'), ('38', 'Ladakh'), ('97', 'Other Territory'),
]
FACILITY_TYPES = [
    ('Plants', [('integrated_cement', 'Integrated cement plant'), ('cement_grinding', 'Cement grinding unit'), ('packing_plant', 'Packing plant'),
                ('tmt_rolling', 'TMT rolling mill'), ('steel_melting', 'Steel melting shop')]),
    ('Depots & warehouses', [('depot', 'Depot / godown'), ('warehouse', 'Warehouse / stockyard')]),
]
PLANT_FACILITIES = {'integrated_cement', 'cement_grinding', 'packing_plant', 'tmt_rolling', 'steel_melting'}


class Site(models.Model):
    code = models.CharField('Location code', max_length=20, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField('Location type', max_length=15, choices=[('Plant', 'Plant'), ('Depot', 'Depot'), ('Warehouse', 'Warehouse')])
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    # Plant / depot profile
    facility_type = models.CharField('Facility', max_length=20, blank=True, choices=FACILITY_TYPES)
    city = models.CharField(max_length=80, blank=True)
    district = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=2, blank=True, choices=INDIAN_STATES)
    pincode = models.CharField('PIN code', max_length=6, blank=True)
    gstin = models.CharField('Plant GSTIN', max_length=15, blank=True, help_text='Each state registration has its own GSTIN with the company PAN inside it.')
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    manager = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='managed_sites',
                                verbose_name='Plant / depot manager')
    capacity_tpa = models.DecimalField('Installed capacity (MT per year)', max_digits=12, decimal_places=2, null=True, blank=True, validators=MONEY)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self): return f'{self.code} · {self.name}'

    @property
    def full_address(self):
        parts = [self.address.strip(), self.city, self.district, f'{self.get_state_display()} {self.pincode}'.strip() if (self.state or self.pincode) else '']
        return ', '.join(p for p in parts if p)

class SiteAccess(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'site'], name='unique_site_access')]

PRODUCT_CATEGORIES = [('Cement', 'Finished cement'), ('TMT', 'TMT bars'), ('Raw material', 'Raw material'),
                      ('Packaging', 'Packaging'), ('Fuel', 'Fuel'), ('Spares', 'Spares')]
CEMENT_GRADES = [('OPC 33', 'OPC 33'), ('OPC 43', 'OPC 43'), ('OPC 53', 'OPC 53'), ('PPC', 'PPC (fly ash)'), ('PSC', 'PSC (slag)'), ('PCC', 'Composite (PCC)')]
TMT_GRADES = [('Fe 415', 'Fe 415'), ('Fe 415D', 'Fe 415D'), ('Fe 500', 'Fe 500'), ('Fe 500D', 'Fe 500D'),
              ('Fe 550', 'Fe 550'), ('Fe 550D', 'Fe 550D'), ('Fe 600', 'Fe 600')]
GRADE_STANDARDS = {'OPC 33': 'IS 269:2015', 'OPC 43': 'IS 269:2015', 'OPC 53': 'IS 269:2015', 'PPC': 'IS 1489 (Part 1):2015',
                   'PSC': 'IS 455:2015', 'PCC': 'IS 16415:2015', **{g: 'IS 1786:2008' for g, _ in TMT_GRADES}}
BAR_SIZES = [(d, f'{d} mm') for d in (8, 10, 12, 16, 20, 25, 28, 32)]
GST_RATES = [(Decimal('0'), 'Nil (0%)'), (Decimal('5'), '5%'), (Decimal('12'), '12%'), (Decimal('18'), '18%'), (Decimal('28'), '28%'), (Decimal('40'), '40%')]
DEFAULT_HSN = {'Cement': '2523', 'TMT': '7214'}


class Product(models.Model):
    code = models.CharField('SKU code', max_length=30, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=25, choices=PRODUCT_CATEGORIES)
    unit = models.CharField('Base unit', max_length=12, choices=[('MT', 'Metric tonnes'), ('BAG', 'Bags'), ('KG', 'Kilograms'), ('PCS', 'Pieces')])
    bag_weight_kg = models.DecimalField('Bag weight (kg)', max_digits=8, decimal_places=3, null=True, blank=True, validators=POSITIVE)
    hsn = models.CharField('HSN code', max_length=10, blank=True)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    active = models.BooleanField(default=True)
    # Cement & TMT specification
    brand = models.CharField(max_length=60, blank=True, help_text='Brand printed on bags or bundles, if any.')
    grade = models.CharField(max_length=10, blank=True, choices=CEMENT_GRADES + TMT_GRADES)
    standard = models.CharField('IS standard', max_length=40, blank=True, help_text='Filled from the grade when left empty.')
    bar_diameter_mm = models.PositiveSmallIntegerField('Bar size', null=True, blank=True, choices=BAR_SIZES)
    weight_per_metre_kg = models.DecimalField('Weight per metre (kg)', max_digits=7, decimal_places=3, null=True, blank=True, validators=POSITIVE,
                                              help_text='Leave empty to use the theoretical weight d²/162.')
    bar_length_m = models.DecimalField('Bar length (m)', max_digits=5, decimal_places=2, null=True, blank=True, validators=POSITIVE)
    bars_per_bundle = models.PositiveSmallIntegerField('Bars per bundle', null=True, blank=True)
    gst_rate = models.DecimalField('GST rate', max_digits=5, decimal_places=2, null=True, blank=True, choices=GST_RATES,
                                   help_text='Confirm the current rate with your accountant.')
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self): return f'{self.code} · {self.name} ({self.unit})'

    @property
    def is_finished(self):
        return self.category in ('Cement', 'TMT')

    @property
    def spec_line(self):
        """Short human description, e.g. 'OPC 53 · 50 kg bag' or 'Fe 500D · 12 mm · 0.889 kg/m'."""
        parts = [self.grade] if self.grade else []
        if self.category == 'TMT':
            if self.bar_diameter_mm: parts.append(f'{self.bar_diameter_mm} mm')
            if self.weight_per_metre_kg: parts.append(f'{self.weight_per_metre_kg.normalize():f} kg/m')
        elif self.bag_weight_kg:
            parts.append(f'{self.bag_weight_kg.normalize():f} kg bag')
        return ' · '.join(parts)

    @property
    def bundle_weight_kg(self):
        if self.weight_per_metre_kg and self.bar_length_m and self.bars_per_bundle:
            return (self.weight_per_metre_kg * self.bar_length_m * self.bars_per_bundle).quantize(Decimal('0.01'))
        return None

PARTNER_KINDS = [('Dealer', 'Dealer'), ('Distributor', 'Distributor'), ('Retailer', 'Retailer'), ('Institutional', 'Institutional / project buyer'),
                 ('Supplier', 'Supplier'), ('Transporter', 'Transporter')]
CUSTOMER_KINDS = ['Dealer', 'Distributor', 'Retailer', 'Institutional']


class Partner(models.Model):
    code = models.CharField('Partner code', max_length=25, unique=True)
    name = models.CharField('Business name', max_length=120)
    kind = models.CharField('Partner type', max_length=15, choices=PARTNER_KINDS)
    gstin = models.CharField('GSTIN', max_length=15, blank=True)
    phone = models.CharField(max_length=25, blank=True)
    territory = models.CharField(max_length=100, blank=True, help_text='Sales area, e.g. Bhopal city or Rewa district.')
    address = models.TextField('Billing address', blank=True)
    credit_limit = models.DecimalField('Credit limit (₹)', max_digits=16, decimal_places=2, default=0, validators=MONEY,
                                       help_text='Approved and completed sales orders above this limit are blocked. 0 means advance payment only.')
    active = models.BooleanField(default=True)
    # Contact & registration
    contact_person = models.CharField(max_length=100, blank=True)
    alt_phone = models.CharField('Alternate phone', max_length=25, blank=True)
    email = models.EmailField(blank=True)
    pan = models.CharField('PAN', max_length=10, blank=True)
    city = models.CharField(max_length=80, blank=True)
    district = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=2, blank=True, choices=INDIAN_STATES)
    pincode = models.CharField('PIN code', max_length=6, blank=True)
    shipping_address = models.TextField('Delivery address', blank=True, help_text='Leave empty when it is the same as the billing address.')
    # Commercial
    payment_terms_days = models.PositiveSmallIntegerField('Payment terms (days)', default=0, help_text='0 means advance / cash on delivery.')
    security_deposit = models.DecimalField('Security deposit (₹)', max_digits=14, decimal_places=2, default=0, validators=MONEY)
    # Bank (for paying suppliers and transporters)
    bank_name = models.CharField(max_length=100, blank=True)
    bank_account_number = models.CharField('Account number', max_length=18, blank=True)
    bank_ifsc = models.CharField('IFSC', max_length=11, blank=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self): return self.name

    @property
    def is_customer(self):
        return self.kind in CUSTOMER_KINDS

    @property
    def full_address(self):
        parts = [self.address.strip(), self.city, self.district, f'{self.get_state_display()} {self.pincode}'.strip() if (self.state or self.pincode) else '']
        return ', '.join(p for p in parts if p)

    @property
    def masked_account(self):
        n = self.bank_account_number
        return ('•' * max(len(n) - 4, 0) + n[-4:]) if n else ''


PARTNER_DOCUMENT_TYPES = [('gst_certificate', 'GST certificate'), ('pan_card', 'PAN card'), ('cancelled_cheque', 'Cancelled cheque'),
                          ('dealership_agreement', 'Dealership / supply agreement'), ('security_deposit', 'Security deposit receipt'),
                          ('vehicle_rc', 'Vehicle RC / permit'), ('aadhaar', 'Proprietor ID proof'), ('other', 'Other document')]


class PartnerDocument(models.Model):
    """KYC and agreement files for a dealer, supplier or transporter (PDF/PNG/JPEG/WebP, max 5 MB)."""
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='documents')
    doc_type = models.CharField('Document type', max_length=30, choices=PARTNER_DOCUMENT_TYPES)
    number = models.CharField('Number / reference', max_length=60, blank=True)
    valid_until = models.DateField('Valid until', null=True, blank=True)
    file = models.BinaryField(editable=False)
    file_name = models.CharField(max_length=150, editable=False)
    content_type = models.CharField(max_length=40, editable=False)
    size = models.PositiveIntegerField(editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['doc_type', '-uploaded_at']

    @property
    def status(self):
        if not self.valid_until:
            return 'none', None
        days = (self.valid_until - timezone.localdate()).days
        return ('expired' if days < 0 else 'expiring' if days <= 30 else 'valid'), days

class Document(models.Model):
    number = models.CharField(max_length=25, default=document_number, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    class Meta:
        abstract = True
        ordering = ['-created_at']

class Order(Document):
    kind = models.CharField(max_length=10, choices=[('Sale', 'Sales order'), ('Purchase', 'Purchase order')])
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT)
    due_date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=15, default='Draft', choices=[(x, x) for x in ['Draft', 'Submitted', 'Approved', 'Completed', 'Cancelled']])
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_orders')
    class Meta(Document.Meta):
        permissions = [('approve_order', 'Approve orders (legacy)'), ('post_order', 'Receive or dispatch orders (legacy)')] + [
            (f'{action}_{kind}_order', f'{action.title()} {kind} orders')
            for kind in ['sales', 'purchase']
            for action in ['view', 'create', 'submit', 'approve', 'post', 'cancel']
        ]
    @property
    def total(self): return sum((line.quantity * line.rate for line in self.lines.all()), Decimal('0'))
    def __str__(self): return f'{self.kind} {self.number}'

class OrderLine(models.Model):
    order = models.ForeignKey(Order, related_name='lines', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    rate = models.DecimalField(max_digits=14, decimal_places=2, validators=MONEY)
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='order_quantity_positive'), models.CheckConstraint(condition=models.Q(rate__gte=0), name='order_rate_nonnegative')]
    @property
    def amount(self): return self.quantity * self.rate

class StockBalance(models.Model):
    site = models.ForeignKey(Site, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['site', 'product'], name='unique_stock_balance'), models.CheckConstraint(condition=models.Q(quantity__gte=0), name='stock_nonnegative')]

class StockMovement(Document):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    source = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=20, choices=[('Opening', 'Opening stock'), ('Receipt', 'Purchase receipt'), ('Dispatch', 'Sales dispatch'), ('Production', 'Production output')])
    class Meta(Document.Meta):
        permissions = [('post_opening', 'Post opening stock'), ('export_stockmovement', 'Export stock ledger')]

class Production(Document):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    planned_quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    actual_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    batch = models.CharField(max_length=60, unique=True)
    shift = models.CharField(max_length=10, choices=[('A', 'Shift A'), ('B', 'Shift B'), ('C', 'Shift C')])
    status = models.CharField(max_length=15, default='Planned', choices=[(x, x) for x in ['Planned', 'Quality hold', 'Released']])
    class Meta(Document.Meta):
        permissions = [('complete_production', 'Complete production'), ('release_production', 'Release quality batch')]

class QualityTest(Document):
    production = models.ForeignKey(Production, on_delete=models.PROTECT, related_name='tests')
    test_name = models.CharField(max_length=100)
    result = models.CharField(max_length=100)
    disposition = models.CharField(max_length=10, choices=[('Pass', 'Pass'), ('Fail', 'Fail')])

class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    site = models.ForeignKey(Site, null=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=80)
    reference = models.CharField(max_length=100)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']



CONSTITUTIONS = [
    ('private_limited', 'Private Limited Company'), ('public_limited', 'Public Limited Company'), ('opc', 'One Person Company'),
    ('llp', 'Limited Liability Partnership'), ('partnership', 'Partnership firm'), ('proprietorship', 'Proprietorship'),
]
GST_REGISTRATION_TYPES = [('regular', 'Regular'), ('composition', 'Composition'), ('sez', 'SEZ unit'), ('unregistered', 'Unregistered')]
MONTHS = [(i, m) for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'], 1)]


class Organization(models.Model):
    """Single company profile, statutory details and branding (row pk=1), edited from Organisation setup.

    Logos are stored in the database (small PNG/JPEG/WebP only) and served by erp.views.brand_asset,
    so branding works for every user without media-server configuration. Plant-level details
    (factory address, plant GST registrations) belong to Plants & depots.
    """
    # Identity & branding
    legal_name = models.CharField('Legal name', max_length=150, default='Sistech Cement & TMT Pvt. Ltd.')
    trade_name = models.CharField('Brand / trade name', max_length=100, blank=True, default='Sistech')
    display_name = models.CharField('Sidebar name', max_length=30, default='SISTECH', help_text='Short name shown beside the logo in the sidebar.')
    subtitle = models.CharField('Sidebar subtitle', max_length=60, blank=True, default='Cement & TMT Pvt. Ltd.')
    tagline = models.CharField(max_length=120, blank=True, default='Solid foundation, strong future.')
    logo = models.BinaryField(null=True, blank=True, editable=False)
    logo_type = models.CharField(max_length=20, blank=True, editable=False)
    icon = models.BinaryField(null=True, blank=True, editable=False)
    icon_type = models.CharField(max_length=20, blank=True, editable=False)
    constitution = models.CharField('Company type', max_length=20, choices=CONSTITUTIONS, default='private_limited')
    incorporation_date = models.DateField('Date of incorporation', null=True, blank=True)
    # Registered office
    address = models.TextField('Registered office address', blank=True)
    city = models.CharField(max_length=80, blank=True)
    district = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=2, choices=INDIAN_STATES, blank=True, help_text='Also sets the GST state code.')
    pincode = models.CharField('PIN code', max_length=6, blank=True)
    # Tax & statutory
    gstin = models.CharField('GSTIN', max_length=15, blank=True)
    pan = models.CharField('PAN', max_length=10, blank=True)
    tan = models.CharField('TAN', max_length=10, blank=True)
    cin = models.CharField('CIN / LLPIN', max_length=21, blank=True)
    udyam = models.CharField('Udyam (MSME) registration', max_length=19, blank=True)
    iec = models.CharField('Import Export Code (IEC)', max_length=10, blank=True)
    gst_registration_type = models.CharField('GST registration type', max_length=15, choices=GST_REGISTRATION_TYPES, default='regular')
    einvoice_applicable = models.BooleanField('E-invoicing applicable', default=True, help_text='Turnover above the e-invoice threshold.')
    eway_bill_applicable = models.BooleanField('E-way bill applicable', default=True, help_text='Goods movement above the e-way bill limit.')
    # Business
    deals_cement = models.BooleanField('Cement', default=True)
    deals_tmt = models.BooleanField('TMT bars', default=True)
    bis_cement_licence = models.CharField('BIS licence – cement', max_length=40, blank=True, help_text='e.g. CM/L-1234567 (IS 269 / IS 1489).')
    bis_tmt_licence = models.CharField('BIS licence – TMT', max_length=40, blank=True, help_text='e.g. CM/L-7654321 (IS 1786).')
    fy_start_month = models.PositiveSmallIntegerField('Financial year starts', choices=MONTHS, default=4)
    default_bag_weight = models.DecimalField('Standard cement bag (kg)', max_digits=5, decimal_places=2, default=50, help_text='Used as the default pack weight for new cement products.')
    # Banking (printed on invoices)
    bank_name = models.CharField(max_length=100, blank=True)
    bank_branch = models.CharField('Branch', max_length=100, blank=True)
    bank_account_name = models.CharField('Account holder name', max_length=150, blank=True)
    bank_account_number = models.CharField('Account number', max_length=18, blank=True)
    bank_ifsc = models.CharField('IFSC', max_length=11, blank=True)
    # Contact & signatory
    phone = models.CharField(max_length=20, blank=True)
    alt_phone = models.CharField('Alternate phone', max_length=20, blank=True)
    email = models.EmailField('Company email', blank=True)
    website = models.URLField(blank=True)
    customer_care = models.CharField('Customer care / toll-free', max_length=20, blank=True)
    whatsapp = models.CharField('WhatsApp number', max_length=20, blank=True)
    signatory_name = models.CharField('Authorised signatory', max_length=100, blank=True)
    signatory_designation = models.CharField('Designation', max_length=80, blank=True)
    # Documents
    terms_conditions = models.TextField('Default terms & conditions', blank=True, help_text='Printed on sales orders and invoices.')
    document_footer = models.CharField('Document footer note', max_length=200, blank=True, help_text='e.g. Subject to Bhopal jurisdiction. This is a computer-generated document.')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name = 'organisation'

    def __str__(self):
        return self.legal_name

    def save(self, *args, **kwargs):
        self.pk = 1  # singleton
        super().save(*args, **kwargs)

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def version(self):
        return int(self.updated_at.timestamp()) if self.updated_at else 0

    @property
    def masked_account(self):
        n = self.bank_account_number
        return ('•' * max(len(n) - 4, 0) + n[-4:]) if n else ''

    @property
    def full_address(self):
        parts = [self.address.strip(), self.city, self.district,
                 f'{self.get_state_display()} {self.pincode}'.strip() if (self.state or self.pincode) else '']
        return ', '.join(p for p in parts if p)

    @property
    def fy_label(self):
        months = dict(MONTHS)
        end = 12 if self.fy_start_month == 1 else self.fy_start_month - 1
        return f'{months[self.fy_start_month]} – {months[end]}'

    @property
    def business_lines(self):
        return [label for flag, label in [(self.deals_cement, 'Cement'), (self.deals_tmt, 'TMT bars')] if flag]

    @property
    def completeness(self):
        """Share of key profile fields filled in, for the setup checklist."""
        keys = ['legal_name', 'address', 'city', 'state', 'pincode', 'gstin', 'pan', 'phone', 'email', 'bank_account_number', 'bank_ifsc', 'signatory_name']
        filled = sum(1 for k in keys if getattr(self, k))
        return round(100 * filled / len(keys))


DOCUMENT_TYPES = [
    ('Registrations', [
        ('gst_certificate', 'GST registration certificate'), ('pan_card', 'PAN card'), ('tan', 'TAN allotment letter'),
        ('incorporation', 'Certificate of incorporation'), ('moa_aoa', 'MOA / AOA'), ('udyam', 'Udyam (MSME) certificate'),
        ('iec', 'IEC certificate'), ('trade_licence', 'Trade licence'),
    ]),
    ('Quality & product', [
        ('bis_cement', 'BIS licence – cement'), ('bis_tmt', 'BIS licence – TMT'), ('iso', 'ISO certificate'),
    ]),
    ('Plant & statutory', [
        ('factory_licence', 'Factory licence'), ('pcb_consent', 'Pollution control consent (CTE / CTO)'),
        ('mining_lease', 'Mining lease'), ('explosives_licence', 'Explosives licence'), ('fire_noc', 'Fire NOC'),
        ('legal_metrology', 'Legal metrology / weighbridge certificate'),
    ]),
    ('Finance', [
        ('cancelled_cheque', 'Cancelled cheque / bank letter'), ('insurance', 'Insurance policy'),
    ]),
    ('Other', [('other', 'Other document')]),
]
DOCUMENT_LABELS = {code: label for _, items in DOCUMENT_TYPES for code, label in items}


class OrganizationDocument(models.Model):
    """Company registration, licence and compliance documents with expiry tracking.
    Files (PDF/PNG/JPEG/WebP, max 5 MB) are stored in the database and served only to permitted users."""
    doc_type = models.CharField('Document type', max_length=30, choices=DOCUMENT_TYPES)
    title = models.CharField(max_length=120, blank=True, help_text='Optional. Defaults to the document type.')
    number = models.CharField('Number / reference', max_length=60, blank=True)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Applies to', help_text='Leave blank for company-wide documents.')
    issued_on = models.DateField('Issued on', null=True, blank=True)
    valid_until = models.DateField('Valid until', null=True, blank=True, help_text='Leave blank if it does not expire.')
    notes = models.CharField(max_length=200, blank=True)
    file = models.BinaryField(editable=False)
    file_name = models.CharField(max_length=150, editable=False)
    content_type = models.CharField(max_length=40, editable=False)
    size = models.PositiveIntegerField(editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['valid_until', 'doc_type']
        verbose_name = 'organisation document'

    def __str__(self):
        return self.display_title

    @property
    def display_title(self):
        return self.title or DOCUMENT_LABELS.get(self.doc_type, 'Document')

    @property
    def status(self):
        """('expired'|'expiring'|'valid'|'none', days_left)."""
        from django.utils import timezone
        if not self.valid_until:
            return 'none', None
        days = (self.valid_until - timezone.localdate()).days
        return ('expired' if days < 0 else 'expiring' if days <= 30 else 'valid'), days
