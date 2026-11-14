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
    # Dispatch settings (FR-LOG-008/009)
    uses_weighbridge = models.BooleanField('Gate & weighbridge dispatch', default=False,
                                           help_text='Sales leave through gate-in, tare, loading, gross and gate-out. Off = dispatch posts directly from the order.')
    stock_post_event = models.CharField('Stock reduces at', max_length=14, default='gate_out',
                                        choices=[('load_complete', 'Loading complete'), ('gate_out', 'Gate-out')])
    weight_tolerance_pct = models.DecimalField('Weight tolerance (%)', max_digits=4, decimal_places=2, default=Decimal('0.50'), validators=MONEY,
                                               help_text='Allowed difference between weighbridge net and loaded quantity before supervisor approval.')
    scale_id = models.CharField('Weighbridge scale ID', max_length=30, blank=True, help_text='Printed on weighment slips. Calibration is tracked under assets later.')
    loading_bays = models.CharField('Loading bays', max_length=120, blank=True, help_text='Comma separated, e.g. Bay 1, Bay 2, Bulk 1.')
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self): return f'{self.code} · {self.name}'

    @property
    def bays(self):
        return [b.strip() for b in self.loading_bays.split(',') if b.strip()]

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

FREIGHT_TERMS = [('ex_works', 'Ex-works (buyer collects)'), ('for_destination', 'FOR destination (freight in price)'), ('to_pay', 'Freight to pay (billed separately)')]


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
    status = models.CharField(max_length=15, default='Draft', choices=[(x, x) for x in ['Draft', 'Submitted', 'Approved', 'Part dispatched', 'Completed', 'Cancelled']])
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_orders')
    place_of_supply = models.CharField('Place of supply', max_length=2, blank=True, choices=INDIAN_STATES,
                                       help_text="Buyer's delivery state. Same state as the plant → CGST + SGST, otherwise IGST.")
    ship_to = models.TextField('Ship-to address', blank=True, help_text="Leave empty to deliver to the partner's delivery / billing address.")
    freight_terms = models.CharField(max_length=16, choices=FREIGHT_TERMS, default='ex_works')
    customer_ref = models.CharField('Customer PO / reference', max_length=40, blank=True)
    class Meta(Document.Meta):
        permissions = [('approve_order', 'Approve orders (legacy)'), ('post_order', 'Receive or dispatch orders (legacy)')] + [
            (f'{action}_{kind}_order', f'{action.title()} {kind} orders')
            for kind in ['sales', 'purchase']
            for action in ['view', 'create', 'submit', 'approve', 'post', 'cancel']
        ]
    @property
    def total(self):
        """Taxable value (quantity × rate), before GST."""
        return sum((line.quantity * line.rate for line in self.lines.all()), Decimal('0'))

    @property
    def seller_state(self):
        if self.kind == 'Purchase':
            return self.partner.state
        return self.site.state or (Organization.objects.filter(pk=1).values_list('state', flat=True).first() or '')

    @property
    def supply_state(self):
        if self.kind == 'Purchase':
            return self.site.state or self.place_of_supply
        return self.place_of_supply or self.partner.state or self.site.state

    @property
    def supply_state_name(self):
        code = self.supply_state
        return f'{dict(INDIAN_STATES).get(code, code)} ({code})' if code else ''

    @property
    def is_interstate(self):
        a, b = self.seller_state, self.supply_state
        return bool(a and b and a != b)

    @property
    def tax(self):
        from .gst import summarise
        return summarise(self.lines.all(), self.is_interstate)

    @property
    def grand_total(self):
        return self.tax['grand_total']

    def __str__(self): return f'{self.kind} {self.number}'

