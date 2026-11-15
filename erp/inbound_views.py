"""Inbound screens: receiving board, gate-in against a PO, weighbridge, GRN posting, quality release, GRN print."""
from accounts.delegation import can_approve
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

from . import inbound as IB
from .logistics_views import POD_TYPES
from .models import GoodsReceipt, GRNLine, Order, AuditEvent, Organization
from .services import permitted, scope


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return 'bad'


def _dec(value):
    text = str(value or '').replace(',', '').strip()
    return Decimal(text) if text else Decimal('0')


@login_required
def inbound(request):
    permitted(request.user, 'view_goodsreceipt')
    grns = scope(GoodsReceipt.objects.select_related('site', 'supplier', 'order'), request.user)
    inside = grns.filter(status__in=['gate_in', 'weighed', 'unloaded']).order_by('gate_in_at')
    today = timezone.localdate()
    held = scope(GRNLine.objects.filter(grn__status='posted', hold__gt=0, qc_at__isnull=True).select_related('grn__supplier', 'product'), request.user, 'grn__site')
    pos = [o for o in scope(Order.objects.filter(kind='Purchase', status__in=['Approved', 'Part received']).select_related('partner', 'site').prefetch_related('lines__product'),
                            request.user).order_by('due_date')[:100]]
    for o in pos:
        o.pending = [(l, l.to_receive) for l in o.lines.all() if l.to_receive > 0]
        o.late = o.due_date < today
    q = request.GET.get('q', '').strip()
    recent = grns.exclude(status__in=['gate_in', 'weighed', 'unloaded'])
    if q:
        recent = recent.filter(Q(number__icontains=q) | Q(vehicle_number__icontains=q.replace(' ', '')) | Q(challan_number__icontains=q) | Q(supplier__name__icontains=q))
    return render(request, 'erp/inbound.html', {
        'title': 'Goods receipt', 'inside': inside, 'held': held, 'pos': [o for o in pos if o.pending], 'recent': recent.order_by('-posted_at', '-gate_in_at')[:60], 'q': q,
        'today_count': grns.filter(status='posted', posted_at__date=today).count(),
        'can_gate': request.user.has_perm('erp.add_goodsreceipt')})


@login_required
def grn_new(request, order_pk):
    permitted(request.user, 'add_goodsreceipt')
    order = get_object_or_404(scope(Order.objects.filter(kind='Purchase').select_related('partner', 'site'), request.user), pk=order_pk)
    lines = [l for l in order.lines.select_related('product') if l.to_receive > 0]
    for l in lines:
        l.qty_in = request.POST.get(f'qty_{l.pk}', '')
    errors = []
    if request.method == 'POST':
        P = request.POST
        challan_date = _date(P.get('challan_date'))
        photo = None
        f = request.FILES.get('photo')
        if challan_date == 'bad':
            errors.append('Enter a valid challan date.')
        if f:
            data = f.read(5 * 1024 * 1024 + 1)
            kind = next((t for sig, t in POD_TYPES.items() if data.startswith(sig)), None)
            if len(data) > 5 * 1024 * 1024 or not kind:
                errors.append('Photo / document must be a PDF, PNG, JPEG or WebP up to 5 MB.')
            photo = (data, kind)
        qty = {}
        for l in lines:
            try:
                qty[l] = _dec(l.qty_in) or None
            except InvalidOperation:
                errors.append(f'{l.product.name}: enter a number.')
        if not errors:
            data = {k: P.get(k, '') for k in ['vehicle_number', 'driver_name', 'driver_mobile', 'transporter_name', 'challan_number', 'eway_bill', 'notes']}
            data['challan_date'] = challan_date
            try:
                g = IB.gate_in(request.user, order, data, qty, photo)
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{g.number}: {g.vehicle_number} inside. ' + ('Send it to the weighbridge for gross weight.' if g.status == 'gate_in' else 'Unload and post the GRN.'))
                return redirect('grn_detail', pk=g.pk)
    return render(request, 'erp/grn_new.html', {'title': f'Gate-in · {order.number}', 'order': order, 'lines': lines, 'errors': errors, 'post': request.POST,
                                                'today': timezone.localdate().isoformat()})


