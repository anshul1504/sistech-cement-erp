def navigation(request):
    items = [
        ('dashboard', 'Overview', '◫', None),
        ('sales', 'Sales orders', '↗', 'view_sales_order'),
        ('purchases', 'Purchase orders', '↙', 'view_purchase_order'),
        ('inventory', 'Inventory', '▦', 'view_stockbalance'),
        ('production', 'Production', '⚙', 'view_production'),
        ('quality', 'Quality lab', '◈', 'view_qualitytest'),
        ('partners', 'Business partners', '♧', 'view_partner'),
        ('products', 'Products & materials', '▤', 'view_product'),
        ('sites', 'Plants & depots', '⌂', 'view_site'),
        ('reports', 'Reports', '▥', 'view_stockmovement'),
        ('audit', 'Audit trail', '◷', 'view_auditevent'),
    ]
    user = request.user
    visible = [(route, label, icon) for route, label, icon, permission in items if user.is_authenticated and (not permission or user.has_perm('erp.' + permission))]
    if user.is_authenticated:
        visible.append(('profile', 'My account', '◎'))
        if user.is_superuser:
            visible.append(('access_control', 'Access control', '⊞'))
    return {'navigation': visible}