class OrderLine(models.Model):
    order = models.ForeignKey(Order, related_name='lines', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    rate = models.DecimalField('Rate (₹, before GST)', max_digits=14, decimal_places=2, validators=MONEY)
    list_rate = models.DecimalField('List price', max_digits=14, decimal_places=2, null=True, blank=True, editable=False)
    price_rule = models.ForeignKey('PriceRule', null=True, blank=True, on_delete=models.PROTECT, related_name='order_lines', editable=False)
    gst_rate = models.DecimalField('GST %', max_digits=5, decimal_places=2, default=0, editable=False)
    hsn = models.CharField('HSN', max_length=10, blank=True, editable=False)
    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='order_quantity_positive'), models.CheckConstraint(condition=models.Q(rate__gte=0), name='order_rate_nonnegative')]
    @property
    def amount(self): return self.quantity * self.rate

    @property
    def below_list(self):
        return self.list_rate is not None and self.rate < self.list_rate

    def dispatched_quantity(self, include_open=False):
        """Quantity that has left (or, with include_open, is planned / on a truck that has not been cancelled)."""
        qs = self.dispatch_lines.exclude(dispatch__status='cancelled')
        if not include_open:
            qs = qs.filter(dispatch__stock_posted=True)
        total = Decimal('0')
        for dl in qs:
            total += dl.loaded_quantity if dl.loaded_quantity is not None else dl.planned_quantity
        return total

    @property
    def pending_quantity(self):
        return max(self.quantity - self.dispatched_quantity(include_open=True), Decimal('0'))

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
    kind = models.CharField(max_length=20, choices=[('Opening', 'Opening stock'), ('Receipt', 'Purchase receipt'), ('Dispatch', 'Sales dispatch'), ('Production', 'Production output'), ('Return', 'Sales return')])
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


# ---------- Masons, contractors & other influencers (SRS §5.1 lead sources, Step 1d) ----------

INFLUENCER_KINDS = [('Mason', 'Mason / mistri'), ('Contractor', 'Contractor'), ('Engineer', 'Site engineer'), ('Architect', 'Architect'), ('Builder', 'Builder / developer')]
INFLUENCER_SPECIALISATIONS = [('', 'Not set'), ('residential', 'Residential houses'), ('commercial', 'Commercial buildings'), ('rcc', 'RCC / slab casting'),
                              ('masonry', 'Brickwork & plaster'), ('flooring', 'Flooring & tiles'), ('infra', 'Roads & infrastructure'), ('industrial', 'Industrial / sheds')]
BRAND_PREFERENCES = [('', 'Not known'), ('sistech', 'Uses Sistech'), ('mixed', 'Sistech + other brands'), ('competitor', 'Other brand only')]
# Monthly cement bags → loyalty tier. Shown on lists; schemes (Step 8) will use the same bands.
INFLUENCER_TIERS = [('Platinum', 1000), ('Gold', 400), ('Silver', 100), ('Bronze', 0)]


def clean_place(value):
    """'  bhopal   CITY ' → 'Bhopal City' so district-wise grouping is not split by typing."""
    return ' '.join(w.capitalize() if w.islower() or w.isupper() else w for w in (value or '').split())


class Influencer(models.Model):
    code = models.CharField(max_length=20, unique=True, editable=False)
    kind = models.CharField('Type', max_length=12, choices=INFLUENCER_KINDS)
    name = models.CharField('Full name', max_length=120)
    firm_name = models.CharField('Firm / team name', max_length=120, blank=True)
    mobile = models.CharField(max_length=10, unique=True, help_text='10-digit mobile. Used to find duplicates.')
    alt_mobile = models.CharField('Alternate mobile', max_length=10, blank=True)
    whatsapp = models.BooleanField('Reachable on WhatsApp', default=True)
    # Location
    state = models.CharField(max_length=2, choices=INDIAN_STATES, default='23')
    district = models.CharField(max_length=80)
    tehsil = models.CharField('Tehsil / block', max_length=80, blank=True)
    village = models.CharField('Village / town', max_length=80, blank=True)
    pincode = models.CharField('PIN code', max_length=6, blank=True)
    address = models.TextField(blank=True)
    # Dealer link
    primary_dealer = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.SET_NULL, related_name='primary_influencers',
                                       verbose_name='Main dealer', help_text='The dealer this person mostly buys or recommends through.')
    dealers = models.ManyToManyField(Partner, blank=True, related_name='influencers', verbose_name='Other dealers')
    # Work profile
    specialisation = models.CharField(max_length=12, choices=INFLUENCER_SPECIALISATIONS, blank=True)
    experience_years = models.PositiveSmallIntegerField('Experience (years)', null=True, blank=True)
    team_size = models.PositiveSmallIntegerField('Team size', null=True, blank=True)
    monthly_cement_bags = models.PositiveIntegerField('Cement per month (bags)', default=0, help_text='Typical consumption across their sites.')
    monthly_tmt_mt = models.DecimalField('TMT per month (MT)', max_digits=8, decimal_places=2, default=0, validators=MONEY)
    brand_preference = models.CharField('Current brand', max_length=10, choices=BRAND_PREFERENCES, blank=True)
    # KYC (only what loyalty payouts need; full Aadhaar is never stored)
    aadhaar_last4 = models.CharField('Aadhaar (last 4 digits)', max_length=4, blank=True)
    pan = models.CharField('PAN', max_length=10, blank=True)
    upi_id = models.CharField('UPI ID', max_length=60, blank=True, help_text='For scheme and reward payouts.')
    date_of_birth = models.DateField('Date of birth', null=True, blank=True)
    kyc_verified = models.BooleanField('KYC verified', default=False)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'mason / contractor'
        verbose_name_plural = 'masons & contractors'
        permissions = [('export_influencer', 'Export masons & contractors')]
        indexes = [models.Index(fields=['district', 'kind'])]

    def __str__(self): return f'{self.name} ({self.code})'

    def save(self, *args, **kwargs):
        self.district, self.tehsil, self.village = clean_place(self.district), clean_place(self.tehsil), clean_place(self.village)
        if not self.code:
            last = Influencer.objects.order_by('-id').values_list('id', flat=True).first() or 0
            self.code = f'INF-{last + 1:05d}'
            while Influencer.objects.filter(code=self.code).exists():
                last += 1
                self.code = f'INF-{last + 1:05d}'
        super().save(*args, **kwargs)

    @property
    def tier(self):
        return next(name for name, floor in INFLUENCER_TIERS if self.monthly_cement_bags >= floor)

    @property
    def place(self):
        return ', '.join(p for p in [self.village, self.tehsil, self.district] if p)

    @property
    def mobile_display(self):
        m = self.mobile
        return f'+91 {m[:5]} {m[5:]}' if len(m) == 10 else m

    def all_dealers(self):
        dealers = list(self.dealers.all())
        if self.primary_dealer and self.primary_dealer not in dealers:
            dealers.insert(0, self.primary_dealer)
        return dealers


