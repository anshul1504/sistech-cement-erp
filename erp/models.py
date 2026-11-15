import uuid
from decimal import Decimal
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
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
    atp_required = models.BooleanField('Approve sales only against free stock (ATP)', default=False,
                                       help_text='On: a sales order is approved only if available stock minus other orders covers it. Off: shortages are approved as backorders and recorded.')
    stock_post_event = models.CharField('Stock reduces at', max_length=14, default='gate_out',
                                        choices=[('load_complete', 'Loading complete'), ('gate_out', 'Gate-out')])
    weight_tolerance_pct = models.DecimalField('Weight tolerance (%)', max_digits=4, decimal_places=2, default=Decimal('0.50'), validators=MONEY,
                                               help_text='Allowed difference between weighbridge net and loaded quantity before supervisor approval.')
    scale_id = models.CharField('Weighbridge scale ID', max_length=30, blank=True, help_text='Printed on weighment slips. Calibration is tracked under assets later.')
    loading_bays = models.CharField('Loading bays', max_length=120, blank=True, help_text='Comma separated, e.g. Bay 1, Bay 2, Bulk 1.')
    operated_by = models.ForeignKey('Partner', null=True, blank=True, on_delete=models.PROTECT, related_name='operated_sites',
                                    limit_choices_to={'manufacturing_partner': True}, verbose_name='Operated by (manufacturing partner)',
                                    help_text='Leave empty for a Sistech-owned location.')
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self): return f'{self.code} · {self.name}'

    @property
    def bays(self):
        return [b.strip() for b in self.loading_bays.split(',') if b.strip()]

    @property
    def full_address(self):
        parts = [self.address.strip(), self.city, self.district if (self.district or '').strip().lower() != (self.city or '').strip().lower() else '', f'{self.get_state_display()} {self.pincode}'.strip() if (self.state or self.pincode) else '']
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
    max_level = models.DecimalField('Maximum stock', max_digits=14, decimal_places=3, default=0, validators=MONEY,
                                    help_text='Stock above this is flagged as excess. 0 = no maximum.')
    reorder_quantity = models.DecimalField('Reorder quantity', max_digits=14, decimal_places=3, default=0, validators=MONEY,
                                           help_text='Usual quantity to buy when stock falls below the reorder level. 0 = top up to the reorder level.')
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
    manufacturing_partner = models.BooleanField('Manufacturing partner', default=False,
                                                help_text='Makes SISTECH-brand cement or TMT under a supply agreement (supplier type only).')
    notes = models.TextField(blank=True)
    approved_application = models.ForeignKey('DealerApplication', null=True, blank=True, on_delete=models.PROTECT, related_name='+', editable=False)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    def __str__(self): return self.name

    @property
    def is_customer(self):
        return self.kind in CUSTOMER_KINDS

    @property
    def full_address(self):
        parts = [self.address.strip(), self.city, self.district if (self.district or '').strip().lower() != (self.city or '').strip().lower() else '', f'{self.get_state_display()} {self.pincode}'.strip() if (self.state or self.pincode) else '']
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
    status = models.CharField(max_length=15, default='Draft', choices=[(x, x) for x in ['Draft', 'Submitted', 'Approved', 'Part dispatched', 'Part received', 'Completed', 'On hold', 'Revised', 'Cancelled']])
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='approved_orders')
    place_of_supply = models.CharField('Place of supply', max_length=2, blank=True, choices=INDIAN_STATES,
                                       help_text="Buyer's delivery state. Same state as the plant → CGST + SGST, otherwise IGST.")
    ship_to = models.TextField('Ship-to address', blank=True, help_text="Leave empty to deliver to the partner's delivery / billing address.")
    freight_terms = models.CharField(max_length=16, choices=FREIGHT_TERMS, default='ex_works')
    customer_ref = models.CharField('Customer PO / reference', max_length=40, blank=True)
    # Purchase-only terms (FR-PUR-003)
    quote = models.ForeignKey('SupplierQuote', null=True, blank=True, on_delete=models.PROTECT, related_name='purchase_orders', editable=False)
    award_reason = models.CharField('Why this supplier', max_length=200, blank=True, editable=False)
    payment_terms_days = models.PositiveSmallIntegerField('Payment terms (days)', null=True, blank=True)
    receipt_tolerance_pct = models.DecimalField('Over-receipt tolerance %', max_digits=5, decimal_places=2, default=0, validators=MONEY,
                                                help_text='How much more than ordered the store may accept, e.g. 2% for bulk material by weight.')
    advance_amount = models.DecimalField('Advance to supplier (₹)', max_digits=14, decimal_places=2, default=0, validators=MONEY)
    delivery_terms = models.CharField('Delivery terms', max_length=120, blank=True, help_text='e.g. Door delivery at plant, unloading by supplier.')
    # Sales order terms and controls (FR-SLS-004…010, FR-AR-003/004)
    priority = models.CharField(max_length=8, choices=[('normal', 'Normal'), ('urgent', 'Urgent')], default='normal')
    unloading_by = models.CharField('Unloading by', max_length=12, blank=True, choices=[('customer', 'Customer'), ('company', 'Sistech'), ('transporter', 'Transporter')], default='customer')
    delivery_instructions = models.CharField('Delivery instructions', max_length=200, blank=True, help_text='Site contact, timing, access road, unloading point…')
    revision = models.PositiveSmallIntegerField(default=1, editable=False)
    revised_from = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='revised_to', editable=False)
    approval_checks = models.JSONField(default=dict, blank=True, editable=False, help_text='Credit, stock and price position recorded at approval.')
    credit_override_reason = models.CharField(max_length=200, blank=True, editable=False)
    credit_override_until = models.DateField(null=True, blank=True, editable=False)
    credit_override_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+', editable=False)
    hold_reason = models.CharField(max_length=200, blank=True, editable=False)
    held_from = models.CharField(max_length=15, blank=True, editable=False)
    cancel_reason = models.CharField(max_length=200, blank=True, editable=False)
    short_close_reason = models.CharField(max_length=200, blank=True, editable=False)
    class Meta(Document.Meta):
        permissions = [('approve_order', 'Approve orders (legacy)'), ('post_order', 'Receive or dispatch orders (legacy)'),
                       ('approve_high_value_purchase', 'Approve purchase orders above the approval limit'),
                       ('override_credit_sales_order', 'Approve sales orders beyond credit / overdue rules with a recorded reason'),
                       ('approve_price_exception', 'Approve sales below list price'),
                       ('hold_sales_order', 'Put sales orders on hold and release them')] + [
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
    pr_line = models.ForeignKey('PurchaseRequestLine', null=True, blank=True, on_delete=models.PROTECT, related_name='po_lines', editable=False)
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

    def received_quantity(self):
        """Purchase lines: accepted + quality-hold quantity on posted GRNs. Orders completed by the old one-step receipt count as fully received."""
        from django.db.models import Sum
        agg = self.grn_lines.filter(grn__status='posted').aggregate(a=Sum('accepted'), h=Sum('hold'))
        got = (agg['a'] or Decimal('0')) + (agg['h'] or Decimal('0'))
        if not got and self.order.status == 'Completed' and not self.grn_lines.exists():
            return self.quantity
        return got

    @property
    def to_receive(self):
        return max(self.quantity - self.received_quantity(), Decimal('0'))

STOCK_STATUSES = [('available', 'Available'), ('damaged', 'Damaged'), ('blocked', 'Blocked')]


class StockBalance(models.Model):
    """Quantity on hand per site, product and stock state, with weighted-average cost per unit (FR-INV-001/002)."""
    site = models.ForeignKey(Site, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    status = models.CharField(max_length=10, choices=STOCK_STATUSES, default='available')
    quantity = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    avg_cost = models.DecimalField('Average cost (₹ per unit)', max_digits=14, decimal_places=4, default=0)
    carrying_value = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, editable=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['site', 'product', 'status'], name='unique_stock_balance_status'), models.CheckConstraint(condition=models.Q(quantity__gte=0), name='stock_nonnegative')]

    @property
    def value(self):
        return self.carrying_value if self.carrying_value is not None else (self.quantity * self.avg_cost).quantize(Decimal('0.01'))

STOCK_KINDS = [('Opening', 'Opening stock'), ('Receipt', 'Purchase receipt'), ('Dispatch', 'Sales dispatch'), ('Production', 'Production output'),
               ('Return', 'Sales return'), ('PurchaseReturn', 'Return to supplier'), ('TransferOut', 'Transfer out'), ('TransferIn', 'Transfer in'),
               ('Damage', 'Moved to damaged'), ('Recovered', 'Recovered from damaged'), ('Scrap', 'Scrapped'), ('Adjustment', 'Count adjustment'),
               ('Consumption', 'Issued to production')]


class StockMovement(Document):
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    source = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=20, choices=STOCK_KINDS)
    status = models.CharField('Stock state', max_length=10, choices=STOCK_STATUSES, default='available')
    uom = models.CharField('Unit', max_length=12, blank=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    balance_after = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    value_delta = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, editable=False)
    class Meta(Document.Meta):
        permissions = [('post_opening', 'Post opening stock'), ('export_stockmovement', 'Export stock ledger')]

    @property
    def value(self):
        return self.value_delta if self.value_delta is not None else (self.quantity * (self.unit_cost or Decimal('0'))).quantize(Decimal('0.01'))


SHIFTS = [('A', 'Shift A'), ('B', 'Shift B'), ('C', 'Shift C')]
PRODUCTION_STATUSES = ['Planned', 'In progress', 'Quality hold', 'Released', 'Rejected', 'Cancelled']


