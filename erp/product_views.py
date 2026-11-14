"""Products & materials: list, detail, create/edit/duplicate, activate, delete-if-unused, standard catalogue."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .forms import ProductForm
from .models import Product, StockBalance, StockMovement, PRODUCT_CATEGORIES, GRADE_STANDARDS
from .services import audit, permitted

CATEGORY_ICONS = {'Cement': 'package', 'TMT': 'factory', 'Raw material': 'boxes', 'Packaging': 'gift', 'Fuel': 'flask', 'Spares': 'settings'}


def product_usage(product):
    """How often a product has been used in transactions (used products keep unit/bag weight and cannot be deleted)."""
    return {'orders': product.orderline_set.count(), 'stock_rows': product.stockbalance_set.count(),
            'movements': StockMovement.objects.filter(product=product).count(), 'production': product.production_set.count()}


def is_used(product):
    return any(product_usage(product).values())


@login_required
def products(request):
    permitted(request.user, 'view_product')
    category, status = request.GET.get('category', ''), request.GET.get('status', 'active')
    q = request.GET.get('q', '').strip()
    qs = Product.objects.annotate(stock=Sum('stockbalance__quantity'))
    if category: qs = qs.filter(category=category)
    if status == 'active': qs = qs.filter(active=True)
    elif status == 'inactive': qs = qs.filter(active=False)
    if q: qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(grade__icontains=q) | Q(hsn__icontains=q) | Q(brand__icontains=q))
    qs = qs.order_by('category', 'grade', 'bar_diameter_mm', 'code')
    counts = dict(Product.objects.filter(active=True).values_list('category').annotate(n=Count('id')))
    tabs = [('', 'All', sum(counts.values()))] + [(code, label, counts.get(code, 0)) for code, label in PRODUCT_CATEGORIES]
    return render(request, 'erp/products.html', {
        'title': 'Products & materials', 'page': Paginator(qs, 25).get_page(request.GET.get('page')), 'tabs': tabs,
        'filters': {'category': category, 'status': status, 'q': q}, 'icons': CATEGORY_ICONS,
        'can_add': request.user.has_perm('erp.add_product'), 'low_stock': low_stock_count()})


def low_stock_count():
    return StockBalance.objects.filter(status='available', product__active=True, quantity__lte=F('product__reorder_level')).count()


@login_required
def product_detail(request, pk):
    permitted(request.user, 'view_product')
    product = get_object_or_404(Product, pk=pk)
    from .views import sites as allowed_sites
    visible = allowed_sites(request.user)
    balances = StockBalance.objects.filter(product=product, site__in=visible, status='available').select_related('site').order_by('site__code')
    total = sum((b.quantity for b in balances), Decimal('0'))
    movements = StockMovement.objects.filter(product=product, site__in=visible).select_related('site').order_by('-created_at')[:10]
    usage = product_usage(product)
    return render(request, 'erp/product_detail.html', {
        'title': product.name, 'product': product, 'balances': balances, 'total': total, 'movements': movements, 'usage': usage,
        'used': any(usage.values()), 'icon': CATEGORY_ICONS.get(product.category, 'package'),
        'can_change': request.user.has_perm('erp.change_product'), 'can_add': request.user.has_perm('erp.add_product')})


@login_required
def product_form(request, pk=None, copy_from=None):
    permitted(request.user, 'change_product' if pk else 'add_product')
    product = get_object_or_404(Product, pk=pk) if pk else None
    initial = None
    if copy_from:
        source = get_object_or_404(Product, pk=copy_from)
        initial = {f: getattr(source, f) for f in ProductForm.Meta.fields if f not in ('code', 'name')}
        initial.update(code='', name=f'{source.name} (copy)', active=True)
    form = ProductForm(request.POST or None, instance=product, initial=initial)
    locked = bool(product and is_used(product))
    if locked:
        # A product's base unit and pack weight are fixed once it has transactions.
        form.fields['unit'].disabled = True
        form.fields['bag_weight_kg'].disabled = True
        form.fields['category'].disabled = True
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            audit(request.user, 'Master updated' if pk else 'Master created', saved,
                  f'Product {saved.code}' + (f'; copied from {source.code}' if copy_from else ''))
        messages.success(request, f'{saved.name} {"updated" if pk else "added"}.')
        return redirect('product_detail', pk=saved.pk)
    return render(request, 'erp/product_form.html', {
        'title': 'Edit product' if pk else ('Duplicate product' if copy_from else 'Add product'), 'form': form, 'product': product,
        'locked': locked, 'scope': ProductForm.FIELD_SCOPE, 'standards': GRADE_STANDARDS})


@login_required
def product_duplicate(request, pk):
    return product_form(request, copy_from=pk)


@login_required
@require_POST
def product_status(request, pk):
    permitted(request.user, 'change_product')
    product = get_object_or_404(Product, pk=pk)
    product.active = not product.active
    product.save(update_fields=['active', 'updated_at'])
    audit(request.user, 'Product activated' if product.active else 'Product deactivated', product, f'Product {product.code}')
    messages.success(request, f'{product.name} is {"active again" if product.active else "now inactive — it no longer appears on new orders"}.')
    return redirect('product_detail', pk=product.pk)


@login_required
@require_POST
def product_delete(request, pk):
    permitted(request.user, 'change_product')
    if not request.user.is_superuser:
        raise PermissionDenied
    product = get_object_or_404(Product, pk=pk)
    if is_used(product):
        messages.error(request, f'{product.name} has transactions, so it cannot be deleted. Deactivate it instead.')
        return redirect('product_detail', pk=product.pk)
    code, name = product.code, product.name
    audit(request.user, 'Product deleted', product, f'Product {code}')
    product.delete()
    messages.success(request, f'{name} deleted.')
    return redirect('products')


# Standard Sistech catalogue: common cement grades and TMT sizes. Adds only SKUs that do not exist yet.
def standard_catalogue():
    items = []
    for grade, label in [('OPC 43', 'OPC 43 Grade'), ('OPC 53', 'OPC 53 Grade'), ('PPC', 'Portland Pozzolana (PPC)'), ('PSC', 'Portland Slag (PSC)')]:
        items.append({'code': f'CEM-{grade.replace(" ", "")}-50', 'name': f'{label} · 50 kg bag', 'category': 'Cement', 'grade': grade,
                      'unit': 'BAG', 'bag_weight_kg': Decimal('50'), 'hsn': '2523', 'gst_rate': Decimal('18'), 'reorder_level': Decimal('500')})
    for grade in ('Fe 500D', 'Fe 550D'):
        for size, bundle in [(8, 10), (10, 7), (12, 5), (16, 3), (20, 2), (25, 1), (32, 1)]:
            wpm = (Decimal(size * size) / Decimal('162')).quantize(Decimal('0.001'))
            items.append({'code': f'TMT-{grade.replace("Fe ", "").replace(" ", "")}-{size:02d}', 'name': f'TMT {grade} · {size} mm', 'category': 'TMT',
                          'grade': grade, 'unit': 'MT', 'bar_diameter_mm': size, 'weight_per_metre_kg': wpm, 'bar_length_m': Decimal('12'),
                          'bars_per_bundle': bundle, 'hsn': '7214', 'gst_rate': Decimal('18'), 'reorder_level': Decimal('10')})
    for item in items:
        item['standard'] = GRADE_STANDARDS[item['grade']]
    return items


@login_required
@require_POST
def products_catalogue(request):
    permitted(request.user, 'add_product')
    existing = set(Product.objects.values_list('code', flat=True))
    created = [Product.objects.create(**item) for item in standard_catalogue() if item['code'] not in existing]
    for product in created:
        audit(request.user, 'Master created', product, f'Product {product.code}; standard catalogue')
    if created:
        messages.success(request, f'Added {len(created)} standard product{"s" if len(created) != 1 else ""}. Review GST rates and prices with your accountant.')
    else:
        messages.info(request, 'The standard catalogue is already loaded — nothing new to add.')
    return redirect('products')