ACTIVITY_KINDS = [('meet', 'Mason / contractor meet'), ('visit', 'Site visit'), ('call', 'Phone call'), ('referral', 'Site referral'),
                  ('project', 'Project / construction site'), ('sample', 'Sample / demo'), ('gift', 'Gift / reward given')]
LEAD_KINDS = ('referral', 'project')
ACTIVITY_STATUSES = [('open', 'Open'), ('won', 'Converted'), ('lost', 'Lost')]


class InfluencerActivity(models.Model):
    """Meets, site visits, calls, referrals and projects for one mason/contractor. Referrals and projects carry a lead status."""
    influencer = models.ForeignKey(Influencer, on_delete=models.CASCADE, related_name='activities')
    kind = models.CharField('Activity', max_length=10, choices=ACTIVITY_KINDS)
    date = models.DateField(default=timezone.localdate)
    title = models.CharField('Site / subject', max_length=120, blank=True, help_text='e.g. "G+2 house, Kolar Road" or "Monthly mason meet".')
    location = models.CharField(max_length=120, blank=True)
    dealer = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='Through dealer')
    expected_bags = models.PositiveIntegerField('Expected cement (bags)', default=0)
    expected_tmt_mt = models.DecimalField('Expected TMT (MT)', max_digits=8, decimal_places=2, default=0, validators=MONEY)
    status = models.CharField(max_length=5, choices=ACTIVITY_STATUSES, blank=True)
    follow_up_on = models.DateField('Follow up on', null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name_plural = 'influencer activities'

    @property
    def is_lead(self):
        return self.kind in LEAD_KINDS


# ---------- Number series & price list (Step 2a) ----------

DOC_SERIES = [('SO', 'Sales order'), ('PO', 'Purchase order'), ('INV', 'Tax invoice'), ('RCT', 'Receipt'), ('CN', 'Credit note')]


class NumberSeries(models.Model):
    """Gap-free running numbers per document type, site and financial year (GST: invoice no. ≤ 16 characters, unique per FY)."""
    doc_type = models.CharField(max_length=4, choices=DOC_SERIES)
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.PROTECT)
    fy = models.CharField('Financial year', max_length=7)  # 2026-27
    prefix = models.CharField(max_length=10)
    next_number = models.PositiveIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=5)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['doc_type', 'site', 'fy'], name='unique_number_series')]
        verbose_name_plural = 'number series'

    def __str__(self): return f'{self.get_doc_type_display()} · {self.prefix} · {self.fy}'

    def format(self, n):
        return f'{self.prefix}/{self.fy[2:]}/{n:0{self.padding}d}'


