"""Inbound flow (SRS §4.2, FR-PUR-004…007): gate-in → gross → unload → tare → GRN → quality release.

Controls enforced here (screens and the future API both call these functions):
- only approved purchase orders can receive; one supplier challan number is accepted once;
- weighbridge readings are never edited — a reweigh adds a linked reading with a reason; manual entry needs a reason;
- at weighbridge plants, the received weight of MT / KG items must agree with net weight within the plant tolerance;
- received = accepted + rejected + damaged + quality hold on every line; rejected / damaged need a reason;
- accepted + hold can never exceed the PO quantity plus its over-receipt tolerance (excess is rejected and returned);
- shortage or excess against the supplier's challan beyond tolerance needs a recorded reason;
- only accepted quantity enters stock, once, with a unique source per line; held quantity enters only after a quality
  user other than the GRN poster releases it;
- the PO moves to Part received / Completed automatically.
"""
from accounts.delegation import authorize, can_approve
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .gst import next_number, qtxt
from .models import GoodsReceipt, GRNLine, Weighment, Order, OrderLine, AuditEvent
from .services import move_stock, require_site

ZERO = Decimal('0')
RECEIVABLE = ('Approved', 'Part received')


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, g, action, detail=''):
    AuditEvent.objects.create(actor=user, site=g.site, action=action, reference=g.number, detail=detail[:250])


def _lock(pk, user):
    obj = GoodsReceipt.objects.select_for_update().select_related('site', 'order', 'supplier').get(pk=pk)
    require_site(user, obj.site)
    return obj


def _state(g, *allowed):
    if g.status not in allowed:
        raise ValidationError(f'{g.number} is "{g.get_status_display()}" — this step is not possible now.')


def unit_kg(product):
    return {'MT': Decimal('1000'), 'KG': Decimal('1')}.get(product.unit)


def allowance(po_line, exclude_grn=None):
    """How much more of this PO line may be accepted or held (PO qty + tolerance − already received)."""
    cap = po_line.quantity * (1 + po_line.order.receipt_tolerance_pct / 100)
    done = ZERO
    for gl in po_line.grn_lines.filter(grn__status='posted'):
        if exclude_grn and gl.grn_id == exclude_grn:
            continue
        done += gl.accepted + gl.hold
    return max(cap - done, ZERO)


def clean_vehicle(text):
    return ''.join(ch for ch in (text or '').upper() if ch.isalnum())


# ---------- Gate ----------

@transaction.atomic
def gate_in(user, order, data, challan_qty, photo=None):
    """data: vehicle_number, driver_name, driver_mobile, transporter_name, challan_number, challan_date, eway_bill, notes. challan_qty: {OrderLine: qty}."""
    _need(user, 'add_goodsreceipt')
    order = Order.objects.select_for_update().select_related('site', 'partner').get(pk=order.pk)
    require_site(user, order.site)
    problems = []
    if order.kind != 'Purchase' or order.status not in RECEIVABLE:
        problems.append('Only approved purchase orders can receive material.')
    if not order.partner.active:
        problems.append(f'{order.partner.name} is inactive.')
    vehicle = clean_vehicle(data.get('vehicle_number'))
    if len(vehicle) < 6:
        problems.append('Enter the vehicle registration number.')
    challan = (data.get('challan_number') or '').strip().upper()
    if not challan:
        problems.append("Enter the supplier's invoice / challan number.")
    elif GoodsReceipt.objects.filter(supplier=order.partner, challan_number__iexact=challan).exclude(status='cancelled').exists():
        problems.append(f'Challan {challan} from {order.partner.name} has already been received.')
    if data.get('challan_date') and data['challan_date'] > timezone.localdate():
        problems.append('Challan date cannot be in the future.')
    mobile = ''.join(ch for ch in (data.get('driver_mobile') or '') if ch.isdigit())[-10:]
    if data.get('driver_mobile') and len(mobile) != 10:
        problems.append('Driver mobile must have 10 digits.')
    eway = ''.join(ch for ch in (data.get('eway_bill') or '') if ch.isdigit())
    if eway and len(eway) != 12:
        problems.append('E-way bill number has 12 digits.')
    if GoodsReceipt.objects.filter(vehicle_number=vehicle, status__in=['gate_in', 'weighed', 'unloaded']).exists():
        problems.append(f'{vehicle} is already inside the plant on another receipt.')
    picked = {l: Decimal(q) for l, q in challan_qty.items() if q not in (None, '') and Decimal(q) > 0}
    if not picked:
        problems.append("Enter the quantity on the supplier's challan for at least one item.")
    for l in picked:
        if l.order_id != order.pk:
            raise ValidationError('Item does not belong to this order.')
    if problems:
        raise ValidationError(problems)
    g = GoodsReceipt.objects.create(number=next_number('GRN', order.site), site=order.site, order=order, supplier=order.partner, vehicle_number=vehicle,
                                    driver_name=(data.get('driver_name') or '').strip()[:80], driver_mobile=mobile, transporter_name=(data.get('transporter_name') or '').strip()[:100],
                                    challan_number=challan[:40], challan_date=data.get('challan_date'), eway_bill=eway, notes=(data.get('notes') or '')[:200], gate_in_by=user,
                                    status='gate_in' if order.site.uses_weighbridge else 'unloaded')
    if photo:
        g.photo, g.photo_type = photo
        g.save(update_fields=['photo', 'photo_type'])
    for l, q in picked.items():
        GRNLine.objects.create(grn=g, po_line=l, product=l.product, challan_qty=q)
    _log(user, g, 'Inbound gate-in', f'{vehicle} · {order.number} · challan {challan}')
    return g


