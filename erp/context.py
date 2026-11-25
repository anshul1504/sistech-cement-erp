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


ASSET_VERSION = '0.25.0'

def _area(area, label):
    """A module's dashboard (erp.insight_views.AREAS); visible when the dashboard's own check passes."""
    return {'route': 'insight_area', 'args': [area], 'area': area, 'label': label, 'icon': 'dashboard', 'perm': None,
            'match': (), 'superuser': False, 'soon': False}


# Modules: after sign-in the home page shows one tile per module the user can open; inside a module the sidebar lists only
# that module's pages (each module starts with its dashboard). Every page belongs to exactly one module.
MODULES = [
    {'key': 'sales', 'label': 'Sales & CRM', 'icon': 'sales', 'blurb': 'Orders, quotations, prices, dealer CRM, visits and incentives', 'sections': [
        ('Overview', [
            _area('sales', 'Sales dashboard'),
            _area('dealers', 'Dealer network'),
            _item('sales_team', 'Sales team', 'chart', match=('sales_person',)),
        ]),
        ('Selling', [
            _item('sales', 'Sales orders', 'sales', 'view_sales_order'),
            _item('quotations', 'Quotations', 'quote', 'view_quotation', match=('quotation_new', 'quotation_detail', 'quotation_edit')),
            _item('prices', 'Price list', 'receipt', 'view_pricerule', match=('prices_new', 'prices_edit', 'price_revise')),
            _item('returns', 'Customer returns', 'truck', 'view_returnrequest', match=('return_new', 'return_detail')),
        ]),
        ('Customers & field', [
            _item('crm_leads', 'Dealer CRM', 'crm', 'view_crmlead', match=('crm_lead_new', 'crm_lead_edit', 'crm_lead_detail', 'crm_applications', 'crm_application_detail', 'crm_application_edit', 'crm_performance', 'crm_policy', 'crm_review_detail', 'crm_performance_detail')),
            _item('projects','Project pipeline','building','view_salesproject',match=('project_new','project_detail','project_edit')),
            _item('beat_plans', 'Territories & visits', 'truck', 'view_beatplan', match=('territories','territory_new','beat_new','beat_detail','beat_edit','beat_outcome','beat_report')),
        ]),
        ('Incentives', [
            _item('commission_plans', 'Sales commission', 'chart', 'view_commissionplan', match=('commission_plan_new', 'commission_plan', 'commission_plan_edit', 'commission_run')),
            _item('channel_commission', 'Channel commission', 'handshake', 'view_channelrate', match=('channel_run', 'channel_rate_action', 'channel_calculate')),
            _item('schemes', 'Schemes & incentives', 'gift', 'view_incentivescheme', match=('scheme_new', 'scheme_edit', 'scheme_detail', 'scheme_evaluation', 'scheme_check_sources', 'scheme_payouts', 'scheme_batch', 'scheme_statement')),
            _item('recognition', 'Star programmes', 'gift', 'view_recognitionprogram', match=('recognition_detail', 'recognition_action', 'influencer_usage')),
        ]),
    ]},
    {'key': 'people', 'label': 'People & network', 'icon': 'handshake', 'blurb': 'C&F, promoters, dealers, retailers and masons', 'sections': [
        ('Network', [
            _item('people', 'People', 'handshake'),
            _item('partners', 'Dealers & suppliers', 'handshake', 'view_partner', match=('partners_new', 'partners_edit', 'partner_detail', 'partner_document_new')),
            _item('influencers', 'Masons & contractors', 'hardhat', 'view_influencer',
                  match=('influencer_districts', 'influencer_dealers', 'influencers_new', 'influencer_import', 'influencer_detail', 'influencers_edit')),
        ]),
    ]},
    {'key': 'procurement', 'label': 'Procurement', 'icon': 'purchase', 'blurb': 'Supply plan, requests, RFQs, purchase orders and GRN', 'sections': [
        ('Overview', [
            _area('procurement', 'Procurement dashboard'),
            _area('supply', 'Partner supply'),
        ]),
        ('Buying', [
            _item('supply_plan', 'Supply plan', 'chart', 'view_purchaserequest'),
            _item('purchase_requests', 'Purchase requests', 'clipboard', 'view_purchaserequest', match=('pr_new', 'pr_detail', 'pr_edit', 'pr_direct')),
            _item('reorder', 'Reorder suggestions', 'chart', 'view_purchaserequest'),
            _item('rfqs', 'RFQs & quotes', 'quote', 'view_rfq', match=('rfq_new', 'rfq_detail', 'quote_form')),
            _item('purchases', 'Purchase orders', 'purchase', 'view_purchase_order'),
            _item('inbound', 'Goods receipt (GRN)', 'boxes', 'view_goodsreceipt', match=('grn_new', 'grn_detail', 'grn_print')),
        ]),
        ('Partners', [
            _item('mfg_partners', 'Manufacturing partners', 'factory', 'view_supplycontract', match=('contract_new', 'contract_decide', 'sourcing_advisor')),
        ]),
    ]},
    {'key': 'plant', 'label': 'Plant & quality', 'icon': 'factory', 'blurb': 'Production, packing, maintenance, lab and complaints', 'sections': [
        ('Overview', [
            _area('plant', 'Plant dashboard'),
            _area('quality', 'Quality dashboard'),
        ]),
        ('Production', [
            _item('production', 'Production', 'factory', 'view_production', match=('production_new', 'production_action', 'production_detail', 'coa', 'trace')),
            _item('recipes', 'Recipes & BOM', 'clipboard', 'view_recipe', match=('recipe_new',)),
            _item('packing', 'Packing', 'package', 'view_packingrun'),
            _item('shift_logs', 'Shift log', 'history', 'view_shiftlog'),
            _item('maintenance', 'Maintenance', 'settings', 'view_maintenancejob'),
        ]),
        ('Quality', [
            _item('quality', 'Quality lab', 'flask', 'view_qualitytest', match=('quality_new',)),
            _item('complaints', 'Complaints & CAPA', 'shield', 'view_complaint', match=('complaint_new', 'complaint_detail')),
        ]),
    ]},
    {'key': 'stock', 'label': 'Stock & warehouse', 'icon': 'boxes', 'blurb': 'Stock position, bins, counts, transfers and damage', 'sections': [
        ('Overview', [
            _area('stock', 'Stock dashboard'),
        ]),
        ('Warehouse', [
            _item('inventory', 'Inventory', 'boxes', 'view_stockbalance', match=('opening', 'stock_card')),
            _item('stock_analysis', 'Ageing & slow-moving', 'history', 'view_stockmovement'),
            _item('bins', 'Bins & locations', 'boxes', 'view_storagebin'),
            _item('counts', 'Stock counts', 'clipboard', 'view_stockcount', match=('count_detail',)),
            _item('transfers', 'Stock transfers', 'truck', 'view_stocktransfer', match=('transfer_new', 'transfer_detail')),
            _item('damages', 'Damaged stock', 'shield', 'view_damagereport', match=('damage_new', 'damage_detail')),
        ]),
    ]},
    {'key': 'logistics', 'label': 'Logistics', 'icon': 'truck', 'blurb': 'Gate, weighbridge, dispatch, vehicles and freight', 'sections': [
        ('Overview', [
            _area('dispatch', 'Dispatch dashboard'),
        ]),
        ('Dispatch', [
            _item('dispatch_board', 'Gate & dispatch', 'scale', 'view_dispatch', match=('dispatch_new', 'dispatch_detail', 'dispatch_print')),
            _item('vehicles', 'Vehicles & drivers', 'truck', 'view_vehicle', match=('vehicle_new', 'vehicle_edit', 'driver_new', 'driver_edit')),
            _item('freight_bills', 'Freight bills', 'receipt', 'view_freightbill', match=('freight_bill_new', 'freight_bill_detail')),
            _item('freight_rates', 'Freight rates', 'chart', 'view_freightrate', match=('freight_rate_new', 'freight_rate_edit')),
        ]),
    ]},
    {'key': 'finance', 'label': 'Finance & accounts', 'icon': 'book', 'blurb': 'Invoices, collections, payables, GST, TDS and books', 'sections': [
        ('Overview', [
            _area('finance', 'Finance dashboard'),
            _area('credit', 'Credit & collections'),
            _area('schemes', 'Schemes dashboard'),
        ]),
        ('Receivables', [
            _item('invoices', 'Tax invoices', 'receipt', 'view_invoice', match=('invoice_detail', 'invoice_new')),
            _item('receipts', 'Receipts', 'book', 'view_receipt', match=('receipt_new', 'receipt_detail')),
            _item('credit_notes', 'Credit notes', 'receipt', 'view_creditnote', match=('credit_note_detail', 'credit_note_new')),
            _item('outstanding', 'Outstanding & ageing', 'chart', 'view_receipt', match=('partner_ledger',)),
            _item('collections', 'Collections desk', 'phone', 'view_receipt'),
            _item('deposits', 'Security deposits', 'book', 'view_depositentry'),
        ]),
        ('Payables', [
            _item('supplier_invoices', 'Supplier invoices', 'purchase', 'view_supplierinvoice', match=('sinv_new', 'sinv_detail')),
            _item('debit_notes', 'Debit notes', 'receipt', 'view_debitnote', match=('dn_new', 'dn_detail')),
            _item('payments', 'Payments', 'book', 'view_supplierpayment', match=('payment_new', 'payment_detail')),
            _item('tds', 'TDS', 'receipt', 'view_tdsdeduction'),
        ]),
        ('Books', [
            _item('journals', 'Accounts & ledgers', 'book', 'view_journalentry',
                  match=('voucher_new', 'journal_detail', 'trial_balance', 'profit_loss', 'balance_sheet', 'account_ledger', 'chart', 'periods', 'bank_rec', 'gst_summary',
                         'fixed_assets', 'cash_flow', 'period_detail', 'year_closes', 'year_detail', 'budgets', 'budget_detail', 'budget_vs_actual', 'cost_centres')),
        ]),
    ]},
    {'key': 'insights', 'label': 'Dashboards & reports', 'icon': 'chart', 'blurb': 'Management overview, every dashboard, report packs and audit', 'sections': [
        ('Dashboards', [
            _area('executive', 'Management'),
            _area('risk', 'Audit & risk'),
        ]),
        ('Reports', [
            _item('reports', 'Reports', 'chart', 'view_stockmovement'),
            _item('audit', 'Audit trail', 'history', 'view_auditevent'),
        ]),
    ]},
    {'key': 'admin', 'label': 'Setup & admin', 'icon': 'settings', 'blurb': 'Products, plants, users, roles, organisation and help', 'sections': [
        ('Masters', [
            _item('products', 'Products & materials', 'package', 'view_product', match=('products_new', 'products_edit', 'product_detail', 'product_duplicate')),
            _item('sites', 'Plants & depots', 'building', 'view_site', match=('sites_new', 'sites_edit', 'site_detail')),
        ]),
        ('Organisation', [
            _item('organization', 'Organisation setup', 'settings', 'view_organization', match=('organization_edit', 'organization_documents', 'organization_document', 'organization_document_new', 'organization_document_edit')),
            _item('access_control', 'Users', 'crm', superuser=True,
                  match=('member_new', 'member_detail', 'member_edit', 'member_set_password', 'dealer_access')),
            _item('roles', 'Roles & permissions', 'shield', superuser=True,
                  match=('role_new', 'role_detail', 'role_edit', 'role_duplicate')),
            _item('delegations', 'Approval delegations', 'key', superuser=True),
            _item('approval_board', 'Approval routing', 'clipboard', superuser=True),
            _item('system_settings', 'System settings', 'sliders', superuser=True),
        ]),
        ('Help & account', [
            _item('tickets', 'Help & support', 'phone', match=('ticket_new', 'ticket_detail')),
            _item('profile', 'My account', 'user', match=('password_change', 'password_change_done', 'profile_edit', 'account_security', 'account_activity', 'account_contacts', 'contact_new', 'contact_edit')),
        ]),
    ]},
    {'key': 'dealer', 'label': 'My dealer account', 'icon': 'handshake', 'blurb': 'Your orders, invoices and ledger', 'sections': [
        ('My account', [
            _item('dealer_dashboard', 'My dealer account', 'handshake', match=('dealer_invoices',)),
            _item('my_ledger', 'My ledger', 'book'),
        ]),
    ]},
]
# Overview (old home) leads the Dashboards module.
MODULES[[m['key'] for m in MODULES].index('insights')]['sections'][0][1].insert(0, _item('overview', 'Overview', 'dashboard'))
MODULE_MAP = {m['key']: m for m in MODULES}
ROUTE_MODULE = {r: m['key'] for m in MODULES for _, items in m['sections'] for i in items for r in i['match']}
AREA_MODULE = {i['area']: m['key'] for m in MODULES for _, items in m['sections'] for i in items if i.get('area')}
# Pages outside the menu that still belong to a module.
ROUTE_MODULE.update({'report_packs': 'insights', 'insight_definitions': 'insights', 'insight_drill': 'insights', 'approval_inbox': 'admin',
                     'order_detail': 'sales', 'order_new': 'sales'})