class Production(Document):
    """Process order / production batch (FR-PRD-003): recipe version, line, material issue, output, actual cost and quality release."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    planned_quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    actual_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    batch = models.CharField(max_length=60, unique=True)
    shift = models.CharField(max_length=10, choices=SHIFTS)
    status = models.CharField(max_length=15, default='Planned', choices=[(x, x) for x in PRODUCTION_STATUSES])
    recipe = models.ForeignKey('Recipe', null=True, blank=True, on_delete=models.PROTECT, related_name='orders')
    line = models.CharField('Line / mill', max_length=40, blank=True)
    planned_date = models.DateField(null=True, blank=True)
    scrap_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    material_cost = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    variance_note = models.CharField(max_length=200, blank=True)
    disposition = models.CharField(max_length=12, blank=True, choices=[('release', 'Released'), ('deviation', 'Released under deviation'), ('reject', 'Rejected')])
    disposition_note = models.CharField(max_length=200, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    released_at = models.DateTimeField(null=True, blank=True)
    class Meta(Document.Meta):
        permissions = [('complete_production', 'Issue material and complete production'), ('release_production', 'Release quality batch')]

    def __str__(self): return self.batch

    @property
    def badge(self):
        return {'Planned': 'draft', 'In progress': 'approved', 'Quality hold': 'submitted', 'Released': 'completed'}.get(self.status, 'cancelled')

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
    # Purchase controls
    po_approval_limit = models.DecimalField('PO approval limit (₹)', max_digits=14, decimal_places=2, default=500000, validators=MONEY,
                                            help_text='Purchase orders above this value (incl. GST) need a senior approver.')
    match_qty_tolerance_pct = models.DecimalField('Invoice quantity tolerance %', max_digits=5, decimal_places=2, default=0, validators=MONEY,
                                                  help_text='Supplier invoice may bill this much above the accepted GRN quantity.')
    match_price_tolerance_pct = models.DecimalField('Invoice rate tolerance %', max_digits=5, decimal_places=2, default=0, validators=MONEY,
                                                    help_text='Supplier invoice rate may be this much above the PO rate.')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name = 'organisation'
        permissions = [('view_executive_dashboard', 'View the management (executive) dashboard')]

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
        parts = [self.address.strip(), self.city, self.district if (self.district or '').strip().lower() != (self.city or '').strip().lower() else '',
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

DOC_SERIES = [('PRJ', 'Sales project'), ('BEAT', 'Daily beat plan'), ('RMA', 'Customer return'), ('HLP', 'Support ticket'), ('CMP', 'Complaint'), ('QT', 'Quotation'), ('SCH', 'Scheme payout'), ('PAY', 'Supplier payment'), ('JV', 'Journal'), ('PCK', 'Packing run'), ('TRF', 'Stock transfer'), ('DMG', 'Damage report'), ('CNT', 'Stock count'), ('SO', 'Sales order'), ('PO', 'Purchase order'), ('INV', 'Tax invoice'), ('RCT', 'Receipt'), ('CN', 'Credit note'),
              ('PR', 'Purchase request'), ('RFQ', 'Request for quotation'), ('GRN', 'Goods receipt'), ('DN', 'Debit note'), ('SINV', 'Supplier invoice')]


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
    submission_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    submission_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
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
    submission_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    submission_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
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
    scheme_credit = models.ForeignKey('SchemeSettlement', null=True, blank=True, on_delete=models.CASCADE, related_name='settlements')
    amount = models.DecimalField(max_digits=16, decimal_places=2, validators=POSITIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(receipt__isnull=False) | models.Q(credit_note__isnull=False) | models.Q(scheme_credit__isnull=False),
                                              name='settlement_has_source_v2')]


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
    batch = models.ForeignKey('Production', null=True, blank=True, on_delete=models.PROTECT, related_name='dispatch_lines', help_text='Cement batch loaded (traceability, COA).')

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
    dispatch = models.ForeignKey(Dispatch, null=True, blank=True, on_delete=models.PROTECT, related_name='weighments')
    grn = models.ForeignKey('GoodsReceipt', null=True, blank=True, on_delete=models.PROTECT, related_name='weighments')
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
        constraints = [models.CheckConstraint(condition=models.Q(weight_kg__gt=0), name='weighment_positive'),
                       models.CheckConstraint(condition=models.Q(dispatch__isnull=False, grn__isnull=True) | models.Q(dispatch__isnull=True, grn__isnull=False),
                                              name='weighment_one_document')]


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
    review_snapshot = models.JSONField(default=dict, blank=True, editable=False)
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


# ---------- Procurement: purchase request, RFQ, supplier quote (Step 4a, SRS §4.2 / FR-PUR-001…003) ----------

PR_SOURCES = [('manual', 'Manual request'), ('reorder', 'Reorder level'), ('production', 'Production plan'), ('maintenance', 'Maintenance / spares')]
PR_STATUSES = [('draft', 'Draft'), ('submitted', 'Waiting approval'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('closed', 'Fully ordered'), ('cancelled', 'Cancelled')]


class PurchaseRequest(models.Model):
    """What a plant needs, by when and why. Approved by someone other than the requester before buying starts."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='purchase_requests', verbose_name='Plant / depot')
    needed_by = models.DateField('Needed by')
    source = models.CharField('Raised from', max_length=12, choices=PR_SOURCES, default='manual')
    priority = models.CharField(max_length=8, choices=[('normal', 'Normal'), ('urgent', 'Urgent')], default='normal')
    purpose = models.CharField('Purpose / department', max_length=150, help_text='e.g. Kiln fuel for November, raw mill liner replacement.')
    status = models.CharField(max_length=10, choices=PR_STATUSES, default='draft')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_purchaserequest', 'Approve or reject purchase requests')]

    def __str__(self): return self.number

    @property
    def badge(self):
        return {'draft': 'draft', 'submitted': 'submitted', 'approved': 'approved', 'closed': 'completed'}.get(self.status, 'cancelled')

    @property
    def estimated_value(self):
        return sum(((l.estimated_rate or 0) * l.quantity for l in self.lines.all()), Decimal('0'))


class PurchaseRequestLine(models.Model):
    request = models.ForeignKey(PurchaseRequest, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Item')
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    estimated_rate = models.DecimalField('Estimated rate (₹)', max_digits=14, decimal_places=2, null=True, blank=True, validators=MONEY)
    specification = models.CharField('Specification / remarks', max_length=150, blank=True)
    stock_at_request = models.DecimalField(max_digits=16, decimal_places=3, null=True, editable=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='pr_quantity_positive')]

    def ordered_quantity(self, exclude_order=None):
        qs = self.po_lines.exclude(order__status='Cancelled')
        if exclude_order:
            qs = qs.exclude(order_id=exclude_order)
        return sum((l.quantity for l in qs), Decimal('0'))

    @property
    def pending_quantity(self):
        return max(self.quantity - self.ordered_quantity(), Decimal('0'))


RFQ_STATUSES = [('open', 'Collecting quotes'), ('awarded', 'Awarded'), ('cancelled', 'Cancelled')]


class RFQ(models.Model):
    """Request for quotation against an approved purchase request. One supplier is awarded the whole RFQ."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    request = models.ForeignKey(PurchaseRequest, on_delete=models.PROTECT, related_name='rfqs')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='rfqs')
    quotes_due = models.DateField('Quotes due by')
    suppliers = models.ManyToManyField(Partner, related_name='rfqs_invited', limit_choices_to={'kind': 'Supplier'})
    terms = models.CharField('Terms sent to suppliers', max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=RFQ_STATUSES, default='open')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'RFQ'
        permissions = [('award_rfq', 'Award an RFQ and raise the purchase order')]

    def __str__(self): return self.number

    @property
    def badge(self):
        return {'open': 'submitted', 'awarded': 'completed'}.get(self.status, 'cancelled')


class RFQLine(models.Model):
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name='lines')
    pr_line = models.ForeignKey(PurchaseRequestLine, on_delete=models.PROTECT, related_name='rfq_lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)


class SupplierQuote(models.Model):
    """A supplier's offer for an RFQ. Compared on landed cost (rate + freight), with lead time, credit days and quality history beside it."""
    rfq = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name='quotes')
    supplier = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='quotes', limit_choices_to={'kind': 'Supplier'})
    reference = models.CharField('Quote ref.', max_length=40, blank=True)
    quote_date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField(null=True, blank=True)
    freight = models.DecimalField('Freight & handling (₹, total)', max_digits=14, decimal_places=2, default=0, validators=MONEY,
                                  help_text='0 when the rate is delivered at plant.')
    lead_time_days = models.PositiveSmallIntegerField('Delivery in (days)', default=7)
    payment_terms_days = models.PositiveSmallIntegerField('Credit (days)', default=0)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['rfq', 'supplier__name']
        constraints = [models.UniqueConstraint(fields=['rfq', 'supplier'], name='one_quote_per_supplier')]

    def __str__(self): return f'{self.supplier} · {self.rfq}'

    @property
    def taxable(self):
        return sum((l.rate * l.rfq_line.quantity for l in self.lines.all()), Decimal('0'))

    @property
    def gst(self):
        return sum(((l.rate * l.rfq_line.quantity * l.gst_rate / 100) for l in self.lines.all()), Decimal('0')).quantize(Decimal('0.01'))

    @property
    def landed(self):
        return self.taxable + self.freight

    def is_valid(self, on=None):
        return not self.valid_until or self.valid_until >= (on or timezone.localdate())


class SupplierQuoteLine(models.Model):
    quote = models.ForeignKey(SupplierQuote, on_delete=models.CASCADE, related_name='lines')
    rfq_line = models.ForeignKey(RFQLine, on_delete=models.CASCADE, related_name='quote_lines')
    rate = models.DecimalField('Rate (₹ per unit, before GST)', max_digits=14, decimal_places=2, validators=MONEY)
    gst_rate = models.DecimalField('GST %', max_digits=5, decimal_places=2, default=18)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['quote', 'rfq_line'], name='one_rate_per_rfq_line')]


# ---------- Inbound gate, weighment and goods receipt (Step 4b, FR-PUR-004…007) ----------

GRN_STATUSES = [('gate_in', 'Inside — waiting weighment'), ('weighed', 'Gross weighed — unloading'), ('unloaded', 'Tare weighed — ready for GRN'),
                ('posted', 'GRN posted'), ('cancelled', 'Cancelled')]


class GoodsReceipt(models.Model):
    """One supplier truck against one purchase order: gate-in → gross → unload → tare → GRN (accepted / rejected / damaged / quality hold)."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='receipts_in')
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='grns', limit_choices_to={'kind': 'Purchase'})
    supplier = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='grns')
    status = models.CharField(max_length=10, choices=GRN_STATUSES, default='gate_in')
    vehicle_number = models.CharField('Vehicle no.', max_length=15)
    driver_name = models.CharField(max_length=80, blank=True)
    driver_mobile = models.CharField(max_length=10, blank=True)
    transporter_name = models.CharField('Transporter', max_length=100, blank=True)
    challan_number = models.CharField("Supplier's invoice / challan no.", max_length=40)
    challan_date = models.DateField(null=True, blank=True)
    eway_bill = models.CharField('E-way bill no.', max_length=12, blank=True)
    photo = models.BinaryField(null=True, blank=True, editable=False)
    photo_type = models.CharField(max_length=40, blank=True, editable=False)
    gate_in_at = models.DateTimeField(default=timezone.now)
    gate_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    variance_reason = models.CharField('Reason for shortage / excess', max_length=200, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    notes = models.CharField(max_length=200, blank=True)
    cancel_reason = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['-gate_in_at']
        verbose_name = 'goods receipt'
        permissions = [('post_goodsreceipt', 'Post goods receipt (store)'), ('release_goodsreceipt', 'Release or reject quality-hold material')]
        constraints = [models.UniqueConstraint(fields=['supplier', 'challan_number'], condition=~models.Q(status='cancelled'), name='unique_supplier_challan')]

    def __str__(self): return self.number

    def reading(self, kind):
        return self.weighments.filter(kind=kind, superseded_by__isnull=True).first()

    @property
    def gross(self): return self.reading('gross')

    @property
    def tare(self): return self.reading('tare')

    @property
    def net_kg(self):
        g, t = self.gross, self.tare
        return (g.weight_kg - t.weight_kg) if g and t else None

    @property
    def badge(self):
        return {'posted': 'completed', 'cancelled': 'cancelled', 'unloaded': 'approved'}.get(self.status, 'submitted')

    @property
    def open_hold(self):
        return sum((l.hold for l in self.lines.all() if not l.qc_at), Decimal('0'))


class GRNLine(models.Model):
    grn = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name='lines')
    po_line = models.ForeignKey(OrderLine, on_delete=models.PROTECT, related_name='grn_lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    challan_qty = models.DecimalField("Supplier's qty", max_digits=14, decimal_places=3, default=0, validators=MONEY)
    received = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    accepted = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    rejected = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    damaged = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    hold = models.DecimalField('Quality hold', max_digits=14, decimal_places=3, default=0, validators=MONEY)
    reject_reason = models.CharField(max_length=150, blank=True)
    # Quality release of the held quantity
    qc_accepted = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    qc_rejected = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    qc_note = models.CharField(max_length=200, blank=True)
    qc_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    qc_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(received=models.F('accepted') + models.F('rejected') + models.F('damaged') + models.F('hold')),
                                              name='grn_line_balances')]

    @property
    def short(self):
        return max(self.challan_qty - self.received, Decimal('0'))

    @property
    def excess(self):
        return max(self.received - self.challan_qty, Decimal('0'))

    @property
    def usable(self):
        """Quantity Sistech keeps (and pays for): accepted now + accepted after quality release."""
        return self.accepted + self.qc_accepted


# ---------- Supplier invoice, three-way match and debit notes (Step 4c, FR-PUR-008…010) ----------

SINV_STATUSES = [('matched', 'Matched'), ('exception', 'Exception — needs decision'), ('approved', 'Approved'), ('rejected', 'Rejected')]


class SupplierInvoice(models.Model):
    """Supplier's tax invoice, matched line by line against PO rate and accepted GRN quantity (three-way match)."""
    number = models.CharField(max_length=24, unique=True, editable=False)
    supplier = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='supplier_invoices')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='supplier_invoices')
    invoice_number = models.CharField("Supplier's invoice no.", max_length=40)
    invoice_key = models.CharField(max_length=40, editable=False, db_index=True)
    invoice_date = models.DateField()
    supplier_gstin = models.CharField('GSTIN on invoice', max_length=15, blank=True)
    billed_taxable = models.DecimalField('Taxable value on invoice', max_digits=16, decimal_places=2, validators=MONEY)
    billed_tax = models.DecimalField('GST on invoice', max_digits=16, decimal_places=2, validators=MONEY)
    billed_total = models.DecimalField('Invoice total', max_digits=16, decimal_places=2, validators=MONEY)
    matched_taxable = models.DecimalField(max_digits=16, decimal_places=2, default=0, editable=False)
    matched_tax = models.DecimalField(max_digits=16, decimal_places=2, default=0, editable=False)
    payable = models.DecimalField(max_digits=16, decimal_places=2, default=0, editable=False)
    due_date = models.DateField(null=True, blank=True, editable=False)
    status = models.CharField(max_length=10, choices=SINV_STATUSES, editable=False)
    issues = models.JSONField(default=list, blank=True, editable=False)
    decision = models.CharField(max_length=10, blank=True, editable=False, choices=[('billed', 'Pay as billed'), ('matched', 'Pay matched value'), ('reject', 'Rejected')])
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_supplierinvoice', 'Decide supplier-invoice match exceptions')]

    def __str__(self): return f'{self.supplier} · {self.invoice_number}'

    @property
    def badge(self):
        return {'matched': 'completed', 'approved': 'completed', 'exception': 'submitted'}.get(self.status, 'cancelled')

    @property
    def difference(self):
        return self.billed_total - (self.matched_taxable + self.matched_tax)


