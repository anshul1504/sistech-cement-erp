"""App-shell context: grouped sidebar navigation, breadcrumbs and quick-create actions.

Every live item is gated by the same permission the view enforces, so users only see
links they can open. Roadmap modules (not built yet) are listed as "soon" without a link.
"""
from django.conf import settings
from django.urls import reverse

from accounts.roles import order_permission


def _item(route, label, icon, perm=None, match=(), superuser=False):
    return {'route': route, 'label': label, 'icon': icon, 'perm': perm,
            'match': (route, *match), 'superuser': superuser, 'soon': False}


def _soon(label, icon):
    return {'route': None, 'label': label, 'icon': icon, 'perm': None, 'match': (), 'superuser': False, 'soon': True}


ASSET_VERSION = '0.14.0'

NAV_GROUPS = [
    ('Overview', [
        _item('dashboard', 'Dashboard', 'dashboard'),
    ]),
    ('Sales', [
        _item('sales', 'Sales orders', 'sales', 'view_sales_order'),
        _item('prices', 'Price list', 'receipt', 'view_pricerule', match=('prices_new', 'prices_edit', 'price_revise')),
        _soon('Quotations', 'quote'),
        _item('crm_leads', 'Dealer CRM', 'crm', 'view_crmlead', match=('crm_lead_new', 'crm_lead_edit', 'crm_lead_detail', 'crm_applications', 'crm_application_detail', 'crm_application_edit', 'crm_performance', 'crm_policy', 'crm_review_detail', 'crm_performance_detail')),
        _item('influencers', 'Masons & contractors', 'hardhat', 'view_influencer',
              match=('influencer_districts', 'influencer_dealers', 'influencers_new', 'influencer_import', 'influencer_detail', 'influencers_edit')),
        _item('schemes', 'Schemes & incentives', 'gift', 'view_incentivescheme', match=('scheme_new', 'scheme_edit', 'scheme_detail', 'scheme_evaluation', 'scheme_check_sources')),
    ]),
    ('Procurement', [
        _item('purchase_requests', 'Purchase requests', 'clipboard', 'view_purchaserequest', match=('pr_new', 'pr_detail', 'pr_edit', 'pr_direct', 'reorder')),
        _item('rfqs', 'RFQs & quotes', 'quote', 'view_rfq', match=('rfq_new', 'rfq_detail', 'quote_form')),
        _item('purchases', 'Purchase orders', 'purchase', 'view_purchase_order'),
        _item('inbound', 'Goods receipt (GRN)', 'boxes', 'view_goodsreceipt', match=('grn_new', 'grn_detail', 'grn_print')),
    ]),
    ('Plant operations', [
        _item('production', 'Production', 'factory', 'view_production', match=('production_new', 'production_action', 'production_detail', 'coa', 'trace')),
        _item('recipes', 'Recipes & BOM', 'clipboard', 'view_recipe', match=('recipe_new',)),
        _item('shift_logs', 'Shift log', 'history', 'view_shiftlog'),
        _item('packing', 'Packing', 'package', 'view_packingrun'),
        _item('quality', 'Quality lab', 'flask', 'view_qualitytest', match=('quality_new',)),
        _item('inventory', 'Inventory', 'boxes', 'view_stockbalance', match=('opening', 'stock_card', 'stock_analysis')),
        _item('counts', 'Stock counts', 'clipboard', 'view_stockcount', match=('count_detail',)),
        _item('transfers', 'Stock transfers', 'truck', 'view_stocktransfer', match=('transfer_new', 'transfer_detail')),
        _item('damages', 'Damaged stock', 'shield', 'view_damagereport', match=('damage_new', 'damage_detail')),
    ]),
    ('Logistics', [
        _item('dispatch_board', 'Gate & dispatch', 'scale', 'view_dispatch', match=('dispatch_new', 'dispatch_detail', 'dispatch_print')),
        _item('vehicles', 'Vehicles & drivers', 'truck', 'view_vehicle', match=('vehicle_new', 'vehicle_edit', 'driver_new', 'driver_edit')),
        _item('freight_bills', 'Freight bills', 'receipt', 'view_freightbill', match=('freight_bill_new', 'freight_bill_detail')),
        _item('freight_rates', 'Freight rates', 'chart', 'view_freightrate', match=('freight_rate_new', 'freight_rate_edit')),
    ]),
    ('Finance', [
        _item('invoices', 'Tax invoices', 'receipt', 'view_invoice', match=('invoice_detail', 'invoice_new')),
        _item('receipts', 'Receipts', 'book', 'view_receipt', match=('receipt_new', 'receipt_detail')),
        _item('credit_notes', 'Credit notes', 'receipt', 'view_creditnote', match=('credit_note_detail', 'credit_note_new')),
        _item('outstanding', 'Outstanding & ageing', 'chart', 'view_receipt', match=('partner_ledger',)),
        _item('supplier_invoices', 'Supplier invoices', 'purchase', 'view_supplierinvoice', match=('sinv_new', 'sinv_detail')),
        _item('debit_notes', 'Debit notes', 'receipt', 'view_debitnote', match=('dn_new', 'dn_detail')),
        _item('journals', 'Accounts & ledgers', 'book', 'view_journalentry',
              match=('voucher_new', 'journal_detail', 'trial_balance', 'profit_loss', 'balance_sheet', 'account_ledger', 'chart', 'periods', 'bank_rec', 'gst_summary',
                     'fixed_assets', 'cash_flow', 'period_detail', 'year_closes', 'year_detail')),
        _item('payments', 'Payments', 'book', 'view_supplierpayment', match=('payment_new', 'payment_detail')),
    ]),
    ('Masters', [
        _item('products', 'Products & materials', 'package', 'view_product', match=('products_new', 'products_edit', 'product_detail', 'product_duplicate')),
        _item('partners', 'Dealers & suppliers', 'handshake', 'view_partner', match=('partners_new', 'partners_edit', 'partner_detail', 'partner_document_new')),
        _item('sites', 'Plants & depots', 'building', 'view_site', match=('sites_new', 'sites_edit', 'site_detail')),
    ]),
    ('Insights', [
        _item('reports', 'Reports', 'chart', 'view_stockmovement'),
        _item('audit', 'Audit trail', 'history', 'view_auditevent'),
    ]),
    ('Administration', [
        _item('access_control', 'Users', 'crm', superuser=True,
              match=('member_new', 'member_detail', 'member_edit', 'member_set_password')),
        _item('roles', 'Roles & permissions', 'shield', superuser=True,
              match=('role_new', 'role_detail', 'role_edit', 'role_duplicate')),
        _item('organization', 'Organisation setup', 'settings', 'view_organization', match=('organization_edit', 'organization_documents', 'organization_document', 'organization_document_new', 'organization_document_edit')),
        _item('system_settings', 'System settings', 'sliders', superuser=True),
        _item('profile', 'My account', 'user', match=('password_change', 'password_change_done', 'profile_edit', 'account_security', 'account_activity', 'account_contacts', 'contact_new', 'contact_edit')),
    ]),
]


