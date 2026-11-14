"""Sales & purchase orders: create / edit draft with price-list lookup and GST, detail with tax breakup, live price API."""
import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from accounts.roles import order_permission
from .forms import OrderForm, Lines
from .gst import next_number, price_for, summarise, in_words
from .models import Order, Partner, Product, Site, StockBalance, AuditEvent, CUSTOMER_KINDS
from .services import audit, scope


def _sites(user):
    qs = Site.objects.filter(active=True)
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


def _check(user, kind, action):
    if not order_permission(user, kind, action):
        raise PermissionDenied


def apply_pricing(order, line_forms):
    """Snapshot GST rate, HSN and list price on each line. Empty sales rate → list price; no list price and no rate → error."""
    errors = []
    for f in line_forms:
        if not f.cleaned_data or f.cleaned_data.get('DELETE'):
            continue
        line, product = f.instance, f.cleaned_data['product']
        line.gst_rate, line.hsn = product.gst_rate or 0, product.hsn
        rule = price_for(product, order.site, order.partner, f.cleaned_data['quantity']) if order.kind == 'Sale' else None
        line.price_rule, line.list_rate = rule, (rule.rate if rule else None)
        if f.cleaned_data.get('rate') in (None, ''):
            if rule:
                line.rate = rule.rate
            else:
                f.add_error('rate', 'No list price for this product — enter a rate.')
                errors.append(product)
        if product.gst_rate is None:
            f.add_error('product', f'{product.name} has no GST rate. Set it in Products first.')
            errors.append(product)
    return not errors


def _form_context(request, kind, form, lines, order=None):
    products = {p.pk: {'unit': p.unit, 'gst': str(p.gst_rate if p.gst_rate is not None else ''), 'hsn': p.hsn} for p in Product.objects.filter(active=True)}
    partners = {p.pk: {'state': p.state, 'kind': p.kind} for p in Partner.objects.filter(active=True)}
    sites = {s.pk: {'state': s.state} for s in Site.objects.filter(active=True)}
    return {'title': ('Edit ' if order else 'New ') + ('sales order' if kind == 'Sale' else 'purchase order'), 'form': form, 'formset': lines,
            'kind': kind, 'order': order, 'js_data': {'products': products, 'partners': partners, 'sites': sites, 'priceUrl': '/orders/price/'}}