class SupplierInvoiceLine(models.Model):
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name='lines')
    grn_line = models.ForeignKey(GRNLine, on_delete=models.PROTECT, related_name='invoice_lines')
    quantity = models.DecimalField('Billed qty', max_digits=14, decimal_places=3, validators=POSITIVE)
    rate = models.DecimalField('Billed rate', max_digits=14, decimal_places=2, validators=MONEY)
    gst_rate = models.DecimalField('Billed GST %', max_digits=5, decimal_places=2)
    po_rate = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    po_gst_rate = models.DecimalField(max_digits=5, decimal_places=2, editable=False)
    open_qty = models.DecimalField('Accepted, not yet billed', max_digits=14, decimal_places=3, editable=False)
    matched_qty = models.DecimalField(max_digits=14, decimal_places=3, editable=False)
    matched_rate = models.DecimalField(max_digits=14, decimal_places=2, editable=False)
    issue = models.CharField(max_length=200, blank=True, editable=False)

    @property
    def billed_value(self):
        return (self.quantity * self.rate).quantize(Decimal('0.01'))

    @property
    def matched_value(self):
        return (self.matched_qty * self.matched_rate).quantize(Decimal('0.01'))


DEBIT_REASONS = [('return', 'Material returned to supplier'), ('rejected', 'Rejected / damaged material billed'), ('short', 'Short receipt billed'),
                 ('rate', 'Rate / quantity difference on invoice'), ('quality', 'Quality claim'), ('other', 'Other')]


class DebitNote(models.Model):
    """Claim on a supplier. Linked to the GRN (and supplier invoice when it settles an over-billing). A 'return' takes stock out."""
    number = models.CharField(max_length=24, unique=True, editable=False)
    supplier = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='debit_notes')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='debit_notes')
    date = models.DateField(default=timezone.localdate)
    grn = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.PROTECT, related_name='debit_notes')
    supplier_invoice = models.ForeignKey(SupplierInvoice, null=True, blank=True, on_delete=models.PROTECT, related_name='debit_notes')
    reason = models.CharField(max_length=10, choices=DEBIT_REASONS)
    remarks = models.CharField(max_length=200)
    vehicle_number = models.CharField('Return vehicle no.', max_length=15, blank=True)
    taxable = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self): return self.number


class DebitNoteLine(models.Model):
    note = models.ForeignKey(DebitNote, on_delete=models.CASCADE, related_name='lines')
    grn_line = models.ForeignKey(GRNLine, null=True, blank=True, on_delete=models.PROTECT, related_name='debit_lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    from_stock = models.BooleanField(default=False, help_text='Accepted material sent back — stock reduces.')
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    rate = models.DecimalField(max_digits=14, decimal_places=2, validators=MONEY)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    amount = models.DecimalField('Taxable amount', max_digits=16, decimal_places=2)


# ---------- Inventory depth: transfers and damage (Step 5a, FR-INV-005/008) ----------

TRANSFER_STATUSES = [('requested', 'Waiting approval'), ('approved', 'Approved — to dispatch'), ('rejected', 'Rejected'), ('in_transit', 'In transit'),
                     ('discrepancy', 'Received — discrepancy open'), ('received', 'Received'), ('cancelled', 'Cancelled')]


class StockTransfer(models.Model):
    """Stock moved between Sistech locations: request → approval → dispatch (in transit) → receipt; shortage opens a case."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    from_site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='transfers_out', verbose_name='From')
    to_site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='transfers_in', verbose_name='To')
    status = models.CharField(max_length=12, choices=TRANSFER_STATUSES, default='requested')
    needed_by = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=150)
    vehicle_number = models.CharField(max_length=15, blank=True)
    driver_name = models.CharField(max_length=80, blank=True)
    lr_number = models.CharField('LR / GR no.', max_length=30, blank=True)
    eway_bill = models.CharField(max_length=12, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    dispatched_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    dispatched_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    received_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    resolution = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_stocktransfer', 'Approve transfers and resolve discrepancies'), ('dispatch_stocktransfer', 'Dispatch transfers'),
                       ('receive_stocktransfer', 'Receive transfers')]

    def __str__(self): return self.number

    @property
    def badge(self):
        return {'requested': 'submitted', 'approved': 'approved', 'in_transit': 'approved', 'received': 'completed', 'discrepancy': 'submitted'}.get(self.status, 'cancelled')


class StockTransferLine(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    requested = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    dispatched = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    received = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    damaged = models.DecimalField('Received damaged', max_digits=14, decimal_places=3, default=0)
    cost_value = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, editable=False)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)

    @property
    def shortage(self):
        return max(self.dispatched - self.received - self.damaged, Decimal('0'))


    @property
    def total_cost(self):
        return self.cost_value if self.cost_value is not None else (self.dispatched * (self.unit_cost or Decimal('0'))).quantize(Decimal('0.01'))

    @property
    def received_cost(self):
        return (self.total_cost * (self.received + self.damaged) / self.dispatched).quantize(Decimal('0.01')) if self.dispatched else Decimal('0')

    @property
    def loss_value(self):
        return self.total_cost - self.received_cost


DAMAGE_REASONS = [('torn', 'Torn / burst bags'), ('wet', 'Wet / water damage'), ('leaking', 'Leaking bags'), ('hardened', 'Hardened / lumped cement'),
                  ('rusted', 'Rusted / bent TMT'), ('contaminated', 'Contaminated'), ('other', 'Other')]


class DamageReport(models.Model):
    """Damaged, wet, leaking or hardened stock: moved out of available on reporting; a different user decides recovery vs scrap."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='damage_reports')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    reason = models.CharField(max_length=12, choices=DAMAGE_REASONS)
    location = models.CharField('Where in the plant / godown', max_length=100)
    incident_owner = models.CharField('Responsible person', max_length=100)
    description = models.CharField(max_length=200, blank=True)
    photo = models.BinaryField(null=True, blank=True, editable=False)
    photo_type = models.CharField(max_length=40, blank=True, editable=False)
    cost_value = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, editable=False)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0, editable=False)
    status = models.CharField(max_length=10, default='reported', choices=[('reported', 'Waiting disposition'), ('decided', 'Decided')])
    recovered = models.DecimalField('Recovered (back to available)', max_digits=14, decimal_places=3, default=0)
    scrapped = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    decision_note = models.CharField(max_length=200, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('decide_damagereport', 'Decide disposition of damaged stock')]

    def __str__(self): return self.number

    @property
    def value(self):
        return self.cost_value if self.cost_value is not None else (self.quantity * self.unit_cost).quantize(Decimal('0.01'))


# ---------- Stock count (Step 5b, FR-INV-006) ----------

COUNT_STATUSES = [('counting', 'Counting'), ('submitted', 'Waiting approval'), ('recount', 'Recount requested'), ('posted', 'Approved & posted'), ('cancelled', 'Cancelled')]


class StockCount(models.Model):
    """Cycle / annual count. System quantity is frozen when counting starts; the approved variance is posted as an adjustment."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='counts')
    category = models.CharField(max_length=25, blank=True, choices=PRODUCT_CATEGORIES, help_text='Empty = every item with stock.')
    blind = models.BooleanField('Blind count', default=True, help_text='Counters do not see the system quantity.')
    status = models.CharField(max_length=10, choices=COUNT_STATUSES, default='counting')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_stockcount', 'Approve count variances and post adjustments')]

    def __str__(self): return self.number

    @property
    def badge(self):
        return {'counting': 'draft', 'submitted': 'submitted', 'recount': 'submitted', 'posted': 'completed'}.get(self.status, 'cancelled')


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    system_qty = models.DecimalField(max_digits=16, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    counted = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    recount_needed = models.BooleanField(default=False)
    recounted = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True)
    reason = models.CharField(max_length=150, blank=True)

    @property
    def final(self):
        return self.recounted if self.recounted is not None else self.counted

    @property
    def variance(self):
        return None if self.final is None else self.final - self.system_qty

    @property
    def variance_value(self):
        v = self.variance
        return None if v is None else (v * self.unit_cost).quantize(Decimal('0.01'))


# ---------- Production: recipes, material issue, shift log, packing (Step 6a, FR-PRD-002…010) ----------

class Recipe(models.Model):
    """Versioned recipe / BOM. Approved by a different user; a new approved version retires the old one; orders keep the version used."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='recipes')
    version = models.PositiveSmallIntegerField(editable=False)
    basis_quantity = models.DecimalField('Per output quantity', max_digits=12, decimal_places=3, default=1, validators=POSITIVE,
                                         help_text="Material quantities below are for this much output, in the product's unit (e.g. 1 MT).")
    expected_yield_pct = models.DecimalField('Expected yield %', max_digits=5, decimal_places=2, default=100, validators=POSITIVE)
    effective_from = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, default='draft', choices=[('draft', 'Draft'), ('approved', 'Approved'), ('retired', 'Retired')])
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['product__name', '-version']
        constraints = [models.UniqueConstraint(fields=['product', 'version'], name='unique_recipe_version')]
        permissions = [('approve_recipe', 'Approve recipe / BOM versions')]

    def __str__(self): return f'{self.product.code} v{self.version}'


class RecipeLine(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='lines')
    material = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='+')
    quantity = models.DecimalField(max_digits=12, decimal_places=4, validators=POSITIVE)
    tolerance_pct = models.DecimalField('Tolerance %', max_digits=5, decimal_places=2, default=2, validators=MONEY)