class PriceRule(models.Model):
    """One price-list entry. The most specific active rule wins: dealer > dealer type > district > plant > all; then the highest slab."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='price_rules')
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Plant / depot', help_text='Empty = every plant and depot.')
    district = models.CharField(max_length=80, blank=True, help_text="Buyer's district. Empty = every district.")
    partner_kind = models.CharField('Buyer type', max_length=15, blank=True, choices=[(k, l) for k, l in PARTNER_KINDS if k in CUSTOMER_KINDS],
                                    help_text='Empty = every buyer type.')
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, verbose_name='Specific buyer',
                                help_text='Special price for one dealer. Overrides the other rules.')
    min_quantity = models.DecimalField('From quantity', max_digits=14, decimal_places=3, default=0, validators=MONEY,
                                       help_text='Quantity slab in the product unit. 0 = any quantity.')
    rate = models.DecimalField('Rate (₹ per unit, before GST)', max_digits=14, decimal_places=2, validators=POSITIVE)
    valid_from = models.DateField(default=timezone.localdate)
    valid_to = models.DateField(null=True, blank=True, help_text='Empty = until replaced.')
    active = models.BooleanField(default=True)
    replaces = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='revisions', editable=False)
    notes = models.CharField(max_length=150, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['product__name', '-valid_from', 'min_quantity']
        verbose_name = 'price'

    def __str__(self): return f'{self.product.code} ₹{self.rate} ({self.scope_label})'

    @property
    def specificity(self):
        return (4 if self.partner_id else 0) + (3 if self.partner_kind else 0) + (2 if self.district else 0) + (1 if self.site_id else 0)

    @property
    def scope_label(self):
        parts = [self.partner.name if self.partner_id else '', self.get_partner_kind_display() if self.partner_kind else '',
                 self.district, self.site.code if self.site_id else '']
        return ' · '.join(p for p in parts if p) or 'Standard price'

    def is_current(self, on=None):
        on = on or timezone.localdate()
        return self.active and self.valid_from <= on and (self.valid_to is None or self.valid_to >= on)


# ---------- GST tax invoice (Step 2b) ----------

class Invoice(models.Model):
    """Posted GST tax invoice. Everything printed is a snapshot; nothing changes after posting (corrections go through credit notes)."""
    number = models.CharField(max_length=16, unique=True, editable=False)
    date = models.DateField(default=timezone.localdate)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='invoices')
    dispatch = models.OneToOneField('Dispatch', null=True, blank=True, on_delete=models.PROTECT, related_name='invoice')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='invoices')
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='invoices')
    # Seller snapshot
    seller_name = models.CharField(max_length=150)
    seller_gstin = models.CharField(max_length=15)
    seller_state = models.CharField(max_length=2, choices=INDIAN_STATES)
    seller_address = models.TextField()
    # Buyer snapshot
    buyer_name = models.CharField(max_length=150)
    buyer_gstin = models.CharField(max_length=15, blank=True)
    buyer_state = models.CharField(max_length=2, choices=INDIAN_STATES, blank=True)
    buyer_address = models.TextField(blank=True)
    ship_to = models.TextField(blank=True)
    place_of_supply = models.CharField(max_length=2, choices=INDIAN_STATES)
    interstate = models.BooleanField()
    reverse_charge = models.BooleanField(default=False)
    # Transport
    vehicle_number = models.CharField('Vehicle no.', max_length=15, blank=True)
    transporter = models.CharField('Transporter', max_length=120, blank=True)
    lr_number = models.CharField('LR / GR no.', max_length=30, blank=True)
    eway_bill = models.CharField('E-way bill no.', max_length=12, blank=True)
    # Totals
    taxable = models.DecimalField(max_digits=16, decimal_places=2)
    cgst = models.DecimalField(max_digits=16, decimal_places=2)
    sgst = models.DecimalField(max_digits=16, decimal_places=2)
    igst = models.DecimalField(max_digits=16, decimal_places=2)
    round_off = models.DecimalField(max_digits=6, decimal_places=2)
    grand_total = models.DecimalField(max_digits=16, decimal_places=2)
    due_date = models.DateField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        permissions = [('post_invoice', 'Post tax invoices'), ('export_invoice', 'Export invoice register')]

    def __str__(self): return self.number

    @property
    def tax(self):
        return self.cgst + self.sgst + self.igst

    @property
    def is_b2b(self):
        return bool(self.buyer_gstin)

    @property
    def settled(self):
        return sum((s.amount for s in self.settlements.all()), Decimal('0'))

    @property
    def outstanding(self):
        return self.grand_total - self.settled

    @property
    def days_overdue(self):
        return max((timezone.localdate() - self.due_date).days, 0) if self.outstanding > 0 else 0


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    description = models.CharField(max_length=150)
    hsn = models.CharField(max_length=10, blank=True)
    unit = models.CharField(max_length=12)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    rate = models.DecimalField(max_digits=14, decimal_places=2)
    taxable = models.DecimalField(max_digits=16, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    cgst = models.DecimalField(max_digits=16, decimal_places=2)
    sgst = models.DecimalField(max_digits=16, decimal_places=2)
    igst = models.DecimalField(max_digits=16, decimal_places=2)
    total = models.DecimalField(max_digits=16, decimal_places=2)

    class Meta:
        ordering = ['id']


# ---------- Receipts, credit notes & settlement (Step 2c) ----------

RECEIPT_MODES = [('neft', 'NEFT / RTGS / IMPS'), ('upi', 'UPI'), ('cheque', 'Cheque'), ('dd', 'Demand draft'), ('cash', 'Cash')]
CASH_LIMIT = Decimal('200000')  # Income-tax Act s.269ST: no cash receipt of ₹2 lakh or more from one person in a day / transaction


class Receipt(models.Model):
    number = models.CharField(max_length=20, unique=True, editable=False)
    date = models.DateField(default=timezone.localdate)
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='receipts', verbose_name='Received from')
    amount = models.DecimalField(max_digits=16, decimal_places=2, validators=POSITIVE)
    mode = models.CharField(max_length=8, choices=RECEIPT_MODES, default='neft')
    reference = models.CharField('UTR / cheque no.', max_length=40, blank=True)
    instrument_date = models.DateField('Cheque / DD date', null=True, blank=True)
    drawn_on = models.CharField('Bank (payer)', max_length=80, blank=True)
    deposited_to = models.CharField('Deposited to', max_length=80, blank=True, help_text='Our bank account.')
    status = models.CharField(max_length=8, default='posted', choices=[('posted', 'Posted'), ('bounced', 'Bounced')])
    bounced_on = models.DateField(null=True, blank=True)
    bounce_reason = models.CharField(max_length=120, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        permissions = [('bounce_receipt', 'Mark cheques as bounced')]

    def __str__(self): return self.number

    @property
    def allocated(self):
        return sum((s.amount for s in self.settlements.all()), Decimal('0'))

    @property
    def unallocated(self):
        return Decimal('0') if self.status == 'bounced' else self.amount - self.allocated


CREDIT_REASONS = [('rate', 'Rate difference / discount'), ('return', 'Goods returned'), ('damage', 'Damaged / short supply'),
                  ('scheme', 'Scheme / incentive'), ('other', 'Other correction')]


class CreditNote(models.Model):
    """GST credit note against one posted invoice. Tax follows the invoice (same rates, same CGST/SGST or IGST)."""
    number = models.CharField(max_length=16, unique=True, editable=False)
    date = models.DateField(default=timezone.localdate)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='credit_notes')
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='credit_notes')
    reason = models.CharField(max_length=8, choices=CREDIT_REASONS)
    returns_stock = models.BooleanField(default=False, editable=False)
    taxable = models.DecimalField(max_digits=16, decimal_places=2)
    cgst = models.DecimalField(max_digits=16, decimal_places=2)
    sgst = models.DecimalField(max_digits=16, decimal_places=2)
    igst = models.DecimalField(max_digits=16, decimal_places=2)
    round_off = models.DecimalField(max_digits=6, decimal_places=2)
    total = models.DecimalField(max_digits=16, decimal_places=2)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self): return self.number

    @property
    def tax(self):
        return self.cgst + self.sgst + self.igst


class CreditNoteLine(models.Model):
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name='lines')
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name='credit_lines')
    quantity = models.DecimalField('Returned qty', max_digits=14, decimal_places=3, default=0)
    taxable = models.DecimalField(max_digits=16, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2)
    cgst = models.DecimalField(max_digits=16, decimal_places=2)
    sgst = models.DecimalField(max_digits=16, decimal_places=2)
    igst = models.DecimalField(max_digits=16, decimal_places=2)


class Settlement(models.Model):
    """Money or credit applied to an invoice. Outstanding = invoice total − settlements."""
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='settlements')
    receipt = models.ForeignKey(Receipt, null=True, blank=True, on_delete=models.CASCADE, related_name='settlements')
    credit_note = models.ForeignKey(CreditNote, null=True, blank=True, on_delete=models.CASCADE, related_name='settlements')
    amount = models.DecimalField(max_digits=16, decimal_places=2, validators=POSITIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(receipt__isnull=False) | models.Q(credit_note__isnull=False), name='settlement_has_source')]


# ---------- Logistics: vehicles, drivers, dispatch & weighbridge (Step 3) ----------

VEHICLE_TYPES = [('lcv', 'LCV / pickup'), ('truck6', 'Truck 6-wheel'), ('truck10', 'Truck 10-wheel'), ('truck12', 'Truck 12-wheel'), ('truck14', 'Truck 14-wheel'),
                 ('trailer', 'Trailer'), ('bulker', 'Bulker (bulk cement)'), ('tipper', 'Tipper')]
VEHICLE_DOCS = [('rc_valid_until', 'RC'), ('insurance_valid_until', 'Insurance'), ('permit_valid_until', 'Permit'), ('fitness_valid_until', 'Fitness'), ('puc_valid_until', 'PUC')]


class Vehicle(models.Model):
    registration = models.CharField('Vehicle no.', max_length=12, unique=True, help_text='e.g. MP04GA1234 (spaces are removed).')
    vehicle_type = models.CharField('Type', max_length=10, choices=VEHICLE_TYPES)
    capacity_mt = models.DecimalField('Capacity (MT)', max_digits=6, decimal_places=2, validators=POSITIVE, help_text='Registered payload. Loading above this needs approval.')
    tare_kg = models.PositiveIntegerField('Usual tare (kg)', null=True, blank=True, help_text='From earlier weighments; a big change is flagged.')
    owner = models.CharField(max_length=12, default='transporter', choices=[('transporter', 'Transporter'), ('customer', "Customer's own"), ('own', 'Sistech fleet')])
    transporter = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='vehicles', limit_choices_to={'kind': 'Transporter'})
    rc_valid_until = models.DateField('RC valid until', null=True, blank=True)
    insurance_valid_until = models.DateField('Insurance valid until', null=True, blank=True)
    permit_valid_until = models.DateField('Permit valid until', null=True, blank=True)
    fitness_valid_until = models.DateField('Fitness valid until', null=True, blank=True)
    puc_valid_until = models.DateField('PUC valid until', null=True, blank=True)
    gps_device = models.CharField('GPS device ID', max_length=40, blank=True)
    active = models.BooleanField(default=True)
    blacklisted = models.BooleanField(default=False)
    blacklist_reason = models.CharField(max_length=150, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['registration']

    def __str__(self): return self.registration

    def document_issues(self, on=None):
        """[(label, date or None)] for missing / expired documents — any issue stops gate-in."""
        on = on or timezone.localdate()
        return [(label, getattr(self, f)) for f, label in VEHICLE_DOCS if not getattr(self, f) or getattr(self, f) < on]

    def expiring(self, days=30):
        today = timezone.localdate()
        return [(label, getattr(self, f)) for f, label in VEHICLE_DOCS if getattr(self, f) and today <= getattr(self, f) <= today + timezone.timedelta(days=days)]

    @property
    def can_enter(self):
        return self.active and not self.blacklisted and not self.document_issues()


class Driver(models.Model):
    name = models.CharField(max_length=100)
    mobile = models.CharField(max_length=10, unique=True)
    licence_number = models.CharField('Driving licence no.', max_length=20, unique=True)
    licence_valid_until = models.DateField('Licence valid until')
    transporter = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='drivers', limit_choices_to={'kind': 'Transporter'})
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self): return f'{self.name} ({self.mobile})'

    @property
    def licence_valid(self):
        return self.licence_valid_until >= timezone.localdate()


DISPATCH_STATUSES = [('planned', 'Planned'), ('gate_in', 'At gate · tare due'), ('tared', 'Tared · waiting to load'), ('loading', 'Loading'),
                     ('loaded', 'Loaded · gross due'), ('weighed', 'Weighed'), ('gate_out', 'In transit'), ('delivered', 'Delivered'), ('cancelled', 'Cancelled')]


class Dispatch(models.Model):
    """One truck trip against one sales order (partial supply allowed). Follows SRS §4.4 step by step."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    token = models.PositiveIntegerField(editable=False, help_text='Daily queue token per plant.')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='dispatches')
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='dispatches')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name='dispatches')
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name='dispatches')
    transporter = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='trips')
    status = models.CharField(max_length=10, choices=DISPATCH_STATUSES, default='planned')
    planned_for = models.DateField(default=timezone.localdate)
    bay = models.CharField('Loading bay', max_length=30, blank=True)
    lr_number = models.CharField('LR / GR no.', max_length=30, blank=True)
    # Gate
    gate_checklist = models.JSONField(default=dict, blank=True)
    gate_in_at = models.DateTimeField(null=True, blank=True)
    gate_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    # Loading
    loading_started_at = models.DateTimeField(null=True, blank=True)
    loading_done_at = models.DateTimeField(null=True, blank=True)
    loaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    loading_team = models.CharField(max_length=80, blank=True)
    seal_number = models.CharField('Seal no.', max_length=30, blank=True)
    damaged_bags = models.PositiveIntegerField('Torn / damaged bags replaced', default=0)
    loading_notes = models.CharField(max_length=200, blank=True)
    # Weight check
    expected_kg = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    variance_kg = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    exception = models.CharField(max_length=200, blank=True, help_text='Why supervisor approval is needed.')
    exception_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    exception_note = models.CharField(max_length=200, blank=True)
    # Exit & delivery
    stock_posted = models.BooleanField(default=False, editable=False)
    gate_out_at = models.DateTimeField(null=True, blank=True)
    gate_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    delivered_at = models.DateTimeField(null=True, blank=True)
    received_by = models.CharField(max_length=80, blank=True)
    receiver_mobile = models.CharField(max_length=10, blank=True)
    shortage_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    damaged_qty = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    pod_notes = models.CharField(max_length=200, blank=True)
    pod_file = models.BinaryField(null=True, blank=True, editable=False)
    pod_file_type = models.CharField(max_length=40, blank=True, editable=False)
    cancel_reason = models.CharField(max_length=150, blank=True)
    # Freight snapshot at gate-out (contract, not the transporter's claim)
    freight_rate = models.ForeignKey('FreightRate', null=True, blank=True, on_delete=models.PROTECT, related_name='dispatches', editable=False)
    freight_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, editable=False)
    freight_payable_by = models.CharField(max_length=8, blank=True, editable=False, choices=[('company', 'Sistech'), ('customer', 'Customer')])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'dispatches'
        permissions = [('gate_dispatch', 'Gate-in / gate-out trucks'), ('weigh_dispatch', 'Record weighments'), ('load_dispatch', 'Record loading'),
                       ('approve_weight_exception', 'Approve weight / overload exceptions'), ('pod_dispatch', 'Record proof of delivery')]

    def __str__(self): return self.number

    def reading(self, kind):
        return self.weighments.filter(kind=kind, superseded_by__isnull=True).first()

    @property
    def tare(self): return self.reading('tare')

    @property
    def gross(self): return self.reading('gross')

    @property
    def net_kg(self):
        t, g = self.tare, self.gross
        return (g.weight_kg - t.weight_kg) if t and g else None

    @property
    def exception_open(self):
        return bool(self.exception) and not self.exception_approved_by_id