def _can_see(user, item):
    if item['route'] == 'sales_team':
        from .team_views import can_see_team
        return can_see_team(user)
    if item.get('area'):
        from .insight_views import AREA_MAP
        return AREA_MAP[item['area']][3](user)
    if item['route'] == 'people':
        return user.has_perm('erp.view_partner') or user.has_perm('erp.view_influencer')
    if item['route'] in {'my_ledger', 'dealer_dashboard'}:
        from .models import DealerLedgerAccess
        return DealerLedgerAccess.objects.filter(user=user, active=True, partner__active=True, partner__kind='Dealer').exists()
    if item['route'] in ('journals', 'payments', 'receipts', 'outstanding', 'collections', 'deposits', 'tds') and not user.has_perm('erp.access_company_finance'):
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
    match = getattr(request, 'resolver_match', None)
    area = match.kwargs.get('area') if match and match.url_name == 'insight_area' else None
    modules = []
    for m in MODULES:
        sections = []
        for label, items in m['sections']:
            visible = []
            for item in items:
                if not _can_see(user, item):
                    continue
                url = reverse(item['route'], args=item.get('args') or [])
                on = (area == item['area']) if item.get('area') else (bool(item['route']) and active in item['match'])
                visible.append(dict(item, url=url, active=on))
            if visible:
                sections.append({'label': label, 'items': visible, 'live': True})
        if sections:
            modules.append({'key': m['key'], 'label': m['label'], 'icon': m['icon'], 'blurb': m['blurb'], 'sections': sections,
                            'url': reverse('module_home', args=[m['key']]), 'first': sections[0]['items'][0]['url']})
    visible_keys = {m['key'] for m in modules}
    key = AREA_MODULE.get(area) if area else ROUTE_MODULE.get(active)
    if key is None and active not in ('dashboard', 'my_work', None) and hasattr(request, 'session'):
        key = request.session.get('erp_module')          # pages outside the menu stay in the module they were opened from
    if key not in visible_keys:
        key = None
    if key and hasattr(request, 'session') and request.session.get('erp_module') != key:
        request.session['erp_module'] = key
    module = next((m for m in modules if m['key'] == key), None)
    current = None
    for m in modules:
        for sec in m['sections']:
            for it in sec['items']:
                if it['active'] and m is module:
                    current = {'group': m['label'], 'label': it['label'], 'url': it['url'], 'module_url': m['url']}
    if module:
        groups = module['sections']
    else:
        groups = [{'label': 'Start', 'live': True, 'items': [{'route': 'dashboard', 'label': 'Home', 'icon': 'grid', 'url': reverse('dashboard'), 'active': active == 'dashboard', 'soon': False}]},
                  {'label': 'Modules', 'live': True, 'items': [{'route': 'module_home', 'label': m['label'], 'icon': m['icon'], 'url': m['url'],
                                                               'active': False, 'soon': False} for m in modules]}]

    # Create menu: inside a module only that module's "new" actions; elsewhere only the everyday ones (core), at most 8.
    # Every entry is gated by the permission its form needs, so each role sees only what it can actually create.
    fin = user.has_perm('erp.access_company_finance')
    has = lambda p: user.has_perm('erp.' + p)
    options = [  # (module, core, label, route, args, icon, allowed)
        ('sales', True, 'Sales order', 'order_new', ['Sale'], 'sales', order_permission(user, 'Sale', 'create')),
        ('sales', True, 'Quotation', 'quotation_new', [], 'quote', has('add_quotation')),
        ('sales', True, 'CRM lead', 'crm_lead_new', [], 'crm', has('add_crmlead')),
        ('sales', False, 'Daily beat plan', 'beat_new', [], 'truck', has('add_beatplan')),
        ('sales', False, 'Project opportunity', 'project_new', [], 'building', has('add_salesproject')),
        ('sales', False, 'Customer return', 'return_new', [], 'truck', has('add_returnrequest')),
        ('sales', False, 'Price', 'prices_new', [], 'receipt', has('add_pricerule')),
        ('people', False, 'Mason or contractor', 'influencers_new', [], 'hardhat', has('add_influencer')),
        ('people', False, 'C&F, agent or supplier', 'partners_new', [], 'handshake', has('add_partner')),
        ('procurement', True, 'Purchase request', 'pr_new', [], 'clipboard', has('add_purchaserequest')),
        ('procurement', True, 'Purchase order', 'order_new', ['Purchase'], 'purchase', order_permission(user, 'Purchase', 'create')),
        ('plant', True, 'Process order', 'production_new', [], 'factory', has('add_production')),
        ('plant', True, 'Quality test', 'quality_new', [], 'flask', has('add_qualitytest')),
        ('plant', False, 'Complaint', 'complaint_new', [], 'shield', has('add_complaint')),
        ('stock', True, 'Stock transfer', 'transfer_new', [], 'truck', has('add_stocktransfer')),
        ('stock', False, 'Damaged stock report', 'damage_new', [], 'shield', has('add_damagereport')),
        ('stock', False, 'Opening stock', 'opening', [], 'boxes', has('post_opening')),
        ('logistics', False, 'Vehicle', 'vehicle_new', [], 'truck', has('add_vehicle')),
        ('finance', True, 'Receipt', 'receipt_new', [], 'book', fin and has('add_receipt')),
        ('finance', True, 'Journal voucher', 'voucher_new', [], 'book', fin and has('add_journalentry')),
        ('admin', False, 'Product', 'products_new', [], 'package', has('add_product')),
        ('admin', False, 'Plant or depot', 'sites_new', [], 'building', has('add_site')),
        ('admin', False, 'User', 'member_new', [], 'user', user.is_superuser),
        ('admin', False, 'Role', 'role_new', [], 'shield', user.is_superuser),
    ]
    here = module['key'] if module else None
    picked = [o for o in options if o[6] and (o[0] == here if here else o[1])]
    if not here:
        picked = picked[:8]
    quick_create = [(o[2], reverse(o[3], args=o[4]), o[5]) for o in picked]
    quick_create_label = f"New in {module['label']}" if module else 'Create new'

    if user.is_superuser:
        role_label = 'Super administrator'
    else:
        role_label = ', '.join(user.groups.order_by('name').values_list('name', flat=True)[:2]) or 'No role assigned'

    context.update(nav_groups=groups, nav_current=current, nav_modules=modules, nav_module=module, quick_create=quick_create, quick_create_label=quick_create_label, user_role_label=role_label)
    return context