class MaterialIssue(models.Model):
    """Material consumed by a process order (FR-PRD-004). Posted to stock at average cost; source silo / lot kept for traceability."""
    production = models.ForeignKey(Production, on_delete=models.PROTECT, related_name='issues')
    material = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='+')
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    source = models.CharField('Source silo / lot', max_length=60, blank=True)
    grn = models.ForeignKey('GoodsReceipt', null=True, blank=True, on_delete=models.PROTECT, related_name='issues', verbose_name='From receipt (lot)')
    cost_value = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True, editable=False)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['issued_at']

    @property
    def value(self):
        return self.cost_value if self.cost_value is not None else (self.quantity * self.unit_cost).quantize(Decimal('0.01'))


DOWNTIME_REASONS = [('', 'No downtime'), ('mechanical', 'Mechanical breakdown'), ('electrical', 'Electrical / power'), ('material', 'No material / feed'),
                    ('planned', 'Planned maintenance'), ('quality', 'Quality problem'), ('other', 'Other')]


class ShiftLog(models.Model):
    """One shift on one line (FR-PRD-005): running hours, downtime with reason, power, output, crew and supervisor."""
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='shift_logs')
    date = models.DateField(default=timezone.localdate)
    shift = models.CharField(max_length=1, choices=SHIFTS)
    line = models.CharField('Line / mill', max_length=40)
    production = models.ForeignKey(Production, null=True, blank=True, on_delete=models.SET_NULL, related_name='shift_logs')
    run_hours = models.DecimalField(max_digits=4, decimal_places=2, validators=MONEY)
    downtime_minutes = models.PositiveIntegerField(default=0)
    downtime_reason = models.CharField(max_length=12, blank=True, choices=DOWNTIME_REASONS)
    power_kwh = models.DecimalField('Power (kWh)', max_digits=12, decimal_places=2, default=0, validators=MONEY)
    output = models.DecimalField('Output', max_digits=14, decimal_places=3, default=0, validators=MONEY)
    meter_opening = models.DecimalField('Meter opening', max_digits=14, decimal_places=2, null=True, blank=True)
    meter_closing = models.DecimalField('Meter closing', max_digits=14, decimal_places=2, null=True, blank=True)
    crew = models.CharField(max_length=150, blank=True)
    supervisor = models.CharField(max_length=80)
    remarks = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-shift']
        constraints = [models.UniqueConstraint(fields=['site', 'date', 'shift', 'line'], name='one_log_per_shift_line')]

    @property
    def kwh_per_unit(self):
        return (self.power_kwh / self.output).quantize(Decimal('0.01')) if self.output else None


class PackingRun(models.Model):
    """Bulk cement packed into bags (FR-PRD-008). Bulk issued by silo meter is reconciled to bags × bag weight + spillage (FR-INV-009)."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='packing_runs')
    date = models.DateField(default=timezone.localdate)
    shift = models.CharField(max_length=1, choices=SHIFTS)
    packer = models.CharField('Packer / line', max_length=40)
    bulk_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='+', verbose_name='Bulk cement')
    bag_product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='+', verbose_name='Bagged SKU')
    batch = models.ForeignKey(Production, null=True, blank=True, on_delete=models.PROTECT, related_name='packing_runs', verbose_name='Cement batch')
    bulk_issued = models.DecimalField('Bulk issued (MT, silo meter)', max_digits=12, decimal_places=3, validators=POSITIVE)
    bags_packed = models.PositiveIntegerField()
    bags_burst = models.PositiveIntegerField('Burst / rejected bags', default=0)
    spillage_kg = models.DecimalField('Spillage (kg)', max_digits=10, decimal_places=2, default=0, validators=MONEY)
    downtime_minutes = models.PositiveIntegerField(default=0)
    empty_bags = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='Empty bag stock item')
    difference_mt = models.DecimalField(max_digits=12, decimal_places=3, default=0, editable=False)
    difference_reason = models.CharField(max_length=200, blank=True)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=4, default=0, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self): return self.number


# ---------- Quality lab (Step 6b, FR-QC-001…006) ----------

class QualitySpec(models.Model):
    """Test parameter with limits for a product (e.g. IS 269 OPC 53: 28-day strength ≥ 53 MPa)."""
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='specs')
    parameter = models.CharField(max_length=80)
    unit = models.CharField(max_length=20, blank=True)
    method = models.CharField('Test method', max_length=60, blank=True)
    min_value = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    age_days = models.PositiveSmallIntegerField('Test age (days)', default=0, help_text='0 = test at once; 3 / 7 / 28 for cube strength.')
    required_for_release = models.BooleanField(default=True)
    sequence = models.PositiveSmallIntegerField(default=10)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['product', 'sequence', 'parameter']
        constraints = [models.UniqueConstraint(fields=['product', 'parameter'], name='unique_spec_parameter')]

    def __str__(self): return f'{self.parameter} ({self.limit_text})'

    @property
    def limit_text(self):
        u = f' {self.unit}' if self.unit else ''
        lo = f'{Decimal(self.min_value).normalize():f}' if self.min_value is not None else None
        hi = f'{Decimal(self.max_value).normalize():f}' if self.max_value is not None else None
        if lo and hi: return f'{lo}–{hi}{u}'
        if lo: return f'≥ {lo}{u}'
        if hi: return f'≤ {hi}{u}'
        return 'Record only'

    def passes(self, value):
        return (self.min_value is None or value >= self.min_value) and (self.max_value is None or value <= self.max_value)


class LabEquipment(models.Model):
    """Lab instrument with calibration due date (FR-QC-006). Overdue equipment cannot be used for results."""
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='lab_equipment')
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    last_calibrated = models.DateField(null=True, blank=True)
    calibration_due = models.DateField()
    certificate = models.CharField('Calibration certificate no.', max_length=60, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['site', 'code']
        constraints = [models.UniqueConstraint(fields=['site', 'code'], name='unique_lab_equipment')]

    def __str__(self): return f'{self.code} · {self.name}'

    @property
    def overdue(self):
        return self.calibration_due < timezone.localdate()

    @property
    def due_days(self):
        return (self.calibration_due - timezone.localdate()).days


class QualityResult(models.Model):
    """One measured value (FR-QC-002). Never edited: a retest adds a new result with a reason and supersedes the old one (FR-QC-003)."""
    production = models.ForeignKey(Production, on_delete=models.PROTECT, related_name='results')
    spec = models.ForeignKey(QualitySpec, on_delete=models.PROTECT, related_name='results')
    value = models.DecimalField(max_digits=12, decimal_places=4)
    ok = models.BooleanField(editable=False)
    equipment = models.ForeignKey(LabEquipment, null=True, blank=True, on_delete=models.PROTECT, related_name='results')
    sample_ref = models.CharField('Sample ref.', max_length=40, blank=True)
    remarks = models.CharField(max_length=150, blank=True)
    retest_reason = models.CharField(max_length=150, blank=True)
    superseded_by = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='supersedes')
    analyst = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    tested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['spec__sequence', 'tested_at']


# ---------- General ledger (Step 7a, FR-FIN-001…017) ----------

ACCOUNT_TYPES = [('asset', 'Asset'), ('liability', 'Liability'), ('equity', 'Equity'), ('income', 'Income'), ('expense', 'Expense')]


class Account(models.Model):
    """Chart of accounts. system_key marks the control / posting accounts the engine uses (e.g. 'ar', 'output_cgst')."""
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=ACCOUNT_TYPES)
    group = models.CharField('Schedule III group', max_length=60)
    system_key = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    is_control = models.BooleanField('Control account', default=False, help_text='Posted only by the system from subledgers (no manual journals).')
    is_bank = models.BooleanField('Bank / cash', default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']
        permissions = [('view_financials', 'View trial balance, P&L and balance sheet'),
                       ('access_company_finance', 'Access company-wide accounting and treasury')]

    def __str__(self): return f'{self.code} · {self.name}'

    @property
    def debit_nature(self):
        return self.type in ('asset', 'expense')


class FinancialPeriod(models.Model):
    """Accounting month. Posting into a closed period is refused (FR-FIN-002 / 010)."""
    start = models.DateField(unique=True)
    end = models.DateField()
    status = models.CharField(max_length=6, default='open', choices=[('open', 'Open'), ('closed', 'Closed')])
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-start']
        permissions = [('close_period', 'Close and reopen accounting periods')]

    def __str__(self): return f'{self.start:%b %Y}'


class JournalEntry(models.Model):
    """Balanced double-entry voucher. System entries are idempotent by source; manual vouchers need approval by a different user."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    date = models.DateField()
    site = models.ForeignKey(Site, null=True, blank=True, on_delete=models.PROTECT, related_name='journals', verbose_name='Plant / branch')
    source = models.CharField(max_length=60, unique=True, null=True, blank=True, editable=False, help_text='Document that created it, e.g. inv:12.')
    kind = models.CharField(max_length=7, choices=[('auto', 'System posting'), ('manual', 'Journal voucher'), ('yearend', 'Year-end carry-forward')], default='auto')
    status = models.CharField(max_length=10, default='posted', choices=[('draft', 'Waiting approval'), ('posted', 'Posted'), ('rejected', 'Rejected')])
    narration = models.CharField(max_length=250)
    year_close = models.ForeignKey('FinancialYearClose', null=True, blank=True, on_delete=models.PROTECT, related_name='entries')
    reversal_of = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversed_by')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['-date', '-pk']
        verbose_name_plural = 'journal entries'
        permissions = [('approve_journalentry', 'Approve journal vouchers')]

    def __str__(self): return self.number

    @property
    def total(self):
        return sum((l.debit for l in self.lines.all()), Decimal('0'))


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='lines')
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='journal_lines', help_text='Customer / supplier subledger.')
    debit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    narration = models.CharField(max_length=150, blank=True)

    class Meta:
        ordering = ['pk']
        constraints = [models.CheckConstraint(condition=(models.Q(debit__gt=0, credit=0) | models.Q(credit__gt=0, debit=0)), name='journal_line_one_side')]


# ---------- Payments, bank reconciliation, fixed assets (Step 7b/7c, FR-FIN-007/008/012) ----------

PAYMENT_MODES = [('neft', 'NEFT'), ('rtgs', 'RTGS'), ('cheque', 'Cheque'), ('upi', 'UPI / IMPS')]
PAYMENT_STATUSES = [('proposed', 'Waiting approval'), ('approved', 'Approved — to pay'), ('paid', 'Paid'), ('failed', 'Failed / returned'), ('cancelled', 'Cancelled')]