class DispatchLine(models.Model):
    dispatch = models.ForeignKey(Dispatch, on_delete=models.CASCADE, related_name='lines')
    order_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name='dispatch_lines')
    planned_quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    loaded_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)

    @property
    def quantity(self):
        return self.loaded_quantity if self.loaded_quantity is not None else self.planned_quantity

    def unit_kg(self):
        """Kilograms per unit for the weight check: SKU bag weight → organisation default; MT → 1000; KG → 1; pieces → unknown."""
        p = self.order_line.product
        if p.unit == 'MT': return Decimal('1000')
        if p.unit == 'KG': return Decimal('1')
        if p.unit == 'BAG':
            if p.bag_weight_kg: return p.bag_weight_kg
            org = Organization.objects.filter(pk=1).values_list('default_bag_weight', flat=True).first()
            return Decimal(org or 50)
        return None


class Weighment(models.Model):
    """Immutable weighbridge reading. A reweigh adds a new row and links the old one; nothing is ever edited."""
    dispatch = models.ForeignKey(Dispatch, on_delete=models.PROTECT, related_name='weighments')
    kind = models.CharField(max_length=5, choices=[('tare', 'Tare (empty)'), ('gross', 'Gross (loaded)')])
    weight_kg = models.PositiveIntegerField()
    source = models.CharField(max_length=6, default='manual', choices=[('scale', 'Scale interface'), ('manual', 'Manual entry')])
    scale_id = models.CharField(max_length=30, blank=True)
    reason = models.CharField(max_length=150, blank=True, help_text='Required for manual entry and every reweigh.')
    superseded_by = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='supersedes')
    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['captured_at']
        constraints = [models.CheckConstraint(condition=models.Q(weight_kg__gt=0), name='weighment_positive')]


