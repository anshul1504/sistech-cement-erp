import csv
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Q, F
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from .models import Site, Product, Partner, Order, StockBalance, StockMovement, Production, QualityTest, AuditEvent
from .forms import SiteForm, ProductForm, PartnerForm, OrderForm, Lines, ProductionForm, QualityForm, OpeningForm
from .services import scope, permitted, audit, move_stock, transition_order, transition_production

def sites(user):
    qs = Site.objects.filter(active=True)
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)

def table(request, title, subtitle, headers, rows, create=None, filters=False):
    return render(request, 'erp/list.html', {'title': title, 'subtitle': subtitle, 'headers': headers, 'page': Paginator(rows, 15).get_page(request.GET.get('page')), 'create': create, 'filters': filters})

@login_required
def dashboard(request):
    orders = scope(Order.objects.select_related('partner', 'site').prefetch_related('lines'), request.user) if request.user.has_perm('erp.view_order') else Order.objects.none()
    balances = scope(StockBalance.objects.select_related('product', 'site'), request.user) if request.user.has_perm('erp.view_stockbalance') else StockBalance.objects.none()
    batches = scope(Production.objects.all(), request.user) if request.user.has_perm('erp.view_production') else Production.objects.none()
    sales = orders.filter(kind='Sale')
    sales_total = sum((o.total for o in sales.exclude(status='Cancelled')), Decimal('0'))
    return render(request, 'erp/dashboard.html', {'title': 'Operations overview', 'sales_total': sales_total, 'pending': orders.filter(status='Submitted').count(), 'held': batches.filter(status='Quality hold').count(), 'low_stock': balances.filter(quantity__lte=F('product__reorder_level')).count(), 'recent_orders': orders[:6], 'balances': balances[:6], 'batches': batches[:4], 'site_count': sites(request.user).count()})

MASTERS = {'sites': (Site, SiteForm, ['Code', 'Name', 'Type', 'Active'], ['code', 'name', 'kind', 'active']), 'products': (Product, ProductForm, ['SKU', 'Product', 'Category', 'UOM', 'Active'], ['code', 'name', 'category', 'unit', 'active']), 'partners': (Partner, PartnerForm, ['Code', 'Partner', 'Type', 'Territory', 'Credit limit INR'], ['code', 'name', 'kind', 'territory', 'credit_limit'])}

@login_required
def master_list(request, module):
    if module not in MASTERS: raise Http404
    model, form, headers, fields = MASTERS[module]
    permitted(request.user, 'view_' + model._meta.model_name)
    qs = model.objects.all().order_by('code')
    if model is Site and not request.user.is_superuser: qs = qs.filter(siteaccess__user=request.user)
    q = request.GET.get('q', '')
    if q: qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
    rows = [{'cells': [getattr(o, f) for f in fields], 'url': f'/{module}/{o.pk}/edit/'} for o in qs]
    can_add = request.user.has_perm('erp.add_' + model._meta.model_name)
    return table(request, {'sites': 'Plants & depots', 'products': 'Products & materials', 'partners': 'Business partners'}[module], 'Manage the master data behind your operations.', headers, rows, f'/{module}/new/' if can_add else None, True)

@login_required
def master_form(request, module, pk=None):
    if module not in MASTERS: raise Http404
    model, cls, _, _ = MASTERS[module]
    permitted(request.user, ('change_' if pk else 'add_') + model._meta.model_name)
    qs = model.objects.all()
    if model is Site and not request.user.is_superuser: qs = qs.filter(siteaccess__user=request.user)
    obj = get_object_or_404(qs, pk=pk) if pk else None
    form = cls(request.POST or None, instance=obj)
    if obj and model is Product:
        # A product's base UOM is fixed once it participates in any transaction.
        used = obj.orderline_set.exists() or obj.stockbalance_set.exists() or obj.production_set.exists()
        if used:
            form.fields['unit'].disabled = True
            form.fields['bag_weight_kg'].disabled = True
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj = form.save()
            audit(request.user, 'Master updated' if pk else 'Master created', obj, f'{model.__name__} {obj.pk}')
        messages.success(request, 'Master data saved.')
        return redirect(module)
    return render(request, 'erp/form.html', {'title': ('Edit ' if pk else 'New ') + model._meta.verbose_name, 'form': form})