def _can_see(user, item):
    if item['route'] in ('journals', 'payments', 'receipts', 'outstanding') and not user.has_perm('erp.access_company_finance'):
        return False
    if item['soon']:
        return True
    if item['superuser']:
        return user.is_superuser
    return not item['perm'] or user.has_perm('erp.' + item['perm'])


def _active_route(request):
    match = getattr(request, 'resolver_match', None)
    if not match:
        return None
    name, kwargs = match.url_name, match.kwargs
    if name == 'order_new':
        return 'sales' if kwargs.get('kind') == 'Sale' else 'purchases'
    if name in ('order_detail', 'order_action', 'order_edit'):
        from erp.models import Order
        kind = Order.objects.filter(pk=kwargs.get('pk')).values_list('kind', flat=True).first()
        return 'sales' if kind == 'Sale' else 'purchases' if kind == 'Purchase' else None
    return name


def branding():
    """Organisation name and logo URLs for every page (sign-in pages included)."""
    from erp.models import Organization
    org = Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization()
    url = lambda kind: f"{reverse('brand_asset', args=[kind])}?v={org.version}" if getattr(org, f'{kind}_type') else None
    return {'org': org, 'org_logo_url': url('logo'), 'org_icon_url': url('icon')}


def user_photo_url(user):
    from accounts.models import UserProfile
    if not user.is_authenticated:
        return None
    row = UserProfile.objects.filter(user=user).values('photo_type', 'updated_at').first()
    if not row or not row['photo_type']:
        return None
    return f"{reverse('profile_photo', args=[user.pk])}?v={int(row['updated_at'].timestamp())}"


