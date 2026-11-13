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


ASSET_VERSION = '0.4.8'

NAV_GROUPS = [
    ('Overview', [
        _item('dashboard', 'Dashboard', 'dashboard'),
    ]),
    ('Sales', [
        _item('sales', 'Sales orders', 'sales', 'view_sales_order'),
        _soon('Quotations', 'quote'),
        _soon('Dealer CRM', 'crm'),
        _soon('Schemes & claims', 'gift'),
    ]),
    ('Procurement', [
        _item('purchases', 'Purchase orders', 'purchase', 'view_purchase_order'),
        _soon('Purchase requests', 'clipboard'),
    ]),
    ('Plant operations', [
        _item('production', 'Production', 'factory', 'view_production', match=('production_new', 'production_action')),
        _item('quality', 'Quality lab', 'flask', 'view_qualitytest', match=('quality_new',)),
        _item('inventory', 'Inventory', 'boxes', 'view_stockbalance', match=('opening',)),
    ]),
    ('Logistics', [
        _soon('Gate & weighbridge', 'scale'),
        _soon('Dispatch & trips', 'truck'),
    ]),
    ('Finance', [
        _soon('Invoices & GST', 'receipt'),
        _soon('Accounts & ledgers', 'book'),
    ]),
    ('Masters', [
        _item('products', 'Products & materials', 'package', 'view_product', match=('products_new', 'products_edit')),
        _item('partners', 'Dealers & suppliers', 'handshake', 'view_partner', match=('partners_new', 'partners_edit')),
        _item('sites', 'Plants & depots', 'building', 'view_site', match=('sites_new', 'sites_edit')),
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
    if name in ('order_detail', 'order_action'):
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
    if order_permission(user, 'Sale', 'create'):
        quick_create.append(('New sales order', reverse('order_new', args=['Sale']), 'sales'))
    if order_permission(user, 'Purchase', 'create'):
        quick_create.append(('New purchase order', reverse('order_new', args=['Purchase']), 'purchase'))
    if user.has_perm('erp.add_production'):
        quick_create.append(('Production entry', reverse('production_new'), 'factory'))
    if user.has_perm('erp.add_qualitytest'):
        quick_create.append(('Quality test', reverse('quality_new'), 'flask'))
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