class SupplierPayment(models.Model):
    """Payment to a supplier or transporter against their approved bills: proposal → approval (different user) → paid with UTR."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    payee = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='payments')
    bank = models.ForeignKey('Account', on_delete=models.PROTECT, related_name='payments', limit_choices_to={'is_bank': True})
    date = models.DateField(default=timezone.localdate)
    mode = models.CharField(max_length=6, choices=PAYMENT_MODES, default='neft')
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    beneficiary_name = models.CharField(max_length=120, blank=True, editable=False)
    beneficiary_account = models.CharField(max_length=18, blank=True, editable=False)
    beneficiary_ifsc = models.CharField(max_length=11, blank=True, editable=False)
    reference = models.CharField('UTR / cheque no.', max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=PAYMENT_STATUSES, default='proposed')
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    paid_at = models.DateTimeField(null=True, blank=True)
    returned_on = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_supplierpayment', 'Approve supplier payments'), ('release_supplierpayment', 'Record payment release (UTR) and failures')]

    def __str__(self): return self.number

    @property
    def badge(self):
        return {'proposed': 'submitted', 'approved': 'approved', 'paid': 'completed'}.get(self.status, 'cancelled')


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(SupplierPayment, on_delete=models.CASCADE, related_name='allocations')
    supplier_invoice = models.ForeignKey('SupplierInvoice', null=True, blank=True, on_delete=models.PROTECT, related_name='allocations')
    freight_bill = models.ForeignKey('FreightBill', null=True, blank=True, on_delete=models.PROTECT, related_name='allocations')
    fixed_asset = models.ForeignKey('FixedAsset', null=True, blank=True, on_delete=models.PROTECT, related_name='allocations')
    debit_note = models.ForeignKey('DebitNote', null=True, blank=True, on_delete=models.PROTECT, related_name='allocations')
    amount = models.DecimalField(max_digits=16, decimal_places=2, help_text='Negative for a debit note adjusted in the payment.')

    class Meta:
        constraints = [models.CheckConstraint(
            condition=(
                models.Q(supplier_invoice__isnull=False, freight_bill__isnull=True, debit_note__isnull=True, fixed_asset__isnull=True, amount__gt=0)
                | models.Q(supplier_invoice__isnull=True, freight_bill__isnull=False, debit_note__isnull=True, fixed_asset__isnull=True, amount__gt=0)
                | models.Q(supplier_invoice__isnull=True, freight_bill__isnull=True, debit_note__isnull=True, fixed_asset__isnull=False, amount__gt=0)
                | models.Q(supplier_invoice__isnull=True, freight_bill__isnull=True, debit_note__isnull=False, fixed_asset__isnull=True, amount__lt=0)
            ), name='payment_allocation_one_source')]


class BankStatementLine(models.Model):
    """Imported bank statement row, matched to a ledger line on the same bank account (FR-FIN-008)."""
    bank = models.ForeignKey('Account', on_delete=models.PROTECT, related_name='statement_lines', limit_choices_to={'is_bank': True})
    date = models.DateField()
    description = models.CharField(max_length=200)
    reference = models.CharField(max_length=60, blank=True)
    withdrawal = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    deposit = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    matched_line = models.OneToOneField('JournalLine', null=True, blank=True, on_delete=models.SET_NULL, related_name='statement_line')
    matched_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    matched_at = models.DateTimeField(null=True, blank=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-pk']
        constraints = [models.UniqueConstraint(fields=['bank', 'date', 'description', 'reference', 'withdrawal', 'deposit'], name='unique_statement_row'),
                       models.CheckConstraint(condition=(models.Q(withdrawal__gt=0, deposit=0) | models.Q(deposit__gt=0, withdrawal=0)), name='statement_one_positive_side')]

    @property
    def amount(self):
        return self.deposit - self.withdrawal


ASSET_CATEGORIES = [('fa_plant', 'Plant & machinery'), ('fa_building', 'Buildings'), ('fa_vehicle', 'Vehicles'), ('fa_office', 'Furniture & office equipment')]


class FixedAsset(models.Model):
    """Asset register entry with depreciation policy (FR-FIN-012). Monthly depreciation and disposal post to the ledger."""
    code = models.CharField('Asset code', max_length=30, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=12, choices=ASSET_CATEGORIES)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='assets', verbose_name='Location')
    location = models.CharField('Location detail', max_length=100, blank=True)
    supplier = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='assets')
    credit_purchase = models.BooleanField(default=False, editable=False)
    put_to_use = models.DateField('Put to use on')
    cost = models.DecimalField(max_digits=16, decimal_places=2, validators=POSITIVE)
    salvage = models.DecimalField('Residual value', max_digits=16, decimal_places=2, default=0, validators=MONEY)
    method = models.CharField(max_length=3, choices=[('slm', 'Straight line'), ('wdv', 'Written down value')], default='slm')
    life_years = models.DecimalField('Useful life (years)', max_digits=5, decimal_places=2, null=True, blank=True)
    wdv_rate = models.DecimalField('WDV rate % per year', max_digits=5, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=9, default='active', choices=[('active', 'In use'), ('disposed', 'Disposed')])
    disposed_on = models.DateField(null=True, blank=True)
    proceeds = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']
        permissions = [('depreciate_fixedasset', 'Run depreciation and dispose assets')]

    def __str__(self): return f'{self.code} · {self.name}'

    @property
    def accumulated(self):
        return self.depreciation.aggregate(t=models.Sum('amount'))['t'] or Decimal('0')

    @property
    def book_value(self):
        return self.cost - self.accumulated


class DepreciationEntry(models.Model):
    asset = models.ForeignKey(FixedAsset, on_delete=models.PROTECT, related_name='depreciation')
    month = models.DateField(help_text='First day of the month depreciated.')
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    journal = models.ForeignKey('JournalEntry', null=True, blank=True, on_delete=models.PROTECT, related_name='+')

    class Meta:
        ordering = ['month']
        constraints = [models.UniqueConstraint(fields=['asset', 'month'], name='one_depreciation_per_month')]


class PeriodCloseReview(models.Model):
    period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name='reviews')
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    prepared_at = models.DateTimeField(auto_now=True)
    tax_reviewed = models.BooleanField(default=False)
    stock_reviewed = models.BooleanField(default=False)
    bank_reviewed = models.BooleanField(default=False)
    note = models.TextField()
    snapshot = models.JSONField(default=dict)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        permissions = [('prepare_periodclose', 'Prepare month-end close review')]


class PeriodBankConfirmation(models.Model):
    period = models.ForeignKey(FinancialPeriod, on_delete=models.PROTECT, related_name='bank_confirmations')
    bank = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='+')
    closing_balance = models.DecimalField(max_digits=16, decimal_places=2)
    statement_reference = models.CharField(max_length=200)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    prepared_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['period', 'bank'], name='one_period_bank_confirmation')]


class FinancialYearClose(models.Model):
    start = models.DateField(unique=True)
    end = models.DateField()
    status = models.CharField(max_length=16, default='proposed', choices=[('proposed', 'Waiting approval'), ('closed', 'Closed'), ('reopen_requested', 'Reopen requested'), ('reopened', 'Reopened'), ('rejected', 'Rejected')])
    revision = models.PositiveIntegerField(default=1)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    prepared_at = models.DateTimeField(auto_now=True)
    note = models.TextField()
    snapshot = models.JSONField(default=dict)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    reopen_requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    reopen_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-start']
        permissions = [('prepare_yearclose', 'Prepare year-end carry-forward'), ('approve_yearclose', 'Approve year-end close and reopen')]

    def __str__(self):
        return f'{self.start:%d %b %Y} – {self.end:%d %b %Y}'


class FinancePostingLock(models.Model):
    """Single transactional lock shared by posting, reconciliation and close."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)


class FinancialYearCloseHistory(models.Model):
    year_close = models.ForeignKey(FinancialYearClose, on_delete=models.PROTECT, related_name='history')
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    note = models.TextField()
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


# ---------- Dealer CRM and onboarding ----------

def default_dealer_kyc():
    return ['pan_card', 'dealership_agreement']


class CRMPolicy(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    required_documents = models.JSONField(default=default_dealer_kyc)
    require_gstin = models.BooleanField(default=False)
    revision = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [('manage_crmpolicy', 'Configure dealer onboarding policy')]


LEAD_STAGES = [('new', 'New'), ('contacted', 'Contacted'), ('qualified', 'Qualified'), ('proposal', 'Proposal / review'), ('won', 'Converted'), ('lost', 'Lost')]
LEAD_SOURCES = [('direct', 'Direct enquiry'), ('referral', 'Referral'), ('visit', 'Field visit'), ('campaign', 'Campaign'), ('influencer', 'Mason / contractor referral'), ('other', 'Other')]


class CRMLead(models.Model):
    number = models.CharField(max_length=25, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='crm_leads')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='crm_leads')
    business_name = models.CharField(max_length=120)
    contact_name = models.CharField(max_length=100, blank=True)
    mobile = models.CharField(max_length=10)
    email = models.EmailField(blank=True)
    kind = models.CharField(max_length=15, choices=[(k, k) for k in CUSTOMER_KINDS], default='Dealer')
    source = models.CharField(max_length=15, choices=LEAD_SOURCES, default='direct')
    district = models.CharField(max_length=80, blank=True)
    territory = models.CharField(max_length=100, blank=True)
    stage = models.CharField(max_length=12, choices=LEAD_STAGES, default='new')
    next_follow_up = models.DateField(null=True, blank=True)
    estimated_value = models.DecimalField(max_digits=16, decimal_places=2, default=0, validators=MONEY)
    notes = models.TextField(blank=True)
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='crm_leads')
    closed_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['site', 'mobile'], name='unique_lead_mobile_per_site')]
        permissions = [('view_team_crm', 'View CRM team records at assigned sites'), ('assign_crmlead', 'Reassign CRM lead owners'), ('export_crmlead', 'Export scoped CRM pipeline')]

    def __str__(self): return self.number


class CRMActivity(models.Model):
    lead = models.ForeignKey(CRMLead, on_delete=models.PROTECT, related_name='activities')
    kind = models.CharField(max_length=12, choices=[('call', 'Call'), ('visit', 'Visit'), ('meeting', 'Meeting'), ('email', 'Email'), ('note', 'Note')])
    happened_on = models.DateField(default=timezone.localdate)
    outcome = models.CharField(max_length=150)
    note = models.TextField(blank=True)
    next_follow_up = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-happened_on', '-created_at']


APPLICATION_STATES = [('draft', 'Draft'), ('submitted', 'KYC review'), ('changes', 'Changes requested'), ('kyc', 'Finance review'), ('credit', 'Final approval'), ('approved', 'Approved'), ('rejected', 'Rejected')]


