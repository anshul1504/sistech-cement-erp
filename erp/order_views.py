"""Sales & purchase orders: create / edit draft with price-list lookup and GST, detail with tax breakup, live price API."""
import json
from datetime import timedelta
from .gst import inr
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
        contract = None
        if order.kind == 'Purchase' and order.site_id:
            from .sourcing import current
            contract = current(order.site, product)
            line.list_rate = contract.rate if contract else None
        if f.cleaned_data.get('rate') in (None, ''):
            if rule or contract:
                line.rate = (rule or contract).rate
            else:
                f.add_error('rate', 'No list price for this product — enter a rate.' if order.kind == 'Sale' else 'No supply contract rate here — enter the agreed rate.')
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
    for l in lines:
        l.sent = l.dispatched_quantity() if order.kind == 'Sale' and order.status in ('Part dispatched', 'Completed') else None
        l.got = l.received_quantity() if order.kind == 'Purchase' and order.status in ('Part received', 'Completed') else None
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
            if credit['overdue_count']:
                checks.append(('danger', f'{credit["overdue_count"]} invoice(s) past due — ₹{inr(credit["overdue"], 0)}. Approval will be blocked until paid, unless a credit approver overrides it with a reason.'))
            if credit['exposure'] + tax['grand_total'] > credit['limit']:
                checks.append(('danger', f'Credit limit will be exceeded: exposure ₹{inr(credit["exposure"], 0)} + this order ₹{inr(tax["grand_total"], 0)} > limit ₹{inr(credit["limit"], 0)}.'))
        if order.status in ('Draft', 'Submitted'):
            from .sales import atp as atp_rows
            for r in atp_rows(order):
                if r['short'] > 0:
                    checks.append(('warn', f'{r["line"].product.name}: only {inr(max(r["free"], 0), 3)} {r["line"].product.unit} free at {order.site.code} (stock {inr(r["available"], 3)}, other orders {inr(r["reserved"], 3)}) — '.replace('.000', '')
                                   + ('approval will be refused (plant approves only against stock).' if order.site.atp_required else 'approver must record a backorder reason.')))
    requests = []
    if order.kind == 'Purchase':
        from .procurement import check_po_against_requests, approval_limit
        from .models import PurchaseRequest
        requests = list(PurchaseRequest.objects.filter(lines__po_lines__order=order).distinct())
        if order.status in ('Draft', 'Submitted'):
            for p in check_po_against_requests(order):
                checks.append(('danger', p))
            if not requests:
                checks.append(('info', 'Not linked to a purchase request — approver please confirm the need.'))
            if order.award_reason and (order.award_reason.startswith('Direct') or (order.quote_id and not order.award_reason.startswith('Lowest'))):
                checks.append(('warn', f'Sourcing: {order.award_reason}'))
            limit = approval_limit()
            if tax['grand_total'] > limit:
                checks.append(('info', f'Above the ₹{inr(limit, 0)} approval limit — needs a senior purchase approver.'))
    invoices = list(order.invoices.all()) if order.kind == 'Sale' else []
    invoice = invoices[0] if len(invoices) == 1 and not invoices[0].dispatch_id else None
    sale = {}
    if order.kind == 'Purchase':
        flow = ['Draft', 'Submitted', 'Approved', 'Part received', 'Completed']
        at = flow.index(order.status) if order.status in flow else -1
        steps = [(name, 'cancelled' if order.status == 'Cancelled' else 'done' if i < at else 'current' if i == at else 'todo') for i, name in enumerate(flow)]
    else:
        from . import sales
        prog = sales.progress(order)
        st = sales.stage(order, prog)
        flow = ['Draft', 'Approval pending', 'Approved', 'Dispatched', 'Invoiced', 'Delivered']
        pos = {'Draft': 0, 'Approval pending': 1, 'Approved': 2, 'Dispatch planned': 3, 'Part dispatched': 3, 'Dispatched': 3, 'Invoiced': 4, 'Delivered': 5, 'Closed': 5,
               'On hold': {'Submitted': 1}.get(order.held_from, 2)}.get(st, -1)
        steps = [(name, 'cancelled' if order.status in ('Cancelled', 'Revised') else 'done' if i < pos or (i == pos and st in ('Delivered', 'Closed')) else 'current' if i == pos else 'todo')
                 for i, name in enumerate(flow)]
        if st == 'Closed':
            steps[-1] = ('Short-closed', 'done')
        u = request.user
        credit = sales.credit_problems(order) if order.status == 'Submitted' else []
        short = [r for r in sales.atp(order) if r['short'] > 0] if order.status == 'Submitted' else []
        sale = {'progress': prog, 'stage': st, 'credit': credit, 'short': short,
                'below': any(l.below_list for l in lines) and order.status == 'Submitted',
                'can_override': u.has_perm('erp.override_credit_sales_order'), 'can_price': u.has_perm('erp.approve_price_exception'),
                'can_hold': u.has_perm('erp.hold_sales_order') and order.status in ('Submitted', 'Approved', 'Part dispatched'),
                'can_release': u.has_perm('erp.hold_sales_order') and order.status == 'On hold',
                'can_revise': order_permission(u, 'Sale', 'create') and order.status in ('Submitted', 'Approved', 'On hold') and not order.dispatches.exclude(status='cancelled').exists() and not invoices,
                'override_max': (timezone.localdate() + timedelta(days=30)).isoformat(), 'today': timezone.localdate().isoformat(),
                'submit_problems': sales.submit_checks(order) if order.status == 'Draft' else [],
                'revisions': list(Order.objects.filter(Q(pk=order.revised_from_id) | Q(revised_from=order)).only('number', 'status', 'revision'))}
    history = AuditEvent.objects.filter(reference=order.number).select_related('actor').order_by('-created_at')[:8]
    return render(request, 'erp/order_detail.html', {'title': order.number, 'order': order, 'lines': lines, 'tax': tax, 'words': in_words(tax['grand_total']),
                                                     'checks': checks, 'history': history, 'requests': requests,
                                                     'grns': order.grns.prefetch_related('lines__product') if order.kind == 'Purchase' else [], 'steps': steps, 'invoice': invoice, 'invoices': invoices,
                                                     'dispatches': order.dispatches.select_related('vehicle', 'invoice').prefetch_related('lines__order_line__product') if order.kind == 'Sale' else [], 'sale': sale, **actions})


