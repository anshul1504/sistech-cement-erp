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

class Site(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=15, choices=[('Plant', 'Plant'), ('Depot', 'Depot'), ('Warehouse', 'Warehouse')])
    address = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    def __str__(self): return f'{self.code} · {self.name}'

class SiteAccess(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    site = models.ForeignKey(Site, on_delete=models.CASCADE)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'site'], name='unique_site_access')]

class Product(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=25, choices=[('Cement', 'Finished cement'), ('Raw material', 'Raw material'), ('Packaging', 'Packaging'), ('Fuel', 'Fuel'), ('Spares', 'Spares')])
    unit = models.CharField(max_length=12, choices=[('MT', 'Metric tonnes'), ('BAG', 'Bags'), ('KG', 'Kilograms'), ('PCS', 'Pieces')])
    bag_weight_kg = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True, validators=POSITIVE)
    hsn = models.CharField(max_length=10, blank=True)
    reorder_level = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=MONEY)
    active = models.BooleanField(default=True)
    def __str__(self): return f'{self.code} · {self.name} ({self.unit})'

class Partner(models.Model):
    code = models.CharField(max_length=25, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=15, choices=[('Dealer', 'Dealer / Customer'), ('Supplier', 'Supplier'), ('Transporter', 'Transporter')])
    gstin = models.CharField(max_length=15, blank=True)
    phone = models.CharField(max_length=25, blank=True)
    territory = models.CharField(max_length=100, blank=True)
    address = models.TextField(blank=True)
    credit_limit = models.DecimalField(max_digits=16, decimal_places=2, default=0, validators=MONEY)
    active = models.BooleanField(default=True)
    def __str__(self): return self.name

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