# ---------- Freight (Step 3b, FR-LOG-013/014) ----------

class FreightRate(models.Model):
    """Contracted freight for a lane. Most specific wins: transporter > vehicle type > destination district."""
    origin = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='freight_rates', verbose_name='From plant / depot')
    destination = models.CharField('To district', max_length=80)
    transporter = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='freight_rates', limit_choices_to={'kind': 'Transporter'},
                                    help_text='Empty = any transporter.')
    vehicle_type = models.CharField(max_length=10, choices=VEHICLE_TYPES, blank=True, help_text='Empty = any vehicle.')
    basis = models.CharField(max_length=8, choices=[('per_mt', 'Per MT (net weight)'), ('per_trip', 'Per trip')], default='per_mt')
    rate = models.DecimalField('Rate (₹)', max_digits=12, decimal_places=2, validators=POSITIVE)
    min_mt = models.DecimalField('Minimum chargeable MT', max_digits=6, decimal_places=2, default=0, validators=MONEY, help_text='Per-MT lanes bill at least this weight.')
    detention_per_day = models.DecimalField('Detention per day (₹)', max_digits=10, decimal_places=2, default=0, validators=MONEY)
    unloading_charge = models.DecimalField('Unloading / handling (₹ per trip)', max_digits=10, decimal_places=2, default=0, validators=MONEY)
    distance_km = models.PositiveIntegerField('Distance (km)', null=True, blank=True)
    valid_from = models.DateField(default=timezone.localdate)
    valid_to = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['origin__code', 'destination', '-valid_from']

    def __str__(self): return f'{self.origin.code} → {self.destination}'

    @property
    def specificity(self):
        return (2 if self.transporter_id else 0) + (1 if self.vehicle_type else 0)

    def amount_for(self, net_kg):
        if self.basis == 'per_trip':
            return self.rate
        mt = max(Decimal(net_kg or 0) / 1000, self.min_mt)
        return (mt * self.rate).quantize(Decimal('0.01'))