class DealerApplication(models.Model):
    number = models.CharField(max_length=25, unique=True, editable=False)
    lead = models.ForeignKey(CRMLead, on_delete=models.PROTECT, related_name='applications')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='dealer_applications')
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='dealer_applications')
    profile = models.JSONField(default=dict)
    base_partner_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
    status = models.CharField(max_length=12, choices=APPLICATION_STATES, default='draft')
    revision = models.PositiveIntegerField(default=0)
    submission_snapshot = models.JSONField(default=dict, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    kyc_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    credit_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    approved_credit = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    approved_terms_days = models.PositiveSmallIntegerField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at', '-pk']
        permissions = [('submit_dealerapplication', 'Submit dealer onboarding / amendment'), ('review_dealerkyc', 'Review dealer KYC evidence'),
                       ('review_dealercredit', 'Review dealer credit and terms'), ('approve_dealerapplication', 'Activate dealer / approve amendment')]
        constraints = [models.UniqueConstraint(fields=['lead'], condition=models.Q(status__in=['draft', 'submitted', 'changes', 'kyc', 'credit']), name='one_open_dealer_application')]

    def __str__(self): return self.number


class DealerDocument(models.Model):
    application = models.ForeignKey(DealerApplication, on_delete=models.PROTECT, related_name='documents')
    doc_type = models.CharField(max_length=30, choices=PARTNER_DOCUMENT_TYPES)
    version = models.PositiveIntegerField()
    is_current = models.BooleanField(default=True)
    number = models.CharField(max_length=60, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    file = models.BinaryField(editable=False)
    file_name = models.CharField(max_length=150)
    content_type = models.CharField(max_length=40)
    size = models.PositiveIntegerField()
    digest = models.CharField(max_length=64, editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['doc_type', '-version']
        constraints = [models.UniqueConstraint(fields=['application', 'doc_type', 'version'], name='unique_dealer_document_version'),
                       models.UniqueConstraint(fields=['application', 'doc_type'], condition=models.Q(is_current=True), name='one_current_dealer_document')]


class DealerReview(models.Model):
    application = models.ForeignKey(DealerApplication, on_delete=models.PROTECT, related_name='reviews')
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    note = models.TextField()
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class SalesTarget(models.Model):
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='sales_targets')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    month = models.DateField()
    sales_amount = models.DecimalField('Net taxable sales target', max_digits=16, decimal_places=2, validators=MONEY)
    new_dealers = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month', 'site__code', 'owner__username']
        constraints = [models.UniqueConstraint(fields=['site', 'owner', 'month'], name='unique_monthly_sales_target')]
        permissions = [('manage_salestarget', 'Set salesperson monthly targets'), ('view_crmperformance', 'View scoped sales performance')]


# ---------- Versioned dealer incentives: rules and retained calculation previews ----------
class IncentiveScheme(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='incentive_schemes')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def __str__(self): return f'{self.code} · {self.name}'


class SchemeVersion(models.Model):
    scheme = models.ForeignKey(IncentiveScheme, on_delete=models.PROTECT, related_name='versions')
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=12, choices=[('draft', 'Draft'), ('submitted', 'Review pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='draft')
    starts_on = models.DateField()
    ends_on = models.DateField()
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    customer_kind = models.CharField(max_length=15, blank=True, choices=[(k, k) for k in CUSTOMER_KINDS])
    district = models.CharField(max_length=80, blank=True)
    territory = models.CharField(max_length=100, blank=True)
    basis = models.CharField(max_length=12, choices=[('net_sales', 'Net taxable sales'), ('settled', 'Fully settled net taxable sales')], default='net_sales')
    dealer_cap = models.DecimalField('Maximum incentive per dealer (₹)', max_digits=16, decimal_places=2, null=True, blank=True, validators=MONEY)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decision_note = models.TextField(blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    contributors = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='+', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-revision']
        constraints = [models.UniqueConstraint(fields=['scheme', 'revision'], name='unique_incentive_revision'),
                       models.UniqueConstraint(fields=['scheme'], condition=models.Q(status__in=['draft', 'submitted']), name='one_open_incentive_revision')]
        permissions = [('submit_schemeversion', 'Submit incentive scheme rules'), ('approve_schemeversion', 'Approve incentive scheme rules')]

    def __str__(self): return f'{self.scheme.code} · v{self.revision}'


class SchemeSlab(models.Model):
    version = models.ForeignKey(SchemeVersion, on_delete=models.CASCADE, related_name='slabs')
    minimum_sales = models.DecimalField('Minimum eligible sales (₹)', max_digits=16, decimal_places=2, validators=MONEY)
    percentage = models.DecimalField('Incentive (%)', max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])

    class Meta:
        ordering = ['minimum_sales']
        constraints = [models.UniqueConstraint(fields=['version', 'minimum_sales'], name='unique_incentive_slab')]


class SchemeEvaluation(models.Model):
    version = models.ForeignKey(SchemeVersion, on_delete=models.PROTECT, related_name='evaluations')
    starts_on = models.DateField()
    ends_on = models.DateField()
    submission_key = models.UUIDField(unique=True, editable=False)
    snapshot = models.JSONField(default=dict, editable=False)
    source_digest = models.CharField(max_length=64, editable=False)
    total = models.DecimalField(max_digits=16, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        permissions = [('export_schemeevaluation', 'Export incentive calculation previews')]

    def __str__(self): return f'CALC-{self.pk:06d}'


# ---------- Manufacturing partners: supply contracts (asset-light model) ----------

class SupplyContract(models.Model):
    """Agreed rate for a SISTECH product made at a partner plant. Approved by a different user; used on POs to that plant
    and to choose the cheapest source for a dealer order (contract rate + freight to the dealer's district)."""
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='supply_contracts', limit_choices_to={'manufacturing_partner': True})
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='supply_contracts', verbose_name='Partner plant')
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='supply_contracts')
    rate = models.DecimalField('Rate (₹ per unit, before GST)', max_digits=14, decimal_places=2, validators=POSITIVE)
    monthly_quantity = models.DecimalField('Monthly commitment', max_digits=14, decimal_places=3, default=0, validators=MONEY,
                                           help_text="Quantity per month in the product's unit the partner commits to supply. 0 = no commitment.")
    valid_from = models.DateField(default=timezone.localdate)
    valid_to = models.DateField(null=True, blank=True)
    reference = models.CharField('Agreement ref.', max_length=60, blank=True)
    status = models.CharField(max_length=9, default='draft', choices=[('draft', 'Waiting approval'), ('approved', 'Approved'), ('ended', 'Ended'), ('rejected', 'Rejected')])
    notes = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['site__code', 'product__name', '-valid_from']
        permissions = [('approve_supplycontract', 'Approve manufacturing partner rate contracts')]

    def __str__(self): return f'{self.partner} · {self.product.code} @ ₹{self.rate}'

    def is_current(self, on=None):
        on = on or timezone.localdate()
        return self.status == 'approved' and self.valid_from <= on and (self.valid_to is None or self.valid_to >= on)


# ---------- Scheme entitlements and settlement (Step 8b stage 2, FR-INC-005…010) ----------

class SchemeEntitlementBatch(models.Model):
    """Turns one retained full-period calculation into dealer entitlements after independent approval; approval accrues them."""
    evaluation = models.OneToOneField(SchemeEvaluation, on_delete=models.PROTECT, related_name='batch')
    version = models.ForeignKey(SchemeVersion, on_delete=models.PROTECT, related_name='batches')
    status = models.CharField(max_length=9, default='submitted', choices=[('submitted', 'Waiting approval'), ('approved', 'Approved & accrued'), ('rejected', 'Rejected')])
    total = models.DecimalField(max_digits=16, decimal_places=2)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_schemeentitlementbatch', 'Approve scheme entitlements (accrual)')]

    def __str__(self): return f'ENT-{self.pk:05d}'


class SchemeEntitlement(models.Model):
    """What one dealer earned under one scheme version. Amount = this calculation − amounts already approved for the same
    dealer and version, so a later recalculation (e.g. after returns) adds only the difference, never a second earning."""
    batch = models.ForeignKey(SchemeEntitlementBatch, on_delete=models.CASCADE, related_name='entitlements')
    version = models.ForeignKey(SchemeVersion, on_delete=models.PROTECT, related_name='entitlements')
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='scheme_entitlements')
    eligible = models.DecimalField('Eligible sales (₹)', max_digits=16, decimal_places=2)
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    calculated = models.DecimalField(max_digits=16, decimal_places=2)
    prior = models.DecimalField('Already approved', max_digits=16, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    settlement = models.ForeignKey('SchemeSettlement', null=True, blank=True, on_delete=models.SET_NULL, related_name='entitlements')

    class Meta:
        ordering = ['partner__name']


class SchemeSettlement(models.Model):
    """Pays a dealer's accrued scheme entitlements: as a credit to the dealer account (reduces outstanding) or by bank transfer."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='scheme_settlements')
    mode = models.CharField(max_length=6, choices=[('ledger', 'Credit to dealer account'), ('bank', 'Bank transfer')], default='ledger')
    bank = models.ForeignKey('Account', null=True, blank=True, on_delete=models.PROTECT, related_name='+', limit_choices_to={'is_bank': True})
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    date = models.DateField(default=timezone.localdate)
    reference = models.CharField('UTR / reference', max_length=40, blank=True)
    status = models.CharField(max_length=9, default='proposed', choices=[('proposed', 'Waiting approval'), ('posted', 'Posted'), ('cancelled', 'Cancelled')])
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_schemesettlement', 'Approve scheme payouts')]

    def __str__(self): return self.number


# ---------- Star programs: verified influencer usage and annual recognition (sistech.co.in) ----------

class InfluencerUsage(models.Model):
    """SISTECH bags a mason / engineer / contractor bought through a dealer. Counts for recognition only after independent verification."""
    influencer = models.ForeignKey(Influencer, on_delete=models.PROTECT, related_name='usages')
    dealer = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='influencer_usages', verbose_name='Bought from (dealer)')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    bags = models.PositiveIntegerField()
    date = models.DateField(default=timezone.localdate)
    site_location = models.CharField('Construction site', max_length=150, blank=True)
    bill_reference = models.CharField("Dealer's bill / slip no.", max_length=40)
    status = models.CharField(max_length=9, default='pending', choices=[('pending', 'Waiting verification'), ('verified', 'Verified'), ('rejected', 'Rejected')])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    verified_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-date', '-pk']
        constraints = [models.UniqueConstraint(fields=['dealer', 'bill_reference', 'influencer'], condition=~models.Q(status='rejected'), name='one_usage_per_bill')]
        permissions = [('verify_influencerusage', 'Verify influencer usage for recognition')]


RECOGNITION_AUDIENCES = [('dealer', 'Star Dealer'), ('engineer', 'Star Engineer'), ('mason', 'Star Mason & Contractor')]
RECOGNITION_LEVELS = [('national', 'National'), ('state', 'State'), ('district', 'District')]


class RecognitionProgram(models.Model):
    """Annual Star programme: participants are ranked on verified volume and the best become district, state or national winners."""
    name = models.CharField(max_length=120)
    audience = models.CharField(max_length=8, choices=RECOGNITION_AUDIENCES)
    starts_on = models.DateField()
    ends_on = models.DateField()
    exclude_overdue_days = models.PositiveSmallIntegerField('Exclude dealers overdue beyond (days)', default=90,
                                                            help_text='Dealers with an invoice overdue longer than this at ranking time are not eligible. 0 = no check.')
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, default='open', choices=[('open', 'Open'), ('ranked', 'Ranking waiting approval'), ('published', 'Winners published')])
    contributors = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='+', blank=True)
    ranked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    ranked_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    published_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-starts_on', 'audience']
        permissions = [('publish_recognitionprogram', 'Approve and publish recognition winners')]

    def __str__(self): return self.name

    @property
    def unit(self):
        return 'MT' if self.audience == 'dealer' else 'bags'


class RecognitionLevel(models.Model):
    program = models.ForeignKey(RecognitionProgram, on_delete=models.CASCADE, related_name='levels')
    level = models.CharField(max_length=8, choices=RECOGNITION_LEVELS)
    winners = models.PositiveSmallIntegerField('Winners per area', default=1)
    minimum = models.DecimalField('Minimum volume', max_digits=14, decimal_places=3, default=0, validators=MONEY)
    reward = models.CharField(max_length=120, help_text='e.g. Domestic couple trip')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['program', 'level'], name='one_rule_per_level')]


class RecognitionAward(models.Model):
    program = models.ForeignKey(RecognitionProgram, on_delete=models.CASCADE, related_name='awards')
    level = models.CharField(max_length=8, choices=RECOGNITION_LEVELS)
    area = models.CharField(max_length=80)
    rank = models.PositiveSmallIntegerField()
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='recognitions')
    influencer = models.ForeignKey(Influencer, null=True, blank=True, on_delete=models.PROTECT, related_name='recognitions')
    volume = models.DecimalField(max_digits=14, decimal_places=3)
    reward = models.CharField(max_length=120)
    fulfilled = models.BooleanField(default=False)
    fulfilled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    fulfilment_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['level', 'area', 'rank']

    @property
    def participant(self):
        return self.partner or self.influencer


# ---------- Quotations (Step 8c, FR-SLS-002) ----------

QUOTE_STATUSES = [('draft', 'Draft'), ('approval', 'Discount approval pending'), ('ready', 'Ready to send'), ('sent', 'Sent'),
                  ('accepted', 'Accepted'), ('rejected', 'Declined'), ('expired', 'Expired'), ('converted', 'Converted to order'), ('revised', 'Revised')]


class Quotation(models.Model):
    """Price offer to a customer or CRM prospect. Below-list discounts need approval by a different user before sending;
    any change after sending is a new revision. An accepted quotation becomes a draft sales order."""
    number = models.CharField(max_length=20, editable=False)
    revision = models.PositiveSmallIntegerField(default=1, editable=False)
    previous = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='next_revision', editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='quotations', verbose_name='Supplying plant / depot')
    partner = models.ForeignKey(Partner, null=True, blank=True, on_delete=models.PROTECT, related_name='quotations', verbose_name='Customer')
    lead = models.ForeignKey('CRMLead', null=True, blank=True, on_delete=models.PROTECT, related_name='quotations', verbose_name='Prospect (CRM lead)')
    date = models.DateField(default=timezone.localdate)
    valid_until = models.DateField()
    place_of_supply = models.CharField(max_length=2, choices=INDIAN_STATES)
    freight_terms = models.CharField(max_length=16, choices=FREIGHT_TERMS, default='for_destination')
    delivery_terms = models.CharField(max_length=150, blank=True)
    payment_terms = models.CharField(max_length=150, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=QUOTE_STATUSES, default='draft')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approval_note = models.CharField(max_length=200, blank=True)
    outcome_note = models.CharField(max_length=200, blank=True)
    order = models.OneToOneField(Order, null=True, blank=True, on_delete=models.PROTECT, related_name='quotation', editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['number', 'revision'], name='unique_quotation_revision')]
        permissions = [('approve_quotation', 'Approve below-list quotation prices')]

    def __str__(self): return f'{self.number} rev {self.revision}'

    @property
    def customer_name(self):
        return self.partner.name if self.partner_id else (self.lead.business_name if self.lead_id else '')

    @property
    def interstate(self):
        return bool(self.site.state and self.place_of_supply and self.site.state != self.place_of_supply)

    @property
    def badge(self):
        return {'draft': 'draft', 'approval': 'submitted', 'ready': 'approved', 'sent': 'approved', 'accepted': 'completed', 'converted': 'completed'}.get(self.status, 'cancelled')


class QuotationLine(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=POSITIVE)
    list_rate = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    rate = models.DecimalField('Offered rate', max_digits=14, decimal_places=2, validators=MONEY)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    hsn = models.CharField(max_length=10, blank=True)

    @property
    def below_list(self):
        return self.list_rate is not None and self.rate < self.list_rate

    @property
    def discount_pct(self):
        return ((self.list_rate - self.rate) / self.list_rate * 100).quantize(Decimal('0.1')) if self.list_rate else None


# ---------- Complaints, returns and CAPA (Step 8d, FR-QC-007/008, SRS §5.12) ----------

COMPLAINT_CATEGORIES = [('quality', 'Quality (setting, strength, lumps, colour)'), ('short', 'Short quantity / weight'), ('damage', 'Damaged / wet / torn bags'),
                        ('delay', 'Late delivery'), ('billing', 'Billing / rate'), ('other', 'Other')]
COMPLAINT_SLA_DAYS = {'quality': 7, 'short': 3, 'damage': 3, 'delay': 2, 'billing': 5, 'other': 5}
COMPLAINT_STATUSES = [('open', 'Open'), ('investigating', 'Under investigation'), ('decided', 'Decided — action pending'), ('closed', 'Closed')]


class Complaint(models.Model):
    """Customer claim linked to invoice, truck and batch. Investigated, decided by a different user, settled (credit note / replacement)
    and closed — quality complaints only after CAPA is verified effective or a reason is recorded why none is needed."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='complaints', verbose_name='Customer')
    invoice = models.ForeignKey('Invoice', null=True, blank=True, on_delete=models.PROTECT, related_name='complaints')
    dispatch = models.ForeignKey('Dispatch', null=True, blank=True, on_delete=models.PROTECT, related_name='complaints', verbose_name='Truck / dispatch')
    batch = models.ForeignKey('Production', null=True, blank=True, on_delete=models.PROTECT, related_name='complaints')
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    category = models.CharField(max_length=8, choices=COMPLAINT_CATEGORIES)
    description = models.TextField()
    quantity = models.DecimalField('Quantity affected', max_digits=14, decimal_places=3, default=0, validators=MONEY)
    site_location = models.CharField('Where (customer site)', max_length=150, blank=True)
    received_on = models.DateField(default=timezone.localdate)
    due_on = models.DateField('Resolve by (SLA)', editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='Investigation owner')
    status = models.CharField(max_length=13, choices=COMPLAINT_STATUSES, default='open')
    findings = models.TextField(blank=True)
    decision = models.CharField(max_length=8, blank=True, choices=[('accept', 'Accepted'), ('partial', 'Partly accepted'), ('reject', 'Rejected')])
    accepted_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    resolution = models.CharField(max_length=10, blank=True, choices=[('credit', 'Credit note'), ('replace', 'Replacement supply'), ('none', 'No settlement')])
    decision_note = models.CharField(max_length=300, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    credit_note = models.ForeignKey('CreditNote', null=True, blank=True, on_delete=models.PROTECT, related_name='complaints')
    replacement_order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name='complaints')
    capa_waiver = models.CharField('Why no CAPA is needed', max_length=200, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-received_on', '-pk']
        permissions = [('decide_complaint', 'Decide complaints and close them')]

    def __str__(self): return self.number

    @property
    def overdue(self):
        return self.status != 'closed' and self.due_on < timezone.localdate()

    @property
    def badge(self):
        return {'open': 'submitted', 'investigating': 'approved', 'decided': 'approved', 'closed': 'completed'}[self.status]


class CapaAction(models.Model):
    """Corrective and preventive action (FR-QC-008): root cause → actions → implemented → effectiveness verified by a different user."""
    complaint = models.ForeignKey(Complaint, null=True, blank=True, on_delete=models.PROTECT, related_name='capas')
    title = models.CharField(max_length=150)
    root_cause = models.TextField()
    containment = models.TextField('Containment (immediate action)', blank=True)
    corrective = models.TextField('Corrective action')
    preventive = models.TextField('Preventive action', blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    due_on = models.DateField()
    status = models.CharField(max_length=9, default='open', choices=[('open', 'Open'), ('done', 'Implemented — verify'), ('effective', 'Verified effective'), ('reopened', 'Not effective — reopened')])
    implementation_note = models.CharField(max_length=300, blank=True)
    implemented_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    verified_at = models.DateTimeField(null=True, blank=True)
    effectiveness_note = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_on']
        permissions = [('verify_capaaction', 'Verify CAPA effectiveness')]

    def __str__(self): return f'CAPA-{self.pk:05d} · {self.title}'

    @property
    def overdue(self):
        return self.status in ('open', 'reopened') and self.due_on < timezone.localdate()


# ---------- Help & support tickets ----------

TICKET_CATEGORIES = [('problem', 'Something is not working'), ('howto', 'How do I…?'), ('access', 'Access / password / role'),
                     ('data', 'Wrong data — needs correction'), ('feature', 'New feature or report'), ('other', 'Other')]
TICKET_PRIORITIES = [('low', 'Low'), ('normal', 'Normal'), ('high', 'High — work is slowed'), ('urgent', 'Urgent — work is stopped')]
TICKET_SLA_HOURS = {'urgent': 4, 'high': 24, 'normal': 72, 'low': 120}
TICKET_STATUSES = [('open', 'Open'), ('progress', 'In progress'), ('waiting', 'Waiting for your reply'), ('resolved', 'Resolved'), ('closed', 'Closed')]


class SupportTicket(models.Model):
    """Help request from any ERP user to the support team: priority-based SLA, assignment, conversation, resolution and rating."""
    number = models.CharField(max_length=20, unique=True, editable=False)
    subject = models.CharField(max_length=150)
    category = models.CharField(max_length=8, choices=TICKET_CATEGORIES, default='problem')
    priority = models.CharField(max_length=6, choices=TICKET_PRIORITIES, default='normal')
    module = models.CharField('Screen / module', max_length=80, blank=True)
    page_url = models.CharField(max_length=300, blank=True, editable=False)
    status = models.CharField(max_length=8, choices=TICKET_STATUSES, default='open')
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    due_at = models.DateTimeField(editable=False)
    first_response_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolution = models.CharField(max_length=300, blank=True, editable=False)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        permissions = [('handle_supportticket', 'Answer, assign and resolve support tickets of all users')]

    def __str__(self): return self.number

    @property
    def overdue(self):
        return self.status in ('open', 'progress') and self.due_at < timezone.now()

    @property
    def badge(self):
        return {'open': 'submitted', 'progress': 'approved', 'waiting': 'draft', 'resolved': 'completed', 'closed': 'completed'}[self.status]


class TicketMessage(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    body = models.TextField()
    internal = models.BooleanField('Internal note (support team only)', default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


# ---------- Collections: follow-ups, promise-to-pay, disputes (FR-AR-007) ----------

FOLLOWUP_KINDS = [('call', 'Phone call'), ('visit', 'Visit'), ('promise', 'Promise to pay'), ('dispute', 'Dispute raised'), ('reminder', 'Reminder sent')]
DISPUTE_REASONS = [('', '—'), ('rate', 'Rate / discount'), ('short', 'Short supply'), ('quality', 'Quality'), ('scheme', 'Scheme / incentive not given'),
                   ('duplicate', 'Duplicate / wrong invoice'), ('other', 'Other')]


class CollectionFollowUp(models.Model):
    """Every collection contact with a customer. A promise is 'kept' when receipts dated from the promise up to the promised date
    reach the promised amount; it is 'broken' once the date passes without that. Disputes stay open until resolved."""
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='followups', limit_choices_to={'kind__in': CUSTOMER_KINDS})
    kind = models.CharField(max_length=8, choices=FOLLOWUP_KINDS)
    on = models.DateField('Date', default=timezone.localdate)
    note = models.CharField(max_length=300)
    promised_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, validators=POSITIVE)
    promised_date = models.DateField(null=True, blank=True)
    dispute_reason = models.CharField(max_length=10, blank=True, choices=DISPUTE_REASONS)
    resolved = models.BooleanField(default=False)
    resolution = models.CharField(max_length=200, blank=True)
    next_action_on = models.DateField('Next follow-up', null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-on', '-created_at']

    def __str__(self): return f'{self.partner} · {self.get_kind_display()} · {self.on}'


# ---------- Customer returns / RMA (FR-SVC-004/005, FR-TAX-008) ----------

RETURN_REASONS = [('damaged', 'Damaged / wet / torn bags'), ('wrong', 'Wrong product or grade'), ('quality', 'Quality problem'), ('excess', 'Excess / not ordered'),
                  ('expired', 'Old stock / hardened'), ('other', 'Other')]
RETURN_STATUSES = [('requested', 'Waiting approval'), ('approved', 'Approved · pickup due'), ('rejected', 'Rejected'), ('transit', 'Picked up · on the way back'),
                   ('inspected', 'Inspected · credit due'), ('credited', 'Credited'), ('closed', 'Closed · nothing accepted'), ('cancelled', 'Cancelled')]


class ReturnRequest(models.Model):
    """Return of goods from a customer against one tax invoice: request → approval (different user) → pickup → inspection at the
    plant (good / damaged / not accepted) → credit note by finance. Inspected goods stay in quarantine until the credit note posts
    them back to stock (good → available, damaged → damaged)."""
    submission_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    submission_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
    number = models.CharField(max_length=20, unique=True, editable=False)
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name='returns')
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='returns')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='returns')
    complaint = models.ForeignKey('Complaint', null=True, blank=True, on_delete=models.PROTECT, related_name='returns')
    reason = models.CharField(max_length=8, choices=RETURN_REASONS)
    description = models.CharField(max_length=300)
    status = models.CharField(max_length=10, choices=RETURN_STATUSES, default='requested')
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    decision_note = models.CharField(max_length=200, blank=True)
    pickup_on = models.DateField(null=True, blank=True)
    vehicle_number = models.CharField(max_length=15, blank=True)
    lr_number = models.CharField('LR / challan no.', max_length=30, blank=True)
    inspected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    inspected_at = models.DateTimeField(null=True, blank=True)
    credit_note = models.OneToOneField(CreditNote, null=True, blank=True, on_delete=models.PROTECT, related_name='return_request')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        permissions = [('approve_returnrequest', 'Approve or reject customer returns'), ('inspect_returnrequest', 'Receive and inspect returned goods')]

    def __str__(self): return self.number

    @property
    def badge(self):
        return {'requested': 'submitted', 'approved': 'approved', 'transit': 'approved', 'inspected': 'submitted', 'credited': 'completed', 'closed': 'completed'}.get(self.status, 'cancelled')


class ReturnLine(models.Model):
    request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name='lines')
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name='return_lines')
    quantity = models.DecimalField('Requested qty', max_digits=14, decimal_places=3, validators=POSITIVE)
    received = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    good = models.DecimalField('Good (resaleable)', max_digits=14, decimal_places=3, default=0, validators=MONEY)
    damaged = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    note = models.CharField(max_length=150, blank=True)

    @property
    def not_accepted(self):
        return max(self.received - self.good - self.damaged, Decimal('0'))

    class Meta:
        constraints = [models.UniqueConstraint(fields=['request', 'invoice_line'], name='unique_return_invoice_line')]


