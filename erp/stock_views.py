"""Inventory screens: stock position by state, stock card (ledger with running balance and cost), transfers, damaged stock."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from . import stock as ST
from .logistics_views import POD_TYPES
from .models import (Site, Product, StockMovement, StockTransfer, DamageReport, AuditEvent, PRODUCT_CATEGORIES, DAMAGE_REASONS, StockBalance)
from .services import permitted, scope


def _sites(user):
    qs = Site.objects.filter(active=True).order_by('code')
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


def _dec(value):
    text = str(value or '').replace(',', '').strip()
    return Decimal(text) if text else Decimal('0')


def _file(request, name):
    f = request.FILES.get(name)
    if not f:
        return None
    data = f.read(5 * 1024 * 1024 + 1)
    kind = next((t for sig, t in POD_TYPES.items() if data.startswith(sig)), None)
    if len(data) > 5 * 1024 * 1024 or not kind:
        raise ValidationError('Photo must be a PDF, PNG, JPEG or WebP up to 5 MB.')
    return data, kind


@login_required
def inventory(request):
    permitted(request.user, 'view_stockbalance')
    sites = list(_sites(request.user))
    f = {k: request.GET.get(k, '').strip() for k in ('site', 'category', 'q', 'show')}
    chosen = [s for s in sites if str(s.pk) == f['site']] or sites
    products = Product.objects.all()
    if f['category']: products = products.filter(category=f['category'])
    if f['q']: products = products.filter(Q(name__icontains=f['q']) | Q(code__icontains=f['q']))
    rows = ST.position(chosen, list(products) if (f['category'] or f['q']) else None)
    if f['show'] == 'low': rows = [r for r in rows if r['low']]
    elif f['show'] == 'damaged': rows = [r for r in rows if r['damaged']]
    elif f['show'] == 'short': rows = [r for r in rows if r['free'] < 0]
    totals = {'value': sum((r['value'] for r in rows), Decimal('0')), 'low': sum(1 for r in rows if r['low']),
              'damaged': sum((r['damaged'] * r['cost'] for r in rows), Decimal('0')), 'short': sum(1 for r in rows if r['free'] < 0)}
    transit = scope(StockTransfer.objects.filter(status='in_transit'), request.user, 'to_site').count()
    return render(request, 'erp/inventory.html', {'title': 'Inventory', 'rows': rows, 'f': f, 'sites': sites, 'categories': PRODUCT_CATEGORIES, 'totals': totals,
                                                  'transit': transit, 'can_open': request.user.has_perm('erp.post_opening')})


@login_required
def stock_card(request, site_pk, product_pk):
    permitted(request.user, 'view_stockmovement')
    site = get_object_or_404(_sites(request.user), pk=site_pk)
    product = get_object_or_404(Product, pk=product_pk)
    status = request.GET.get('status', 'available')
    moves = StockMovement.objects.filter(site=site, product=product, status=status).select_related('created_by').order_by('-created_at', '-pk')[:300]
    bal = StockBalance.objects.filter(site=site, product=product, status=status).first()
    return render(request, 'erp/stock_card.html', {'title': f'Stock card · {product.code}', 'site': site, 'product': product, 'moves': moves, 'bal': bal, 'status': status,
                                                   'bal_mt': (bal.quantity * ST.mt_factor(product)).quantize(Decimal('0.001')) if bal and ST.mt_factor(product) else None})


# ---------- Transfers ----------

@login_required
def transfers(request):
    permitted(request.user, 'view_stocktransfer')
    mine = _sites(request.user)
    qs = StockTransfer.objects.select_related('from_site', 'to_site', 'created_by').prefetch_related('lines__product')
    if not request.user.is_superuser:
        qs = qs.filter(Q(from_site__in=mine) | Q(to_site__in=mine))
    status = request.GET.get('status', '')
    if status: qs = qs.filter(status=status)
    base = StockTransfer.objects.filter(pk__in=qs.values('pk')) if not status else qs
    return render(request, 'erp/transfers.html', {'title': 'Stock transfers', 'transfers': qs[:200], 'status': status, 'statuses': StockTransfer._meta.get_field('status').choices,
                                                  'counts': {k: StockTransfer.objects.filter(pk__in=base.values('pk'), status=k).count() for k in ('requested', 'approved', 'in_transit', 'discrepancy')},
                                                  'can_add': request.user.has_perm('erp.add_stocktransfer')})


@login_required
def transfer_new(request):
    permitted(request.user, 'add_stocktransfer')
    sites = list(_sites(request.user))
    all_sites = list(Site.objects.filter(active=True).order_by('code'))
    products = list(Product.objects.filter(active=True).order_by('category', 'name'))
    P = request.POST
    errors = []
    rows = [{'product': P.get(f'product_{i}', ''), 'qty': P.get(f'qty_{i}', '')} for i in range(8)]
    if request.method == 'POST':
        src = next((s for s in sites if str(s.pk) == P.get('from_site')), None)
        dst = next((s for s in all_sites if str(s.pk) == P.get('to_site')), None)
        pmap = {str(p.pk): p for p in products}
        try:
            lines = [(pmap[r['product']], _dec(r['qty'])) for r in rows if r['product'] in pmap and r['qty']]
            nb = date.fromisoformat(P['needed_by']) if P.get('needed_by') else None
        except (InvalidOperation, ValueError):
            lines, nb = [], None
            errors.append('Enter numbers and dates correctly.')
        if not errors:
            try:
                t = ST.request_transfer(request.user, src, dst, P.get('reason', ''), lines, nb)
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{t.number} requested. A different user approves it.')
                return redirect('transfer_detail', pk=t.pk)
    avail = {(b.site_id, b.product_id): b.quantity for b in StockBalance.objects.filter(status='available', site__in=sites)}
    return render(request, 'erp/transfer_new.html', {'title': 'New stock transfer', 'sites': sites, 'all_sites': all_sites, 'products': products, 'rows': rows,
                                                     'errors': errors, 'post': P, 'avail': {f'{k[0]}:{k[1]}': str(v.normalize()) for k, v in avail.items()}})


@login_required
def transfer_detail(request, pk):
    permitted(request.user, 'view_stocktransfer')
    t = get_object_or_404(StockTransfer.objects.select_related('from_site', 'to_site', 'created_by', 'approved_by', 'dispatched_by', 'received_by', 'resolved_by'), pk=pk)
    mine = set(_sites(request.user).values_list('pk', flat=True))
    if not request.user.is_superuser and not ({t.from_site_id, t.to_site_id} & mine):
        raise Http404
    lines = list(t.lines.select_related('product'))
    for l in lines:
        l.on_hand = StockBalance.objects.filter(site=t.from_site, product=l.product, status='available').values_list('quantity', flat=True).first() or Decimal('0')
    u = request.user
    return render(request, 'erp/transfer_detail.html', {
        'title': t.number, 't': t, 'lines': lines, 'loss': ST.transit_loss(t) if t.status in ('discrepancy', 'received') else None,
        'history': AuditEvent.objects.filter(reference=t.number).select_related('actor').order_by('-created_at')[:12],
        'can': {'decide': t.status == 'requested' and u.has_perm('erp.approve_stocktransfer') and t.created_by_id != u.pk and (u.is_superuser or t.from_site_id in mine),
                'dispatch': t.status == 'approved' and u.has_perm('erp.dispatch_stocktransfer') and (u.is_superuser or t.from_site_id in mine),
                'receive': t.status == 'in_transit' and u.has_perm('erp.receive_stocktransfer') and t.dispatched_by_id != u.pk and (u.is_superuser or t.to_site_id in mine),
                'resolve': t.status == 'discrepancy' and u.has_perm('erp.approve_stocktransfer') and u.pk not in (t.received_by_id, t.dispatched_by_id) and (u.is_superuser or t.to_site_id in mine),
                'cancel': t.status in ('requested', 'approved') and u.has_perm('erp.add_stocktransfer') and (u.is_superuser or t.from_site_id in mine)}})


@login_required
@require_POST
def transfer_action(request, pk, action):
    t = get_object_or_404(StockTransfer, pk=pk)
    mine = set(_sites(request.user).values_list('pk', flat=True))
    if not request.user.is_superuser and not ({t.from_site_id, t.to_site_id} & mine):
        raise Http404
    P, u = request.POST, request.user
    try:
        if action in ('approve', 'reject'):
            ST.decide_transfer(u, pk, action == 'approve', P.get('note', '')); msg = 'Decision recorded.'
        elif action == 'dispatch':
            ST.dispatch_transfer(u, pk, {l.pk: _dec(P.get(f'qty_{l.pk}')) for l in t.lines.all()}, P.get('vehicle', ''), P.get('driver', ''), P.get('lr', ''), P.get('eway', ''))
            msg = 'Dispatched. Stock is in transit.'
        elif action == 'receive':
            r = ST.receive_transfer(u, pk, {l.pk: _dec(P.get(f'good_{l.pk}')) for l in t.lines.all()}, {l.pk: _dec(P.get(f'damaged_{l.pk}')) for l in t.lines.all()})
            msg = 'Received.' + (' Discrepancy opened for the shortage / damage.' if r.status == 'discrepancy' else '')
        elif action == 'resolve':
            ST.resolve_transfer(u, pk, P.get('note', '')); msg = 'Discrepancy closed.'
        elif action == 'cancel':
            ST.cancel_transfer(u, pk, P.get('note', '')); msg = 'Transfer cancelled.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except InvalidOperation:
        messages.error(request, 'Enter numbers only.')
    return redirect('transfer_detail', pk=pk)


# ---------- Damaged stock ----------

@login_required
def damages(request):
    permitted(request.user, 'view_damagereport')
    qs = scope(DamageReport.objects.select_related('site', 'product', 'created_by'), request.user)
    status = request.GET.get('status', '')
    if status: qs = qs.filter(status=status)
    open_qs = scope(DamageReport.objects.filter(status='reported'), request.user)
    return render(request, 'erp/damages.html', {'title': 'Damaged stock', 'reports': qs[:200], 'status': status, 'open': open_qs.count(),
                                                'open_value': sum((d.value for d in open_qs), Decimal('0')), 'can_add': request.user.has_perm('erp.add_damagereport')})


@login_required
def damage_new(request):
    permitted(request.user, 'add_damagereport')
    sites = list(_sites(request.user))
    products = list(Product.objects.filter(active=True).order_by('category', 'name'))
    P = request.POST
    errors = []
    if request.method == 'POST':
        site = next((s for s in sites if str(s.pk) == P.get('site')), None)
        product = next((p for p in products if str(p.pk) == P.get('product')), None)
        if not site or not product:
            errors.append('Choose the location and the item.')
        try:
            photo = _file(request, 'photo')
            qty = _dec(P.get('quantity'))
        except (ValidationError, InvalidOperation) as e:
            errors += getattr(e, 'messages', ['Enter the quantity as a number.'])
            photo, qty = None, None
        if not errors:
            try:
                d = ST.report_damage(request.user, site, product, qty, P.get('reason', ''), P.get('location', ''), P.get('owner', ''), P.get('description', ''), photo)
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{d.number}: {P.get("quantity")} {product.unit} moved to damaged stock. A different user decides recovery or scrap.')
                return redirect('damage_detail', pk=d.pk)
    return render(request, 'erp/damage_new.html', {'title': 'Report damaged stock', 'sites': sites, 'products': products, 'reasons': DAMAGE_REASONS, 'errors': errors, 'post': P})


@login_required
def damage_detail(request, pk):
    permitted(request.user, 'view_damagereport')
    d = get_object_or_404(scope(DamageReport.objects.select_related('site', 'product', 'created_by', 'decided_by'), request.user), pk=pk)
    return render(request, 'erp/damage_detail.html', {'title': d.number, 'd': d,
                                                      'history': AuditEvent.objects.filter(reference=d.number).select_related('actor').order_by('-created_at')[:10],
                                                      'can_decide': d.status == 'reported' and request.user.has_perm('erp.decide_damagereport') and d.created_by_id != request.user.pk})


@login_required
@require_POST
def damage_decide(request, pk):
    get_object_or_404(scope(DamageReport.objects.all(), request.user), pk=pk)
    try:
        ST.decide_damage(request.user, pk, _dec(request.POST.get('recovered')), _dec(request.POST.get('scrapped')), request.POST.get('note', ''))
        messages.success(request, 'Disposition recorded.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except InvalidOperation:
        messages.error(request, 'Enter numbers only.')
    return redirect('damage_detail', pk=pk)


@login_required
@xframe_options_sameorigin
def damage_photo(request, pk):
    permitted(request.user, 'view_damagereport')
    d = get_object_or_404(scope(DamageReport.objects.all(), request.user), pk=pk)
    if not d.photo:
        raise Http404
    response = HttpResponse(bytes(d.photo), content_type=d.photo_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


# ---------- Stock counts & analysis ----------

from .models import StockCount  # noqa: E402


@login_required
def counts(request):
    permitted(request.user, 'view_stockcount')
    qs = scope(StockCount.objects.select_related('site', 'created_by').prefetch_related('lines'), request.user)
    errors = []
    if request.method == 'POST':
        site = next((s for s in _sites(request.user) if str(s.pk) == request.POST.get('site')), None)
        if not site:
            errors.append('Choose the location.')
        else:
            try:
                c = ST.start_count(request.user, site, request.POST.get('category', ''), request.POST.get('blind') == 'on', request.POST.get('note', ''))
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{c.number} started with {c.lines.count()} items. Print or open it on a phone and count.')
                return redirect('count_detail', pk=c.pk)
    rows = list(qs[:100])
    for c in rows:
        c.net = sum(((l.variance_value or 0) for l in c.lines.all()), Decimal('0'))
    return render(request, 'erp/counts.html', {'title': 'Stock counts', 'counts': rows, 'sites': _sites(request.user), 'categories': PRODUCT_CATEGORIES, 'errors': errors,
                                               'can_add': request.user.has_perm('erp.add_stockcount')})


@login_required
def count_detail(request, pk):
    permitted(request.user, 'view_stockcount')
    c = get_object_or_404(scope(StockCount.objects.select_related('site', 'created_by', 'submitted_by', 'approved_by'), request.user), pk=pk)
    lines = list(c.lines.select_related('product'))
    u = request.user
    entering = c.status in ('counting', 'recount') and u.has_perm('erp.add_stockcount')
    reviewing = c.status == 'submitted' and u.has_perm('erp.approve_stockcount') and c.submitted_by_id != u.pk
    show_system = not c.blind or c.status in ('submitted', 'posted', 'cancelled') and not entering or reviewing
    return render(request, 'erp/count_detail.html', {
        'title': c.number, 'c': c, 'lines': lines, 'entering': entering, 'reviewing': reviewing, 'show_system': show_system,
        'net': sum(((l.variance_value or 0) for l in lines), Decimal('0')), 'diff': sum(1 for l in lines if l.variance),
        'history': AuditEvent.objects.filter(reference=c.number).select_related('actor').order_by('-created_at')[:10]})


@login_required
@require_POST
def count_action(request, pk, action):
    c = get_object_or_404(scope(StockCount.objects.all(), request.user), pk=pk)
    P = request.POST
    try:
        if action in ('save', 'submit'):
            counted, reasons = {}, {}
            for l in c.lines.all():
                raw = P.get(f'qty_{l.pk}', '').strip()
                counted[l.pk] = _dec(raw) if raw else None
                reasons[l.pk] = P.get(f'reason_{l.pk}', '')
            ST.save_count(request.user, pk, counted, reasons, submit=action == 'submit')
            msg = 'Count submitted for approval.' if action == 'submit' else 'Count saved.'
        elif action in ('recount', 'approve'):
            ST.review_count(request.user, pk, action, [int(x) for x in P.getlist('recount') if x.isdigit()], P.get('note', ''))
            msg = 'Recount requested.' if action == 'recount' else 'Count approved — adjustments posted.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except InvalidOperation:
        messages.error(request, 'Enter numbers only.')
    return redirect('count_detail', pk=pk)


@login_required
def stock_analysis(request):
    permitted(request.user, 'view_stockmovement')
    try:
        days = max(15, min(int(request.GET.get('days', 60)), 365))
    except ValueError:
        days = 60
    rows = ST.analysis(_sites(request.user), days)
    show = request.GET.get('show', '')
    if show in ('slow', 'excess', 'low'):
        rows = [r for r in rows if r[show]]
    buckets = {}
    for r in ST.analysis(_sites(request.user), days):
        buckets[r['bucket']] = buckets.get(r['bucket'], Decimal('0')) + r['b'].value
    return render(request, 'erp/stock_analysis.html', {'title': 'Ageing & slow-moving', 'rows': rows, 'days': days, 'show': show, 'day_options': [30, 60, 90, 180],
                                                       'buckets': [(k, buckets.get(k, Decimal('0'))) for k in ('0–30', '31–60', '61–90', '90+')]})