@login_required
def orders(request, kind):
    permitted(request.user, 'view_order')
    qs = scope(Order.objects.filter(kind=kind).select_related('partner', 'site').prefetch_related('lines'), request.user)
    if request.GET.get('q'): qs = qs.filter(Q(number__icontains=request.GET['q']) | Q(partner__name__icontains=request.GET['q']))
    if request.GET.get('status'): qs = qs.filter(status=request.GET['status'])
    rows = [{'cells': [o.number, o.partner.name, o.site.code, o.due_date, f'INR {o.total:,.2f}', o.status], 'url': f'/orders/{o.pk}/'} for o in qs]
    return table(request, 'Sales orders' if kind == 'Sale' else 'Purchase orders', 'Track documents from draft through approval and stock posting. Values exclude tax and freight.', ['Document', 'Partner', 'Site', 'Due date', 'Net value', 'Status'], rows, f'/orders/new/{kind}/' if request.user.has_perm('erp.add_order') else None, True)

@login_required
def order_new(request, kind):
    if kind not in ['Sale', 'Purchase']: raise Http404
    permitted(request.user, 'add_order')
    obj = Order(kind=kind, created_by=request.user)
    form = OrderForm(request.POST or None, instance=obj)
    form.fields['site'].queryset = sites(request.user)
    form.fields['partner'].queryset = Partner.objects.filter(active=True, kind='Dealer' if kind == 'Sale' else 'Supplier')
    lines = Lines(request.POST or None, instance=obj)
    for line in lines: line.fields['product'].queryset = Product.objects.filter(active=True)
    if request.method == 'POST' and form.is_valid() and lines.is_valid():
        with transaction.atomic():
            obj = form.save()
            lines.instance = obj
            lines.save()
            audit(request.user, 'Order created', obj)
        messages.success(request, 'Draft created. Submit it for independent approval.')
        return redirect('order_detail', pk=obj.pk)
    return render(request, 'erp/form.html', {'title': 'New sales order' if kind == 'Sale' else 'New purchase order', 'form': form, 'formset': lines})

@login_required
def order_detail(request, pk):
    permitted(request.user, 'view_order')
    obj = get_object_or_404(scope(Order.objects.select_related('partner', 'site', 'created_by', 'approved_by').prefetch_related('lines__product'), request.user), pk=pk)
    return render(request, 'erp/order_detail.html', {'title': str(obj), 'order': obj})

@login_required
@require_POST
def order_action(request, pk, action):
    get_object_or_404(scope(Order.objects.all(), request.user), pk=pk)
    try:
        transition_order(request.user, pk, action)
        messages.success(request, 'Order workflow updated.')
    except ValidationError as e: messages.error(request, ' '.join(e.messages))
    return redirect('order_detail', pk=pk)

@login_required
def inventory(request):
    permitted(request.user, 'view_stockbalance')
    qs = scope(StockBalance.objects.select_related('site', 'product'), request.user).order_by('site__code', 'product__code')
    if request.GET.get('q'): qs = qs.filter(Q(product__name__icontains=request.GET['q']) | Q(site__name__icontains=request.GET['q']))
    rows = [{'cells': [o.product.code, o.product.name, o.site.name, o.quantity, o.product.unit, 'Low stock' if o.quantity <= o.product.reorder_level else 'Available']} for o in qs]
    return table(request, 'Inventory', 'Posted available quantities by site and product. Quality-held production is excluded.', ['SKU', 'Product', 'Location', 'On hand', 'UOM', 'Status'], rows, '/inventory/opening/' if request.user.has_perm('erp.post_opening') else None, True)

@login_required
def opening(request):
    permitted(request.user, 'post_opening')
    form = OpeningForm(request.POST or None)
    form.fields['site'].queryset = sites(request.user)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                obj = move_stock(request.user, d['site'], d['product'], d['quantity'], 'opening:' + d['reference'], 'Opening', d['notes'])
                audit(request.user, 'Opening stock posted', obj, d['notes'])
            messages.success(request, 'Opening stock posted.')
            return redirect('inventory')
        except IntegrityError: form.add_error('reference', 'This reference has already been posted.')
    return render(request, 'erp/form.html', {'title': 'Post opening stock', 'form': form})