@login_required
def grn_detail(request, pk):
    permitted(request.user, 'view_goodsreceipt')
    g = get_object_or_404(scope(GoodsReceipt.objects.select_related('site', 'supplier', 'order', 'gate_in_by', 'posted_by'), request.user), pk=pk)
    lines = list(g.lines.select_related('product', 'po_line', 'qc_by'))
    suggest = IB.suggested_received(g) if g.status == 'unloaded' else {}
    saved = request.session.pop(f'grn{g.pk}', {})
    for l in lines:
        l.room = IB.allowance(l.po_line, exclude_grn=g.pk)
        l.suggest = suggest.get(l.pk, l.challan_qty)
        l.f = {k: saved.get(f'{k}_{l.pk}', '') for k in ['received', 'accepted', 'rejected', 'damaged', 'hold', 'reason']}
    u = request.user
    weigh_next = 'gross' if g.status == 'gate_in' else 'tare' if g.status == 'weighed' else None
    return render(request, 'erp/grn_detail.html', {
        'title': g.number, 'g': g, 'lines': lines, 'weighments': g.weighments.select_related('captured_by'), 'weigh_next': weigh_next, 'variance_reason': saved.get('variance_reason', ''),
        'history': AuditEvent.objects.filter(reference=g.number).select_related('actor').order_by('-created_at')[:12],
        'can': {'weigh': u.has_perm('erp.weigh_dispatch'), 'post': u.has_perm('erp.post_goodsreceipt'), 'cancel': u.has_perm('erp.add_goodsreceipt'),
                'release': can_approve(u, 'release_goodsreceipt', g.site, makers=(g.posted_by_id,)) and g.posted_by_id != u.pk}})


@login_required
@require_POST
def grn_action(request, pk, action):
    get_object_or_404(scope(GoodsReceipt.objects.all(), request.user), pk=pk)
    P, u = request.POST, request.user
    try:
        if action in ('gross', 'tare'):
            w = IB.weigh(u, pk, action, P.get('weight_kg'), P.get('source', 'manual'), P.get('reason', ''))
            msg = f'{action.title()} {w.weight_kg:,} kg recorded.' + (' Unload the truck, then take the empty (tare) weight.' if action == 'gross' else '')
        elif action == 'post':
            g = GoodsReceipt.objects.get(pk=pk)
            rows = {}
            try:
                for l in g.lines.all():
                    rows[l] = {k: _dec(P.get(f'{k}_{l.pk}')) for k in ['received', 'accepted', 'rejected', 'damaged', 'hold']}
                    rows[l]['reason'] = P.get(f'reason_{l.pk}', '')
            except InvalidOperation:
                raise ValidationError('Enter numbers only.')
            try:
                IB.post(u, pk, rows, P.get('variance_reason', ''))
            except ValidationError:
                request.session[f'grn{pk}'] = {k: v for k, v in P.items() if k != 'csrfmiddlewaretoken'}
                raise
            msg = 'GRN posted. Accepted quantity is in stock.'
        elif action == 'release':
            l = get_object_or_404(GRNLine, pk=P.get('line') or 0, grn_id=pk)
            IB.release(u, l.pk, _dec(P.get('accept')), _dec(P.get('reject')), P.get('note', ''))
            msg = 'Quality decision recorded.'
        elif action == 'cancel':
            IB.cancel(u, pk, P.get('reason', ''))
            msg = 'Receipt cancelled. The truck may leave with the material.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except InvalidOperation:
        messages.error(request, 'Enter numbers only.')
    return redirect('grn_detail', pk=pk)


@login_required
def grn_print(request, pk):
    permitted(request.user, 'view_goodsreceipt')
    g = get_object_or_404(scope(GoodsReceipt.objects.select_related('site', 'supplier', 'order', 'posted_by', 'gate_in_by'), request.user), pk=pk)
    if g.status != 'posted':
        messages.error(request, 'The GRN prints after it is posted.')
        return redirect('grn_detail', pk=pk)
    lines = list(g.lines.select_related('product', 'po_line'))
    for l in lines:
        l.value = IB.value_of(l)
    return render(request, 'erp/grn_print.html', {'title': f'GRN {g.number}', 'g': g, 'lines': lines, 'weighments': g.weighments.select_related('captured_by'),
                                                  'company': Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization(), 'now': timezone.now()})


@login_required
@xframe_options_sameorigin
def grn_photo(request, pk):
    permitted(request.user, 'view_goodsreceipt')
    g = get_object_or_404(scope(GoodsReceipt.objects.all(), request.user), pk=pk)
    if not g.photo:
        raise Http404
    response = HttpResponse(bytes(g.photo), content_type=g.photo_type)
    response['Content-Disposition'] = f'inline; filename="gate-{g.number.replace("/", "-")}"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response