# ---------- Sales territories and field visit plans ----------
class SalesTerritory(models.Model):
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='sales_territories')
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=8, choices=[('state', 'State'), ('district', 'District'), ('area', 'Area / beat')])
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    state = models.CharField(max_length=2, choices=INDIAN_STATES, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['site__code', 'code']
        constraints = [models.UniqueConstraint(fields=['site', 'code'], name='unique_territory_site_code')]
        permissions = [('manage_salesterritory', 'Manage sales territories and dated dealer assignments')]

    def __str__(self): return f'{self.site.code} · {self.code} · {self.name}'


class DealerTerritoryAssignment(models.Model):
    territory = models.ForeignKey(SalesTerritory, on_delete=models.PROTECT, related_name='assignments')
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='dealer_territory_assignments')
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name='territory_assignments')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=300)
    end_note = models.CharField(max_length=300, blank=True)
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    superseded_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-starts_on', '-pk']
        constraints = [models.CheckConstraint(condition=models.Q(ends_on__isnull=True) | models.Q(ends_on__gte=models.F('starts_on')), name='territory_assignment_valid_dates')]

    def __str__(self): return f'{self.partner.code} · {self.territory.code} · {self.owner.get_username()}'


class BeatPlan(models.Model):
    number = models.CharField(max_length=25, unique=True, editable=False)
    site = models.ForeignKey(Site, on_delete=models.PROTECT, related_name='beat_plans')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='beat_plans')
    on = models.DateField(default=timezone.localdate)
    title = models.CharField(max_length=120)
    status = models.CharField(max_length=12, default='draft', choices=[('draft','Draft'),('submitted','Review pending'),('approved','Approved'),('finished','Finished'),('cancelled','Cancelled')])
    revision = models.PositiveIntegerField(default=0)
    decision_note = models.CharField(max_length=300, blank=True)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    contributors = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='+', blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-on','-pk']
        constraints = [models.UniqueConstraint(fields=['site','owner','on'], condition=~models.Q(status='cancelled'), name='one_live_daily_beat_plan')]
        permissions = [('view_team_beats','View assigned-site team beat plans'), ('approve_beatplan','Approve or return beat plans'),
                       ('submit_beatplan','Submit beat plans'), ('export_beatplan','Export scoped field visit reports')]

    def __str__(self): return self.number