@login_required
def order_new(request, kind, pk=None):
    if kind not in ['Sale', 'Purchase']:
        raise Http404
    order = None
    if pk:
        order = get_object_or_404(scope(Order.objects.all(), request.user), pk=pk, kind=kind)
        _check(request.user, kind, 'create')
        if order.status != 'Draft':
            messages.error(request, 'Only draft orders can be edited. Cancel and re-create, or raise a revision after approval.')
            return redirect('order_detail', pk=order.pk)
    else:
        _check(request.user, kind, 'create')
    obj = order or Order(kind=kind, created_by=request.user)
    initial = {}
    if not order and request.GET.get('partner', '').isdigit():
        initial['partner'] = request.GET['partner']
    form = OrderForm(request.POST or None, instance=obj, initial=initial)
    form.fields['site'].queryset = _sites(request.user)
    form.fields['partner'].queryset = Partner.objects.filter(Q(active=True) | Q(pk=obj.partner_id or 0), kind__in=CUSTOMER_KINDS if kind == 'Sale' else ['Supplier']).order_by('name')
    lines = Lines(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid() and lines.is_valid():
        draft = form.save(commit=False)
        if apply_pricing(draft, lines.forms):
            with transaction.atomic():
                if not draft.pk:
                    draft.number = next_number('SO' if kind == 'Sale' else 'PO', draft.site)
                draft.save()
                lines.instance = draft
                lines.save()
                audit(request.user, 'Order updated' if order else 'Order created', draft)
            messages.success(request, 'Draft saved. Submit it for independent approval.' if not order else 'Draft updated.')
            return redirect('order_detail', pk=draft.pk)
    return render(request, 'erp/order_form.html', _form_context(request, kind, form, lines, order))


@login_required
def price_lookup(request):
    """JSON for the order form: list price and slab for product × site × partner × quantity."""
    if not (order_permission(request.user, 'Sale', 'create') or order_permission(request.user, 'Sale', 'view')):
        raise PermissionDenied
    try:
        product = Product.objects.get(pk=request.GET.get('product'))
        qty = Decimal(request.GET.get('qty') or '0')
    except (Product.DoesNotExist, ValueError, InvalidOperation):
        return JsonResponse({'rate': None})
    site = Site.objects.filter(pk=request.GET.get('site') or 0).first()
    partner = Partner.objects.filter(pk=request.GET.get('partner') or 0).first()
    rule = price_for(product, site, partner, qty)
    if not rule:
        return JsonResponse({'rate': None, 'gst': str(product.gst_rate or 0)})
    nxt = price_for(product, site, partner, Decimal('1e9'))
    slab = None
    if nxt and nxt.pk != rule.pk and nxt.min_quantity > qty:
        slab = {'from': str(nxt.min_quantity.normalize()), 'rate': str(nxt.rate)}
    return JsonResponse({'rate': str(rule.rate), 'scope': rule.scope_label, 'from': str(rule.min_quantity.normalize()), 'gst': str(product.gst_rate or 0), 'next_slab': slab})


@login_required
def order_edit(request, pk):
    order = get_object_or_404(scope(Order.objects.all(), request.user), pk=pk)
    return order_new(request, order.kind, pk)


@login_required
def order_detail(request, pk):
    order = get_object_or_404(scope(Order.objects.select_related('partner', 'site', 'created_by', 'approved_by'), request.user), pk=pk)
    _check(request.user, order.kind, 'view')
    lines = list(order.lines.select_related('product', 'price_rule'))
    tax = summarise(lines, order.is_interstate)
    actions = {f'can_{a}': order_permission(request.user, order.kind, a) for a in ['create', 'submit', 'approve', 'post', 'cancel']}
    checks = []
    if order.kind == 'Sale' and order.status in ('Submitted', 'Approved', 'Draft'):
        below = [l for l in lines if l.below_list]
        if below:
            checks.append(('warn', f'{len(below)} line{"s" if len(below) > 1 else ""} below list price — approver please confirm the discount.'))
        if not order.partner.gstin:
            checks.append(('info', 'Customer has no GSTIN — this will be a B2C (unregistered) invoice.'))
        if order.status != 'Approved':
            from .partner_views import credit_position
            credit = credit_position(order.partner)
            if credit['limit'] and credit['exposure'] + tax['grand_total'] > credit['limit']:
                checks.append(('danger', f'Credit limit will be exceeded: exposure ₹{credit["exposure"]:,.0f} + this order ₹{tax["grand_total"]:,.0f} > limit ₹{credit["limit"]:,.0f}.'))
        for l in lines:
            bal = StockBalance.objects.filter(site=order.site, product=l.product).values_list('quantity', flat=True).first() or Decimal('0')
            reserved = (Order.objects.filter(kind='Sale', site=order.site, status='Approved').exclude(pk=order.pk)
                        .aggregate(q=Sum('lines__quantity', filter=Q(lines__product=l.product)))['q'] or Decimal('0'))
            if bal - reserved < l.quantity:
                checks.append(('warn', f'{l.product.name}: {max(bal - reserved, 0):,.3f} {l.product.unit} free at {order.site.code} (stock {bal:,.3f}, reserved {reserved:,.3f}).'.replace('.000', '')))
    flow = ['Draft', 'Submitted', 'Approved', 'Completed']
    at = flow.index(order.status) if order.status in flow else -1
    steps = [(name, 'cancelled' if order.status == 'Cancelled' else 'done' if i < at else 'current' if i == at else 'todo') for i, name in enumerate(flow)]
    if order.kind == 'Sale':
        steps[-1] = ('Dispatched', steps[-1][1])
    history = AuditEvent.objects.filter(reference=order.number).select_related('actor').order_by('-created_at')[:8]
    return render(request, 'erp/order_detail.html', {'title': order.number, 'order': order, 'lines': lines, 'tax': tax, 'words': in_words(tax['grand_total']),
                                                     'checks': checks, 'history': history, 'steps': steps, **actions})
