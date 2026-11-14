"""One permission catalogue shared by role editing, presets and tests."""
from django.contrib.auth.models import Group, Permission

ACTIONS = [('view', 'View'), ('create', 'Create'), ('edit', 'Edit'), ('submit', 'Submit'), ('approve', 'Approve'), ('post', 'Post'), ('cancel', 'Cancel'), ('export', 'Export')]
MODULES = [
    ('sales', 'Sales orders', {a: f'{a}_sales_order' for a in ['view', 'create', 'submit', 'approve', 'post', 'cancel']}),
    ('purchase', 'Purchase orders', {a: f'{a}_purchase_order' for a in ['view', 'create', 'submit', 'approve', 'post', 'cancel']}),
    ('inventory', 'Inventory', {'view': 'view_stockbalance', 'post': 'post_opening'}),
    ('production', 'Production', {'view': 'view_production', 'create': 'add_production', 'post': 'complete_production', 'approve': 'release_production'}),
    ('quality', 'Quality lab', {'view': 'view_qualitytest', 'create': 'add_qualitytest'}),
    ('invoices', 'Tax invoices', {'view': 'view_invoice', 'post': 'post_invoice', 'export': 'export_invoice'}),
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
ALL_VIEWS = {p['view'] for _, _, p in MODULES}
PRESETS = {
    'Operations maker': ALL_VIEWS | {'create_sales_order', 'submit_sales_order', 'cancel_sales_order', 'post_sales_order', 'create_purchase_order', 'submit_purchase_order', 'cancel_purchase_order', 'post_purchase_order', 'add_production', 'complete_production', 'add_qualitytest'},
    'Operations approver': ALL_VIEWS | {'post_invoice', 'approve_sales_order', 'approve_purchase_order', 'post_sales_order', 'post_purchase_order', 'release_production'},
    'Sales executive': {'view_pricerule', 'view_sales_order', 'create_sales_order', 'submit_sales_order', 'cancel_sales_order', 'view_influencer', 'add_influencer', 'change_influencer', 'view_partner', 'view_product', 'view_site'},
    'Purchase officer': {'view_purchase_order', 'create_purchase_order', 'submit_purchase_order', 'cancel_purchase_order', 'view_partner', 'view_product', 'view_site'},
    'Warehouse operator': {'view_sales_order', 'post_sales_order', 'view_purchase_order', 'post_purchase_order', 'view_stockbalance', 'view_stockmovement', 'view_product', 'view_site'},
    'Production operator': {'view_production', 'add_production', 'complete_production', 'view_product', 'view_site'},
    'Quality manager': {'view_qualitytest', 'add_qualitytest', 'view_production', 'release_production', 'view_product', 'view_site'},
    'Internal auditor': ALL_VIEWS | {'export_invoice', 'export_stockmovement'},
}


def initialize_roles(update=False):
    for name, codes in PRESETS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created or update:
            group.permissions.set(Permission.objects.filter(content_type__app_label='erp', codename__in=codes))


def order_permission(user, kind, action):
    return user.has_perm(f'erp.{action}_{"sales" if kind == "Sale" else "purchase"}_order')