BEAT_PURPOSES = [('visit','Dealer visit'),('order','Order enquiry'),('collection','Collection follow-up'),('complaint','Complaint follow-up')]
BEAT_OUTCOMES = [('visited','Visit completed'),('enquiry','Order enquiry'),('promise','Payment promise reported'),('complaint','Complaint discussed')]


class BeatStop(models.Model):
    plan = models.ForeignKey(BeatPlan, on_delete=models.CASCADE, related_name='stops')
    assignment = models.ForeignKey(DealerTerritoryAssignment, on_delete=models.PROTECT, related_name='stops')
    sequence = models.PositiveSmallIntegerField()
    purpose = models.CharField(max_length=12, choices=BEAT_PURPOSES, default='visit')
    instructions = models.CharField(max_length=200, blank=True)
    snapshot = models.JSONField(default=dict, blank=True, editable=False)
    status = models.CharField(max_length=10, default='planned', choices=[('planned','Planned'),('completed','Completed'),('missed','Missed'),('reassigned','Reassigned')])
    replacement_stop = models.OneToOneField('self', null=True, blank=True, on_delete=models.PROTECT, related_name='transferred_from', editable=False)
    visited_on = models.DateField(null=True, blank=True)
    outcome = models.CharField(max_length=12, choices=BEAT_OUTCOMES, blank=True)
    note = models.TextField(blank=True)
    next_action_on = models.DateField(null=True, blank=True)
    promised_amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True, validators=POSITIVE)
    promised_on = models.DateField(null=True, blank=True)
    lead = models.ForeignKey(CRMLead, null=True, blank=True, on_delete=models.PROTECT, related_name='beat_stops')
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name='beat_stops')
    complaint = models.ForeignKey(Complaint, null=True, blank=True, on_delete=models.PROTECT, related_name='beat_stops')
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    recorded_at = models.DateTimeField(null=True, blank=True)
    collection_followup = models.OneToOneField(CollectionFollowUp, null=True, blank=True, on_delete=models.PROTECT, related_name='beat_stop')
    followup_note = models.CharField(max_length=300, blank=True)
    followed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    followed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['sequence','pk']
        constraints = [models.CheckConstraint(condition=models.Q(status='reassigned',replacement_stop__isnull=False) | (~models.Q(status='reassigned') & models.Q(replacement_stop__isnull=True)), name='beat_reassigned_stop_lineage'),
                       models.UniqueConstraint(fields=['plan','assignment'],name='unique_beat_assignment_stop'),
                       models.UniqueConstraint(fields=['plan','sequence'],name='unique_beat_stop_sequence')]
        permissions = [('record_beatstop','Record assigned field visit outcomes'), ('publish_beatpromise','Review field promises into collections')]


class BeatReview(models.Model):
    plan = models.ForeignKey(BeatPlan, on_delete=models.PROTECT, related_name='reviews')
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=20)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    note = models.CharField(max_length=300)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at','-pk']


# ---------- Unified construction/project opportunity pipeline ----------
PROJECT_STAGES = [('new','New enquiry'),('qualified','Qualified'),('sampling','Sample / technical review'),('quoted','Quoted'),('negotiation','Negotiation'),('hold','On hold'),('won','Won'),('lost','Lost')]
PROJECT_WEIGHTS = {'new':10,'qualified':30,'sampling':50,'quoted':65,'negotiation':80,'hold':0,'won':100,'lost':0}


class SalesProject(models.Model):
    number = models.CharField(max_length=25,unique=True,editable=False)
    site = models.ForeignKey(Site,on_delete=models.PROTECT,related_name='sales_projects')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='sales_projects')
    name = models.CharField(max_length=150)
    location = models.CharField(max_length=200)
    district = models.CharField(max_length=80)
    contact_name = models.CharField(max_length=100)
    mobile = models.CharField(max_length=10)
    prospect_name = models.CharField('Prospect / buyer name',max_length=150,blank=True)
    partner = models.ForeignKey(Partner,null=True,blank=True,on_delete=models.PROTECT,related_name='sales_projects')
    lead = models.ForeignKey(CRMLead,null=True,blank=True,on_delete=models.PROTECT,related_name='sales_projects')
    referral = models.OneToOneField(InfluencerActivity,null=True,blank=True,on_delete=models.PROTECT,related_name='sales_project')
    source_snapshot = models.JSONField(default=dict,blank=True,editable=False)
    dedupe_key = models.CharField(max_length=64,editable=False)
    stage = models.CharField(max_length=12,choices=PROJECT_STAGES,default='new')
    estimated_value = models.DecimalField('Estimated taxable opportunity (₹)',max_digits=16,decimal_places=2,default=0,validators=MONEY)
    cement_bags = models.PositiveIntegerField('Expected cement bags',default=0)
    tmt_mt = models.DecimalField('Expected TMT (MT)',max_digits=14,decimal_places=3,default=0,validators=MONEY)
    expected_close = models.DateField(null=True,blank=True)
    next_action_on = models.DateField(null=True,blank=True)
    notes = models.TextField(blank=True)
    closed_reason = models.CharField(max_length=500,blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering=['-updated_at','-pk']
        constraints=[models.UniqueConstraint(fields=['site','dedupe_key'],name='unique_site_project_identity')]
        permissions=[('view_team_projects','View assigned-site team projects'),('manage_salesproject','Assign and reopen sales projects'),('export_salesproject','Export scoped project pipeline')]

    def __str__(self):return self.number

    @property
    def buyer_name(self):return self.partner.name if self.partner_id else self.prospect_name


class ProjectActivity(models.Model):
    project = models.ForeignKey(SalesProject,on_delete=models.PROTECT,related_name='activities')
    kind = models.CharField(max_length=12,choices=[('call','Call'),('visit','Site visit'),('sample','Sample / technical review'),('note','Note')])
    on = models.DateField(default=timezone.localdate)
    note = models.TextField()
    next_action_on = models.DateField(null=True,blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:ordering=['-on','-pk']


class ProjectLink(models.Model):
    project = models.ForeignKey(SalesProject,on_delete=models.PROTECT,related_name='links')
    quotation = models.ForeignKey(Quotation,null=True,blank=True,on_delete=models.PROTECT,related_name='project_links')
    order = models.ForeignKey(Order,null=True,blank=True,on_delete=models.PROTECT,related_name='project_links')
    active = models.BooleanField(default=True)
    snapshot = models.JSONField(default=dict,editable=False)
    note = models.CharField(max_length=300)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    unlinked_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT,related_name='+')
    unlinked_at = models.DateTimeField(null=True,blank=True)
    unlink_note = models.CharField(max_length=300,blank=True)

    class Meta:
        ordering=['-created_at','-pk']
        constraints=[models.CheckConstraint(condition=(models.Q(quotation__isnull=False,order__isnull=True)|models.Q(quotation__isnull=True,order__isnull=False)),name='one_project_link_source'),
            models.UniqueConstraint(fields=['quotation'],condition=models.Q(active=True,quotation__isnull=False),name='one_active_project_per_quote'),
            models.UniqueConstraint(fields=['order'],condition=models.Q(active=True,order__isnull=False),name='one_active_project_per_order')]


class ProjectEvent(models.Model):
    project = models.ForeignKey(SalesProject,on_delete=models.PROTECT,related_name='events')
    action = models.CharField(max_length=40)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    note = models.CharField(max_length=500)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:ordering=['-created_at','-pk']


class BeatExecutionCover(models.Model):
    """Retained independent authorization to execute, never rewrite, a route."""
    plan = models.ForeignKey(BeatPlan, on_delete=models.PROTECT, related_name='execution_covers')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    starts_at = models.DateTimeField(default=timezone.now, editable=False)
    ends_at = models.DateTimeField()
    revision = models.PositiveIntegerField()
    evidence = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default='submitted', choices=[('submitted','Review pending'),('approved','Approved'),('rejected','Rejected'),('revoked','Revoked')])
    reason = models.CharField(max_length=300)
    review_note = models.CharField(max_length=300, blank=True)
    revoke_note = models.CharField(max_length=300, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')), name='beat_cover_finite_window')]
        indexes = [models.Index(fields=['recipient','status','ends_at'])]

    def __str__(self):
        return f'Beat cover {self.pk}'


class DealerOwnershipTransfer(models.Model):
    source = models.OneToOneField(DealerTerritoryAssignment, on_delete=models.PROTECT, related_name='outgoing_transfer')
    replacement = models.OneToOneField(DealerTerritoryAssignment, on_delete=models.PROTECT, related_name='incoming_transfer')
    effective_on = models.DateField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=300)
    request_key = models.UUIDField(unique=True)
    payload_digest = models.CharField(max_length=64)
    evidence = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):return f'Dealer transfer {self.pk}'