# ---------- Weighbridge ----------

@transaction.atomic
def weigh(user, pk, kind, weight_kg, source='manual', reason=''):
    """Inbound trucks come loaded: gross first, then tare after unloading. A second reading of the same kind is a reweigh."""
    _need(user, 'weigh_dispatch')
    g = _lock(pk, user)
    if not g.site.uses_weighbridge:
        raise ValidationError(f'{g.site.code} does not use a weighbridge.')
    reason = (reason or '').strip()
    try:
        weight = int(Decimal(str(weight_kg)))
    except Exception:
        raise ValidationError('Enter the weight in kilograms.')
    if weight <= 0 or weight > 80000:
        raise ValidationError('Weight must be between 1 and 80,000 kg.')
    if source == 'manual' and not reason:
        raise ValidationError('Manual entry needs a reason (e.g. scale interface offline).')
    current = g.reading(kind)
    if current:
        _state(g, 'weighed', 'unloaded') if kind == 'gross' else _state(g, 'unloaded')
        if not reason:
            raise ValidationError('A reweigh needs a reason.')
    else:
        _state(g, 'gate_in') if kind == 'gross' else _state(g, 'weighed')
    if kind == 'tare' and weight >= g.gross.weight_kg:
        raise ValidationError(f'Tare {weight:,} kg must be less than gross {g.gross.weight_kg:,} kg.')
    if kind == 'gross' and g.tare and weight <= g.tare.weight_kg:
        raise ValidationError(f'Gross {weight:,} kg must be more than tare {g.tare.weight_kg:,} kg.')
    w = Weighment.objects.create(grn=g, kind=kind, weight_kg=weight, source=source, scale_id=g.site.scale_id, reason=reason, captured_by=user)
    if current:
        current.superseded_by = w
        current.save(update_fields=['superseded_by'])
        _log(user, g, 'Reweigh', f'{kind} {current.weight_kg:,} → {weight:,} kg · {reason}')
    else:
        _log(user, g, f'{kind.title()} weight', f'{weight:,} kg ({source}){" · " + reason if reason else ""}')
    if not current:
        g.status = 'weighed' if kind == 'gross' else 'unloaded'
        g.save(update_fields=['status'])
    return w


# ---------- GRN ----------

def suggested_received(g):
    """Single weight-unit line at a weighbridge plant: received = net weight. Otherwise the challan quantity."""
    lines = list(g.lines.select_related('product'))
    weight_lines = [l for l in lines if unit_kg(l.product)]
    out = {}
    for l in lines:
        out[l.pk] = l.challan_qty
    if g.net_kg and len(weight_lines) == 1:
        l = weight_lines[0]
        out[l.pk] = (Decimal(g.net_kg) / unit_kg(l.product)).quantize(Decimal('0.001'))
    return out