@login_required
def production(request):
    permitted(request.user, 'view_production')
    qs = scope(Production.objects.select_related('site', 'product'), request.user)
    return render(request, 'erp/production.html', {'title': 'Production', 'batches': Paginator(qs, 12).get_page(request.GET.get('page'))})

@login_required
def production_new(request):
    permitted(request.user, 'add_production')
    form = ProductionForm(request.POST or None)
    form.fields['site'].queryset = sites(request.user)
    form.fields['product'].queryset = Product.objects.filter(active=True, category='Cement')
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            audit(request.user, 'Production created', obj)
        return redirect('production')
    return render(request, 'erp/form.html', {'title': 'New production batch', 'form': form})

@login_required
@require_POST
def production_action(request, pk, action):
    get_object_or_404(scope(Production.objects.all(), request.user), pk=pk)
    try:
        transition_production(request.user, pk, action)
        messages.success(request, 'Production workflow updated.')
    except ValidationError as e: messages.error(request, ' '.join(e.messages))
    return redirect('production')

@login_required
def quality(request):
    permitted(request.user, 'view_qualitytest')
    qs = scope(QualityTest.objects.select_related('production', 'site', 'created_by'), request.user)
    return table(request, 'Quality lab', 'Recorded test evidence controls production release. Failed batches remain on hold.', ['Batch', 'Test', 'Result', 'Disposition', 'Lab user', 'Recorded'], [{'cells': [o.production.batch, o.test_name, o.result, o.disposition, o.created_by.username, o.created_at]} for o in qs], '/quality/new/' if request.user.has_perm('erp.add_qualitytest') else None)

@login_required
def quality_new(request):
    permitted(request.user, 'add_qualitytest')
    form = QualityForm(request.POST or None)
    form.fields['production'].queryset = scope(Production.objects.filter(status='Quality hold'), request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            batch = Production.objects.select_for_update().get(pk=form.cleaned_data['production'].pk)
            if batch.status != 'Quality hold':
                form.add_error('production', 'Batch is no longer on quality hold.')
            else:
                obj = form.save(commit=False)
                obj.created_by, obj.site = request.user, batch.site
                obj.save()
                audit(request.user, 'Quality test recorded', obj, f'{batch.batch}: {obj.disposition}')
                return redirect('quality')
    return render(request, 'erp/form.html', {'title': 'Record quality test', 'form': form})

@login_required
def audit_list(request):
    permitted(request.user, 'view_auditevent')
    qs = AuditEvent.objects.select_related('actor', 'site')
    if not request.user.is_superuser: qs = scope(qs, request.user)
    return table(request, 'Audit trail', 'Read-only history of workflow actions and master changes.', ['When', 'User', 'Action', 'Reference', 'Detail'], [{'cells': [e.created_at, e.actor, e.action, e.reference, e.detail]} for e in qs])

@login_required
def reports(request):
    permitted(request.user, 'view_stockmovement')
    movements = scope(StockMovement.objects.select_related('product', 'site', 'created_by'), request.user)
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="stock-ledger.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date (Asia/Kolkata)', 'Site', 'SKU', 'Product', 'Quantity', 'UOM', 'Type', 'Source'])
        from django.utils.timezone import localtime
        def safe(value):
            value = str(value)
            return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
        for m in movements:
            writer.writerow([safe(v) for v in [localtime(m.created_at).isoformat(), m.site.code, m.product.code, m.product.name, m.quantity, m.product.unit, m.kind, m.source]])
        AuditEvent.objects.create(actor=request.user, action='Stock ledger exported', reference='CSV', detail='All permitted sites')
        return response
    return table(request, 'Stock movement report', 'Source-linked posted movements. Export contains only your permitted sites.', ['Document', 'Site', 'Product', 'Quantity', 'UOM', 'Type', 'Source'], [{'cells': [m.number, m.site.code, m.product.name, m.quantity, m.product.unit, m.kind, m.source]} for m in movements], '/reports/?export=csv')