class FreightBill(models.Model):
    """Transporter's bill checked against contracted freight per trip. Maker-checker before it becomes payable."""
    transporter = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='freight_bills', limit_choices_to={'kind': 'Transporter'})
    bill_number = models.CharField("Transporter's bill no.", max_length=40)
    bill_date = models.DateField()
    status = models.CharField(max_length=9, default='submitted', choices=[('submitted', 'Waiting for check'), ('approved', 'Approved · payable'), ('rejected', 'Rejected')])
    contracted = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    claimed = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    extras = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    deductions = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    advance = models.DecimalField('Advance already paid', max_digits=14, decimal_places=2, default=0, validators=MONEY)
    payable = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    checked_at = models.DateTimeField(null=True, blank=True)
    check_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-bill_date', '-id']
        permissions = [('approve_freightbill', 'Approve transporter freight bills')]
        constraints = [models.UniqueConstraint(fields=['transporter', 'bill_number'], name='unique_transporter_bill')]

    def __str__(self): return f'{self.transporter.name} · {self.bill_number}'

    @property
    def variance(self):
        return self.claimed + self.extras - self.contracted


class FreightBillLine(models.Model):
    bill = models.ForeignKey(FreightBill, on_delete=models.CASCADE, related_name='lines')
    dispatch = models.OneToOneField(Dispatch, on_delete=models.PROTECT, related_name='freight_line')
    contracted = models.DecimalField(max_digits=12, decimal_places=2)
    claimed = models.DecimalField(max_digits=12, decimal_places=2)
    detention_days = models.PositiveSmallIntegerField(default=0)
    extra = models.DecimalField('Detention / toll / unloading claimed', max_digits=12, decimal_places=2, default=0)
    extra_reason = models.CharField(max_length=120, blank=True)
    shortage_recovery = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    allowed_extra = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def payable(self):
        """Freight capped at contract + allowed extras − shortage/damage recovery."""
        return min(self.claimed, self.contracted) + self.allowed_extra - self.shortage_recovery