@transaction.atomic
def post(user, pk, rows, variance_reason=''):
    """rows: {GRNLine: {'received', 'accepted', 'rejected', 'damaged', 'hold', 'reason'}} — all Decimals except reason."""
    _need(user, 'post_goodsreceipt')
    g = _lock(pk, user)
    _state(g, 'unloaded')
    order = Order.objects.select_for_update().get(pk=g.order_id)
    if order.status not in RECEIVABLE:
        raise ValidationError(f'{order.number} is {order.status} — it cannot receive.')
    variance_reason = (variance_reason or '').strip()
    problems, weight_kg, variance = [], ZERO, False
    lines = list(g.lines.select_related('product', 'po_line__order'))
    for l in lines:
        r = rows.get(l)
        if r is None:
            problems.append(f'{l.product.name}: enter the quantities.')
            continue
        vals = {k: r.get(k) or ZERO for k in ('received', 'accepted', 'rejected', 'damaged', 'hold')}
        if any(v < 0 for v in vals.values()):
            problems.append(f'{l.product.name}: quantities cannot be negative.')
            continue
        split = vals['accepted'] + vals['rejected'] + vals['damaged'] + vals['hold']
        if split != vals['received']:
            problems.append(f'{l.product.name}: accepted + rejected + damaged + hold = {qtxt(split)}, but received is {qtxt(vals["received"])}.')
        if (vals['rejected'] or vals['damaged']) and not (r.get('reason') or '').strip():
            problems.append(f'{l.product.name}: give the reason for rejected / damaged quantity.')
        room = allowance(l.po_line, exclude_grn=g.pk)
        if vals['accepted'] + vals['hold'] > room:
            problems.append(f'{l.product.name}: only {qtxt(room)} {l.product.unit} more can be taken on {order.number} (incl. {qtxt(order.receipt_tolerance_pct)}% tolerance). Reject the excess.')
        tol = l.challan_qty * g.site.weight_tolerance_pct / 100
        if abs(vals['received'] - l.challan_qty) > tol:
            variance = True
        kg = unit_kg(l.product)
        if kg:
            weight_kg += vals['received'] * kg
        r.update(vals)
    if g.site.uses_weighbridge and weight_kg and g.net_kg:
        net = Decimal(g.net_kg)
        if abs(weight_kg - net) > net * g.site.weight_tolerance_pct / 100:
            problems.append(f'Received {qtxt(weight_kg / 1000)} MT does not match net weight {qtxt(net / 1000)} MT (±{qtxt(g.site.weight_tolerance_pct)}%).')
    if variance and not variance_reason:
        problems.append("Received quantity differs from the supplier's challan — record the reason for the shortage / excess.")
    if problems:
        raise ValidationError(problems)
    for l in lines:
        r = rows[l]
        l.received, l.accepted, l.rejected, l.damaged, l.hold, l.reject_reason = r['received'], r['accepted'], r['rejected'], r['damaged'], r['hold'], (r.get('reason') or '').strip()[:150]
        l.save()
        if l.accepted:
            move_stock(user, g.site, l.product, l.accepted, f'grn:{g.pk}:line:{l.pk}', 'Receipt', f'{g.number} · {g.supplier.name}', unit_cost=l.po_line.rate)
    g.status, g.posted_at, g.posted_by, g.variance_reason = 'posted', timezone.now(), user, variance_reason[:200]
    g.save()
    refresh_order(order)
    summary = ', '.join(f'{l.product.code} {qtxt(l.accepted)}' + (f' (+{qtxt(l.hold)} hold)' if l.hold else '') + (f' (−{qtxt(l.rejected + l.damaged)} rej.)' if l.rejected or l.damaged else '') for l in lines)
    _log(user, g, 'GRN posted', summary)
    return g


def refresh_order(order):
    lines = list(order.lines.all())
    full = all(l.received_quantity() >= l.quantity for l in lines)
    some = any(l.received_quantity() > 0 for l in lines)
    status = 'Completed' if full else 'Part received' if some else order.status
    if status != order.status:
        order.status = status
        order.save(update_fields=['status'])
        from .procurement import refresh_requests
        refresh_requests(order)


@transaction.atomic
def release(user, line_pk, accept, reject, note):
    """Quality decision on held quantity. Accepted part enters stock; rejected part goes back to the supplier (debit note)."""
    l = GRNLine.objects.select_for_update().select_related('grn__site', 'grn__supplier', 'product', 'po_line').get(pk=line_pk)
    require_site(user, l.grn.site)
    g = l.grn
    authorize(user, 'release_goodsreceipt', g.site, makers=(g.posted_by_id,), reference=g.number)
    note = (note or '').strip()
    accept, reject = Decimal(accept or 0), Decimal(reject or 0)
    if g.status != 'posted' or not l.hold:
        raise ValidationError('Nothing on quality hold for this item.')
    if l.qc_at:
        raise ValidationError('This hold has already been decided.')
    if g.posted_by_id == user.pk:
        raise ValidationError('The person who posted the GRN cannot release its quality hold.')
    if accept < 0 or reject < 0 or accept + reject != l.hold:
        raise ValidationError(f'Accepted + rejected must equal the {qtxt(l.hold)} {l.product.unit} on hold.')
    if not note:
        raise ValidationError('Record the test result or reason (e.g. lab report number).')
    l.qc_accepted, l.qc_rejected, l.qc_note, l.qc_by, l.qc_at = accept, reject, note[:200], user, timezone.now()
    l.save()
    if accept:
        move_stock(user, g.site, l.product, accept, f'grn:{g.pk}:line:{l.pk}:qc', 'Receipt', f'{g.number} quality release', unit_cost=l.po_line.rate)
    _log(user, g, 'Quality release', f'{l.product.code}: accepted {qtxt(accept)}, rejected {qtxt(reject)} · {note}')
    return l


@transaction.atomic
def cancel(user, pk, reason):
    _need(user, 'add_goodsreceipt')
    g = _lock(pk, user)
    _state(g, 'gate_in', 'weighed', 'unloaded')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Give the reason (e.g. supplier truck sent back, wrong material).')
    g.status, g.cancel_reason = 'cancelled', reason[:150]
    g.save(update_fields=['status', 'cancel_reason'])
    _log(user, g, 'Inbound cancelled', reason)
    return g


def value_of(line):
    """Value of a GRN line at the PO rate (used for supplier invoice matching and debit notes)."""
    return (line.po_line.rate * line.usable).quantize(Decimal('0.01'))