def navigation(request):
    user = request.user
    # Bump ASSET_VERSION whenever CSS/JS changes so browsers fetch fresh files.
    context = {'local_server': settings.DEBUG, 'app_version': 'v0.2', 'asset_version': ASSET_VERSION, **branding(), 'user_photo_url': user_photo_url(user)}
    if not user.is_authenticated:
        return context
    active = _active_route(request)
    groups, current = [], None
    for label, items in NAV_GROUPS:
        visible = []
        for item in items:
            if not _can_see(user, item):
                continue
            entry = dict(item, url=reverse(item['route']) if item['route'] else None,
                         active=bool(item['route']) and active in item['match'])
            if entry['active']:
                current = {'group': label, 'label': entry['label'], 'url': entry['url']}
            visible.append(entry)
        if visible:
            groups.append({'label': label, 'items': visible, 'live': any(not i['soon'] for i in visible)})

    quick_create = []
    if user.has_perm('erp.add_crmlead'):
        quick_create.append(('CRM lead', reverse('crm_lead_new'), 'crm'))
    if order_permission(user, 'Sale', 'create'):
        quick_create.append(('New sales order', reverse('order_new', args=['Sale']), 'sales'))
    if user.has_perm('erp.add_purchaserequest'):
        quick_create.append(('Purchase request', reverse('pr_new'), 'clipboard'))
    if order_permission(user, 'Purchase', 'create'):
        quick_create.append(('New purchase order', reverse('order_new', args=['Purchase']), 'purchase'))
    if user.has_perm('erp.add_production'):
        quick_create.append(('Process order', reverse('production_new'), 'factory'))
    if user.has_perm('erp.add_shiftlog'):
        quick_create.append(('Shift log', reverse('shift_logs'), 'history'))
    if user.has_perm('erp.add_qualitytest'):
        quick_create.append(('Quality test', reverse('quality_new'), 'flask'))
    if user.has_perm('erp.add_product'):
        quick_create.append(('Product', reverse('products_new'), 'package'))
    if user.has_perm('erp.add_vehicle'):
        quick_create.append(('Vehicle', reverse('vehicle_new'), 'truck'))
    if user.has_perm('erp.add_journalentry') and user.has_perm('erp.access_company_finance'):
        quick_create.append(('Journal voucher', reverse('voucher_new'), 'book'))
    if user.has_perm('erp.add_receipt') and user.has_perm('erp.access_company_finance'):
        quick_create.append(('Receipt', reverse('receipt_new'), 'book'))
    if user.has_perm('erp.add_pricerule'):
        quick_create.append(('Price', reverse('prices_new'), 'receipt'))
    if user.has_perm('erp.add_influencer'):
        quick_create.append(('Mason or contractor', reverse('influencers_new'), 'hardhat'))
    if user.has_perm('erp.add_partner'):
        quick_create.append(('Dealer or supplier', reverse('partners_new'), 'handshake'))
    if user.has_perm('erp.add_site'):
        quick_create.append(('Plant or depot', reverse('sites_new'), 'building'))
    if user.has_perm('erp.add_stocktransfer'):
        quick_create.append(('Stock transfer', reverse('transfer_new'), 'truck'))
    if user.has_perm('erp.add_damagereport'):
        quick_create.append(('Damaged stock report', reverse('damage_new'), 'shield'))
    if user.has_perm('erp.post_opening'):
        quick_create.append(('Opening stock', reverse('opening'), 'boxes'))
    if user.is_superuser:
        quick_create.append(('User', reverse('member_new'), 'user'))
        quick_create.append(('Role', reverse('role_new'), 'shield'))

    if user.is_superuser:
        role_label = 'Super administrator'
    else:
        role_label = ', '.join(user.groups.order_by('name').values_list('name', flat=True)[:2]) or 'No role assigned'

    context.update(nav_groups=groups, nav_current=current, quick_create=quick_create, user_role_label=role_label)
    return context