# ---------- Order list (sales & purchase) ----------

LIST_FILTERS = {
    'Sale': [('open', 'All open'), ('Draft', 'Draft'), ('Submitted', 'Approval pending'), ('Approved', 'Approved · to dispatch'), ('Part dispatched', 'Part dispatched'),
             ('On hold', 'On hold'), ('late', 'Past requested date'), ('urgent', 'Urgent'), ('Completed', 'Completed'), ('Cancelled', 'Cancelled'), ('Revised', 'Revised'), ('all', 'All')],
    'Purchase': [('open', 'All open'), ('Draft', 'Draft'), ('Submitted', 'Approval pending'), ('Approved', 'Approved · to receive'), ('Part received', 'Part received'),
                 ('late', 'Past delivery date'), ('Completed', 'Completed'), ('Cancelled', 'Cancelled'), ('all', 'All')],
}
OPEN_STATES = ['Draft', 'Submitted', 'Approved', 'Part dispatched', 'Part received', 'On hold']


@login_required
def order_list(request, kind):
    from django.core.paginator import Paginator
    from .models import DispatchLine, GRNLine
    _check(request.user, kind, 'view')
    G = request.GET
    base = scope(Order.objects.filter(kind=kind), request.user)
    today = timezone.localdate()
    status, q, site = G.get('status', 'open'), G.get('q', '').strip(), G.get('site', '')
    qs = base.select_related('partner', 'site').prefetch_related('lines')
    if status == 'open': qs = qs.filter(status__in=OPEN_STATES)
    elif status == 'late': qs = qs.filter(status__in=['Approved', 'Part dispatched', 'Part received', 'On hold'], due_date__lt=today)
    elif status == 'urgent': qs = qs.filter(status__in=OPEN_STATES, priority='urgent')
    elif status != 'all': qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(number__icontains=q) | Q(partner__name__icontains=q) | Q(customer_ref__icontains=q))
    if site.isdigit(): qs = qs.filter(site_id=site)
    try:
        if G.get('from'): qs = qs.filter(created_at__date__gte=G['from'])
        if G.get('to'): qs = qs.filter(created_at__date__lte=G['to'])
    except ValidationError:
        pass
    # Dispatch view: urgent first, then by requested date. Otherwise newest first.
    qs = qs.order_by('-priority', 'due_date', '-created_at') if kind == 'Sale' and status in ('Approved', 'open', 'late') else qs.order_by('-created_at')
    page = Paginator(qs, 25).get_page(G.get('page'))
    orders = list(page.object_list)
    ids = [o.pk for o in orders]
    moved = {}
    if kind == 'Sale':
        for r in DispatchLine.objects.filter(order_line__order_id__in=ids, dispatch__stock_posted=True).values('order_line__order').annotate(q=Sum('loaded_quantity')):
            moved[r['order_line__order']] = r['q'] or Decimal('0')
    else:
        for r in GRNLine.objects.filter(po_line__order_id__in=ids, grn__status='posted').values('po_line__order').annotate(q=Sum('accepted'), h=Sum('qc_accepted')):
            moved[r['po_line__order']] = (r['q'] or 0) + (r['h'] or 0)
    for o in orders:
        total = sum((l.quantity for l in o.lines.all()), Decimal('0'))
        done = total if o.status == 'Completed' and not o.short_close_reason else moved.get(o.pk, Decimal('0'))
        o.pct = min(int(done * 100 / total), 100) if total else 0
        o.value = sum((l.quantity * l.rate for l in o.lines.all()), Decimal('0'))
        o.late = o.status in ('Approved', 'Part dispatched', 'Part received', 'On hold') and o.due_date < today
    open_base = base.filter(status__in=OPEN_STATES)
    kpi = {'approval': open_base.filter(status='Submitted').count(), 'to_move': open_base.filter(status__in=['Approved', 'Part dispatched', 'Part received']).count(),
           'held': open_base.filter(status='On hold').count(), 'late': open_base.filter(status__in=['Approved', 'Part dispatched', 'Part received', 'On hold'], due_date__lt=today).count(),
           'drafts': open_base.filter(status='Draft').count()}
    params = G.copy(); params.pop('page', None)
    return render(request, 'erp/orders.html', {'title': 'Sales orders' if kind == 'Sale' else 'Purchase orders', 'kind': kind, 'page': page, 'orders': orders, 'kpi': kpi,
                                               'filters': LIST_FILTERS[kind], 'status': status, 'q': q, 'site': site, 'sites': _sites(request.user).order_by('code'),
                                               'date_from': G.get('from', ''), 'date_to': G.get('to', ''), 'qs': params.urlencode(),
                                               'can_create': order_permission(request.user, kind, 'create')})
