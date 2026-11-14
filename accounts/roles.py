"""One permission catalogue shared by role editing, presets and tests."""
from django.contrib.auth.models import Group, Permission

ACTIONS = [('view', 'View'), ('create', 'Create'), ('edit', 'Edit'), ('submit', 'Submit'), ('approve', 'Approve'), ('post', 'Post'), ('cancel', 'Cancel'), ('export', 'Export')]
MODULES = [
    ('sales', 'Sales orders', {a: f'{a}_sales_order' for a in ['view', 'create', 'submit', 'approve', 'post', 'cancel']}),
    ('purchase_requests', 'Purchase requests', {'view': 'view_purchaserequest', 'create': 'add_purchaserequest', 'edit': 'change_purchaserequest', 'approve': 'approve_purchaserequest'}),
    ('rfq', 'RFQs & quotes', {'view': 'view_rfq', 'create': 'add_rfq', 'approve': 'award_rfq'}),
    ('purchase', 'Purchase orders', {a: f'{a}_purchase_order' for a in ['view', 'create', 'submit', 'approve', 'post', 'cancel']}),
    ('purchase_high', 'High-value PO approval', {'approve': 'approve_high_value_purchase'}),
    ('company_finance', 'Company-wide finance access', {'view': 'access_company_finance'}),
    ('ledger', 'Journals & vouchers', {'view': 'view_journalentry', 'create': 'add_journalentry', 'approve': 'approve_journalentry'}),
    ('financials', 'Financial statements', {'view': 'view_financials', 'post': 'close_period'}),
    ('chart', 'Chart of accounts', {'view': 'view_account', 'create': 'add_account'}),
    ('payments', 'Supplier payments', {'view': 'view_supplierpayment', 'create': 'add_supplierpayment', 'approve': 'approve_supplierpayment', 'post': 'release_supplierpayment'}),
    ('bank', 'Bank reconciliation', {'view': 'view_bankstatementline', 'create': 'add_bankstatementline', 'edit': 'change_bankstatementline'}),
    ('assets', 'Fixed assets', {'view': 'view_fixedasset', 'create': 'add_fixedasset', 'post': 'depreciate_fixedasset'}),
    ('supplier_invoices', 'Supplier invoices', {'view': 'view_supplierinvoice', 'create': 'add_supplierinvoice', 'approve': 'approve_supplierinvoice'}),
    ('debit_notes', 'Debit notes & returns', {'view': 'view_debitnote', 'create': 'add_debitnote'}),
    ('grn', 'Goods receipt (GRN)', {'view': 'view_goodsreceipt', 'create': 'add_goodsreceipt', 'post': 'post_goodsreceipt', 'approve': 'release_goodsreceipt'}),
    ('inventory', 'Inventory', {'view': 'view_stockbalance', 'post': 'post_opening'}),
    ('transfers', 'Stock transfers', {'view': 'view_stocktransfer', 'create': 'add_stocktransfer', 'approve': 'approve_stocktransfer', 'post': 'dispatch_stocktransfer', 'edit': 'receive_stocktransfer'}),
    ('counts', 'Stock counts', {'view': 'view_stockcount', 'create': 'add_stockcount', 'approve': 'approve_stockcount'}),
    ('damage', 'Damaged stock', {'view': 'view_damagereport', 'create': 'add_damagereport', 'approve': 'decide_damagereport'}),
    ('production', 'Production', {'view': 'view_production', 'create': 'add_production', 'post': 'complete_production', 'approve': 'release_production'}),
    ('recipes', 'Recipes & BOM', {'view': 'view_recipe', 'create': 'add_recipe', 'approve': 'approve_recipe'}),
    ('shift_logs', 'Shift log', {'view': 'view_shiftlog', 'create': 'add_shiftlog'}),
    ('packing', 'Packing', {'view': 'view_packingrun', 'create': 'add_packingrun'}),
    ('quality', 'Quality lab', {'view': 'view_qualitytest', 'create': 'add_qualitytest', 'edit': 'add_labequipment'}),
    ('invoices', 'Tax invoices', {'view': 'view_invoice', 'post': 'post_invoice', 'export': 'export_invoice'}),
    ('receipts', 'Receipts & ledger', {'view': 'view_receipt', 'create': 'add_receipt', 'cancel': 'bounce_receipt'}),
    ('credit_notes', 'Credit notes', {'view': 'view_creditnote', 'create': 'add_creditnote'}),
    ('vehicles', 'Vehicles & drivers', {'view': 'view_vehicle', 'create': 'add_vehicle', 'edit': 'change_vehicle'}),
    ('dispatch', 'Dispatch planning', {'view': 'view_dispatch', 'create': 'add_dispatch'}),
    ('gate', 'Gate-in / gate-out', {'post': 'gate_dispatch'}),
    ('weighbridge', 'Weighbridge readings', {'post': 'weigh_dispatch', 'approve': 'approve_weight_exception'}),
    ('loading', 'Truck loading', {'post': 'load_dispatch'}),
    ('pod', 'Proof of delivery', {'post': 'pod_dispatch'}),
    ('freight_rates', 'Freight rates', {'view': 'view_freightrate', 'create': 'add_freightrate', 'edit': 'change_freightrate'}),
    ('freight_bills', 'Freight bills', {'view': 'view_freightbill', 'create': 'add_freightbill', 'approve': 'approve_freightbill'}),
    ('pricing', 'Price list', {'view': 'view_pricerule', 'create': 'add_pricerule', 'edit': 'change_pricerule'}),
    ('influencers', 'Masons & contractors', {'view': 'view_influencer', 'create': 'add_influencer', 'edit': 'change_influencer', 'export': 'export_influencer'}),
    ('partners', 'Business partners', {'view': 'view_partner', 'create': 'add_partner', 'edit': 'change_partner'}),
    ('products', 'Products & materials', {'view': 'view_product', 'create': 'add_product', 'edit': 'change_product'}),
    ('sites', 'Plants & depots', {'view': 'view_site', 'create': 'add_site', 'edit': 'change_site'}),
    ('reports', 'Stock reports', {'view': 'view_stockmovement', 'export': 'export_stockmovement'}),
    ('audit', 'Audit trail', {'view': 'view_auditevent'}),
    ('organisation', 'Organisation setup', {'view': 'view_organization', 'edit': 'change_organization'}),
]
MANAGED_CODES = {code for _, _, permissions in MODULES for code in permissions.values()}
ALL_VIEWS = {p['view'] for _, _, p in MODULES if 'view' in p} - {'access_company_finance'}
PRESETS = {
    'Operations maker': ALL_VIEWS | {'create_sales_order', 'submit_sales_order', 'cancel_sales_order', 'post_sales_order', 'create_purchase_order', 'submit_purchase_order', 'cancel_purchase_order', 'post_purchase_order', 'add_production', 'complete_production', 'add_qualitytest'},
    'Operations approver': ALL_VIEWS | {'approve_stockcount', 'approve_stocktransfer', 'decide_damagereport', 'approve_purchaserequest', 'post_invoice', 'approve_sales_order', 'approve_purchase_order', 'post_sales_order', 'post_purchase_order', 'release_production'},
    'Sales executive': {'view_pricerule', 'view_sales_order', 'create_sales_order', 'submit_sales_order', 'cancel_sales_order', 'view_influencer', 'add_influencer', 'change_influencer', 'view_partner', 'view_product', 'view_site'},
    'Purchase officer': {'view_purchase_order', 'create_purchase_order', 'submit_purchase_order', 'cancel_purchase_order', 'view_purchaserequest', 'add_purchaserequest',
                         'change_purchaserequest', 'view_rfq', 'add_rfq', 'award_rfq', 'view_stockbalance', 'view_partner', 'view_product', 'view_site'},
    'Purchase head': {'view_debitnote', 'add_debitnote', 'view_supplierinvoice', 'view_purchase_order', 'approve_purchase_order', 'approve_high_value_purchase', 'cancel_purchase_order', 'view_purchaserequest', 'approve_purchaserequest',
                      'view_rfq', 'award_rfq', 'view_stockbalance', 'view_partner', 'view_product', 'view_site'},
    'Accountant': {'access_company_finance', 'view_supplierpayment', 'add_supplierpayment', 'view_bankstatementline', 'add_bankstatementline', 'change_bankstatementline', 'view_fixedasset',
                   'add_fixedasset', 'view_journalentry', 'add_journalentry', 'view_financials', 'view_account', 'view_invoice', 'view_receipt', 'add_receipt', 'view_creditnote',
                   'view_supplierinvoice', 'add_supplierinvoice', 'view_debitnote', 'view_partner', 'view_site', 'view_product', 'view_stockbalance'},
    'Accounts payable': {'view_supplierinvoice', 'add_supplierinvoice', 'view_debitnote', 'add_debitnote', 'view_goodsreceipt', 'view_purchase_order', 'view_partner', 'view_site', 'view_product'},
    'Finance controller': {'access_company_finance', 'view_supplierpayment', 'approve_supplierpayment', 'release_supplierpayment', 'view_bankstatementline', 'view_fixedasset',
                           'depreciate_fixedasset', 'view_journalentry', 'approve_journalentry', 'view_financials', 'close_period', 'view_account', 'add_account',
                           'view_supplierinvoice', 'approve_supplierinvoice', 'view_debitnote', 'view_goodsreceipt', 'view_purchase_order', 'view_receipt', 'view_invoice',
                           'view_partner', 'view_site', 'view_product'},
    'Store requester': {'view_purchaserequest', 'add_purchaserequest', 'change_purchaserequest', 'view_stockbalance', 'view_product', 'view_site'},
    'Warehouse operator': {'view_stockcount', 'add_stockcount', 'view_stocktransfer', 'add_stocktransfer', 'dispatch_stocktransfer', 'receive_stocktransfer', 'view_damagereport', 'add_damagereport', 'view_goodsreceipt', 'post_goodsreceipt', 'view_sales_order', 'post_sales_order', 'view_purchase_order', 'post_purchase_order', 'view_stockbalance', 'view_stockmovement', 'view_product', 'view_site'},
    'Production operator': {'view_production', 'add_production', 'complete_production', 'view_recipe', 'view_shiftlog', 'add_shiftlog', 'view_packingrun', 'add_packingrun',
                            'view_stockbalance', 'view_product', 'view_site'},
    'Production head': {'view_production', 'add_production', 'complete_production', 'view_recipe', 'add_recipe', 'approve_recipe', 'view_shiftlog', 'add_shiftlog',
                        'view_packingrun', 'add_packingrun', 'view_qualitytest', 'view_stockbalance', 'view_product', 'view_site'},
    'Quality manager': {'view_goodsreceipt', 'release_goodsreceipt', 'view_qualitytest', 'add_qualitytest', 'add_labequipment', 'view_production', 'release_production',
                        'view_recipe', 'approve_recipe', 'view_product', 'view_site'},
    'Gate security': {'view_dispatch', 'gate_dispatch', 'view_vehicle', 'view_goodsreceipt', 'add_goodsreceipt', 'view_purchase_order'},
    'Weighbridge operator': {'view_dispatch', 'weigh_dispatch', 'view_vehicle', 'view_goodsreceipt'},
    'Loading supervisor': {'view_dispatch', 'load_dispatch', 'view_vehicle'},
    'Logistics manager': {'view_freightrate', 'add_freightrate', 'change_freightrate', 'view_freightbill', 'approve_freightbill', 'view_dispatch', 'add_dispatch', 'approve_weight_exception', 'pod_dispatch', 'view_vehicle', 'add_vehicle', 'change_vehicle',
                          'view_sales_order', 'view_partner', 'view_product', 'view_site'},
    'Internal auditor': ALL_VIEWS | {'access_company_finance', 'export_invoice', 'export_influencer', 'export_stockmovement'},
}


def initialize_roles(update=False):
    for name, codes in PRESETS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created or update:
            group.permissions.set(Permission.objects.filter(content_type__app_label='erp', codename__in=codes))


def order_permission(user, kind, action):
    return user.has_perm(f'erp.{action}_{"sales" if kind == "Sale" else "purchase"}_order')
