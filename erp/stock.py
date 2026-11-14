"""Inventory depth (SRS §5.5): stock position by state, inter-site transfers with in-transit stock, damaged-stock disposition.

Controls enforced here (screens and the future API both call these functions):
- stock states: available, damaged and blocked are real balances; reserved (open sales orders), quality hold (GRN / production)
  and in transit (dispatched transfers) are derived, so nothing is counted twice (FR-INV-002);
- a transfer is requested, approved by a different user, dispatched (stock leaves source, valued at its average cost) and
  received at the destination at the same cost; any shortage opens a discrepancy that a different user must resolve (FR-INV-005);
- damaged stock leaves available as soon as it is reported (with reason, location, owner and photo) and a different user
  decides how much is recovered and how much is scrapped (FR-INV-008).
"""
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .gst import next_number, qtxt, inr
from .models import (StockBalance, StockTransfer, StockTransferLine, DamageReport, Product, OrderLine, GRNLine, Production, AuditEvent, Organization)
from .services import move_stock, transfer_state

ZERO = Decimal('0')


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, site, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, site=site, action=action, reference=reference, detail=detail[:250])


def mt_factor(product):
    """MT per unit (bag ↔ MT conversion, FR-INV-009). None for pieces without a weight."""
    if product.unit == 'MT': return Decimal('1')
    if product.unit == 'KG': return Decimal('0.001')
    if product.unit == 'BAG':
        kg = product.bag_weight_kg or Organization.objects.filter(pk=1).values_list('default_bag_weight', flat=True).first() or Decimal('50')
        return Decimal(kg) / 1000
    return None


# ---------- Stock position ----------

def position(sites, product_filter=None):
    """One row per site × product: real balances by state plus derived reserved / quality hold / in transit."""
    site_ids = [s.pk for s in sites]
    rows = defaultdict(lambda: {'available': ZERO, 'damaged': ZERO, 'blocked': ZERO, 'reserved': ZERO, 'hold': ZERO, 'transit': ZERO,
                                'cost': ZERO, 'value': ZERO})
    bal = StockBalance.objects.filter(site_id__in=site_ids).select_related('site', 'product')
    if product_filter is not None:
        bal = bal.filter(product__in=product_filter)
    for b in bal:
        r = rows[(b.site_id, b.product_id)]
        r[b.status] += b.quantity
        r['value'] += b.value
        if b.status == 'available':
            r['cost'] = b.avg_cost
    for l in OrderLine.objects.filter(order__kind='Sale', order__status__in=['Approved', 'Part dispatched'], order__site_id__in=site_ids).select_related('order'):
        if product_filter is None or l.product_id in {p.pk for p in product_filter}:
            rows[(l.order.site_id, l.product_id)]['reserved'] += l.pending_quantity
    for l in GRNLine.objects.filter(grn__status='posted', hold__gt=0, qc_at__isnull=True, grn__site_id__in=site_ids).select_related('grn'):
        rows[(l.grn.site_id, l.product_id)]['hold'] += l.hold
    for p in Production.objects.filter(status='Quality hold', site_id__in=site_ids):
        rows[(p.site_id, p.product_id)]['hold'] += p.actual_quantity
    for l in StockTransferLine.objects.filter(transfer__status='in_transit', transfer__to_site_id__in=site_ids).select_related('transfer'):
        rows[(l.transfer.to_site_id, l.product_id)]['transit'] += l.dispatched
    products = {p.pk: p for p in Product.objects.filter(pk__in={k[1] for k in rows})}
    site_map = {s.pk: s for s in sites}
    out = []
    for (sid, pid), r in rows.items():
        if product_filter is not None and pid not in {p.pk for p in product_filter}:
            continue
        p = products[pid]
        if not any(r[k] for k in ('available', 'damaged', 'blocked', 'reserved', 'hold', 'transit')):
            continue
        f = mt_factor(p)
        r.update(site=site_map[sid], product=p, free=r['available'] - r['reserved'], mt=(r['available'] * f).quantize(Decimal('0.001')) if f is not None else None,
                 low=r['available'] <= p.reorder_level and p.reorder_level > 0)
        out.append(r)
    out.sort(key=lambda r: (r['site'].code, r['product'].category, r['product'].name))
    return out


# ---------- Transfers ----------

def _lock(pk):
    return StockTransfer.objects.select_for_update().select_related('from_site', 'to_site').get(pk=pk)


def _state(t, *allowed):
    if t.status not in allowed:
        raise ValidationError(f'{t.number} is "{t.get_status_display()}" — this step is not possible now.')


@transaction.atomic
def request_transfer(user, from_site, to_site, reason, lines, needed_by=None):
    """lines: [(Product, qty)]"""
    _need(user, 'add_stocktransfer')
    problems = []
    if not from_site or not to_site or from_site == to_site:
        problems.append('Choose two different locations.')
    if not (reason or '').strip():
        problems.append('Say why the stock is needed.')
    seen, clean = set(), []
    for product, qty in lines:
        qty = Decimal(qty or 0)
        if qty <= 0:
            continue
        if product.pk in seen:
            problems.append(f'{product.name} is listed twice.')
        seen.add(product.pk)
        clean.append((product, qty))
    if not clean:
        problems.append('Enter a quantity for at least one item.')
    if problems:
        raise ValidationError(problems)
    t = StockTransfer.objects.create(number=next_number('TRF', from_site), from_site=from_site, to_site=to_site, reason=reason.strip()[:150], needed_by=needed_by, created_by=user)
    for product, qty in clean:
        StockTransferLine.objects.create(transfer=t, product=product, requested=qty)
    _log(user, from_site, 'Transfer requested', t.number, f'{from_site.code} → {to_site.code} · {len(clean)} item(s)')
    return t


@transaction.atomic
def decide_transfer(user, pk, approve, note=''):
    _need(user, 'approve_stocktransfer')
    t = _lock(pk)
    _state(t, 'requested')
    if t.created_by_id == user.pk:
        raise ValidationError('The person who requested the transfer cannot approve it (maker-checker).')
    note = (note or '').strip()
    if not approve and not note:
        raise ValidationError('Give the reason for rejecting.')
    t.status, t.approved_by, t.note = ('approved' if approve else 'rejected'), user, note[:200]
    t.save()
    _log(user, t.from_site, 'Transfer approved' if approve else 'Transfer rejected', t.number, note)
    return t


@transaction.atomic
def dispatch_transfer(user, pk, quantities, vehicle, driver='', lr='', eway=''):
    """quantities: {line_pk: qty}. Stock leaves the source at its average cost and is in transit until received."""
    _need(user, 'dispatch_stocktransfer')
    t = _lock(pk)
    _state(t, 'approved')
    vehicle = ''.join(ch for ch in (vehicle or '').upper() if ch.isalnum())
    problems = []
    if len(vehicle) < 6:
        problems.append('Enter the vehicle number.')
    eway = ''.join(ch for ch in (eway or '') if ch.isdigit())
    if eway and len(eway) != 12:
        problems.append('E-way bill number has 12 digits.')
    lines = list(t.lines.select_related('product'))
    sent = {}
    for l in lines:
        q = Decimal(quantities.get(l.pk) or 0)
        if q < 0 or q > l.requested:
            problems.append(f'{l.product.name}: dispatch between 0 and the approved {qtxt(l.requested)} {l.product.unit}.')
        sent[l] = q
    if not any(sent.values()):
        problems.append('Dispatch at least one item.')
    if problems:
        raise ValidationError(problems)
    for l, q in sent.items():
        l.dispatched = q
        if q:
            m = move_stock(user, t.from_site, l.product, -q, f'trf:{t.pk}:line:{l.pk}:out', 'TransferOut', f'{t.number} → {t.to_site.code} · {vehicle}')
            l.unit_cost = m.unit_cost
        l.save()
    t.status, t.vehicle_number, t.driver_name, t.lr_number, t.eway_bill = 'in_transit', vehicle, (driver or '')[:80], (lr or '')[:30], eway
    t.dispatched_by, t.dispatched_at = user, timezone.now()
    t.save()
    _log(user, t.from_site, 'Transfer dispatched', t.number, f'{vehicle} · ' + ', '.join(f'{l.product.code} {qtxt(q)}' for l, q in sent.items() if q))
    return t


@transaction.atomic
def receive_transfer(user, pk, received, damaged):
    """received / damaged: {line_pk: qty}. Good quantity goes to available, damaged to the damaged state; shortage opens a case."""
    _need(user, 'receive_stocktransfer')
    t = _lock(pk)
    _state(t, 'in_transit')
    if t.dispatched_by_id == user.pk:
        raise ValidationError('The person who dispatched cannot also confirm receipt.')
    problems, lines = [], list(t.lines.select_related('product'))
    for l in lines:
        r, d = Decimal(received.get(l.pk) or 0), Decimal(damaged.get(l.pk) or 0)
        if r < 0 or d < 0 or r + d > l.dispatched:
            problems.append(f'{l.product.name}: good + damaged cannot be more than the {qtxt(l.dispatched)} {l.product.unit} dispatched.')
        l.received, l.damaged = r, d
    if problems:
        raise ValidationError(problems)
    for l in lines:
        l.save()
        if l.received:
            move_stock(user, t.to_site, l.product, l.received, f'trf:{t.pk}:line:{l.pk}:in', 'TransferIn', f'{t.number} from {t.from_site.code}', unit_cost=l.unit_cost)
        if l.damaged:
            move_stock(user, t.to_site, l.product, l.damaged, f'trf:{t.pk}:line:{l.pk}:dmg', 'TransferIn', f'{t.number} damaged in transit', status='damaged', unit_cost=l.unit_cost)
            DamageReport.objects.create(number=next_number('DMG', t.to_site), site=t.to_site, product=l.product, quantity=l.damaged, reason='torn',
                                        location=f'Received on {t.number}', incident_owner=t.vehicle_number or 'Transporter',
                                        description=f'Damaged in transit from {t.from_site.code}', unit_cost=l.unit_cost or 0, created_by=user)
    short = [l for l in lines if l.shortage > 0 or l.damaged > 0]
    t.status = 'discrepancy' if short else 'received'
    t.received_by, t.received_at = user, timezone.now()
    t.save()
    _log(user, t.to_site, 'Transfer received', t.number, ('Discrepancy: ' + ', '.join(f'{l.product.code} short {qtxt(l.shortage)} damaged {qtxt(l.damaged)}' for l in short)) if short else 'Complete')
    return t


@transaction.atomic
def resolve_transfer(user, pk, note):
    """Close the discrepancy case: the shortage stays written off as transit loss (stock already left the source) with an explanation."""
    _need(user, 'approve_stocktransfer')
    t = _lock(pk)
    _state(t, 'discrepancy')
    if user.pk in (t.received_by_id, t.dispatched_by_id):
        raise ValidationError('Someone not involved in dispatch or receipt must resolve the discrepancy.')
    note = (note or '').strip()
    if not note:
        raise ValidationError('Record the finding (e.g. transporter recovery, weighbridge difference, theft complaint).')
    loss = sum((l.shortage * (l.unit_cost or 0) for l in t.lines.all()), ZERO)
    t.status, t.resolved_by, t.resolution = 'received', user, note[:200]
    t.save()
    from .gl import post_transit_loss
    post_transit_loss(user, t, loss.quantize(Decimal('0.01')))
    _log(user, t.to_site, 'Transfer discrepancy resolved', t.number, f'Transit loss ₹{inr(loss)} · {note}')
    return t


@transaction.atomic
def cancel_transfer(user, pk, note):
    _need(user, 'add_stocktransfer')
    t = _lock(pk)
    _state(t, 'requested', 'approved')
    if not (note or '').strip():
        raise ValidationError('Give the reason for cancelling.')
    t.status, t.note = 'cancelled', note.strip()[:200]
    t.save()
    _log(user, t.from_site, 'Transfer cancelled', t.number, note)
    return t


def transit_loss(t):
    return sum((l.shortage * (l.unit_cost or 0) for l in t.lines.all()), ZERO).quantize(Decimal('0.01'))


# ---------- Damaged stock ----------

@transaction.atomic
def report_damage(user, site, product, quantity, reason, location, owner, description='', photo=None):
    _need(user, 'add_damagereport')
    quantity = Decimal(quantity or 0)
    problems = []
    if quantity <= 0: problems.append('Enter the damaged quantity.')
    if reason not in dict(DamageReport._meta.get_field('reason').choices): problems.append('Choose the reason.')
    if not (location or '').strip(): problems.append('Say where the damaged stock is (stack, bay, godown).')
    if not (owner or '').strip(): problems.append('Name the person responsible for the area.')
    if reason == 'other' and not (description or '').strip(): problems.append('Describe the damage.')
    if problems:
        raise ValidationError(problems)
    d = DamageReport.objects.create(number=next_number('DMG', site), site=site, product=product, quantity=quantity, reason=reason, location=location.strip()[:100],
                                    incident_owner=owner.strip()[:100], description=(description or '').strip()[:200], created_by=user)
    if photo:
        d.photo, d.photo_type = photo
    m = transfer_state(user, site, product, quantity, f'dmg:{d.pk}', 'Damage', 'Damage', 'available', 'damaged', f'{d.number} · {d.get_reason_display()}')
    d.unit_cost = m.unit_cost or 0
    d.save()
    _log(user, site, 'Damage reported', d.number, f'{product.code} {qtxt(quantity)} {product.unit} · {d.get_reason_display()} · {d.location} · ₹{inr(d.value)}')
    return d


@transaction.atomic
def decide_damage(user, pk, recovered, scrapped, note):
    _need(user, 'decide_damagereport')
    d = DamageReport.objects.select_for_update().select_related('site', 'product').get(pk=pk)
    recovered, scrapped = Decimal(recovered or 0), Decimal(scrapped or 0)
    note = (note or '').strip()
    if d.status != 'reported':
        raise ValidationError('This report has already been decided.')
    if d.created_by_id == user.pk:
        raise ValidationError('The person who reported the damage cannot decide its disposition.')
    if recovered < 0 or scrapped < 0 or recovered + scrapped != d.quantity:
        raise ValidationError(f'Recovered + scrapped must equal the {qtxt(d.quantity)} {d.product.unit} reported.')
    if not note:
        raise ValidationError('Record the disposition (e.g. 30 bags re-packed, rest sent to crusher).')
    if recovered:
        transfer_state(user, d.site, d.product, recovered, f'dmg:{d.pk}:rec', 'Recovered', 'Recovered', 'damaged', 'available', d.number)
    if scrapped:
        move_stock(user, d.site, d.product, -scrapped, f'dmg:{d.pk}:scrap', 'Scrap', f'{d.number} · {note}', status='damaged')
    d.status, d.recovered, d.scrapped, d.decision_note, d.decided_by, d.decided_at = 'decided', recovered, scrapped, note[:200], user, timezone.now()
    d.save()
    _log(user, d.site, 'Damage disposition', d.number, f'recovered {qtxt(recovered)}, scrapped {qtxt(scrapped)} (₹{inr(scrapped * d.unit_cost)}) · {note}')
    return d


# ---------- Stock count (FR-INV-006) ----------

from .models import StockCount, StockCountLine, StockMovement  # noqa: E402


def _count_lock(pk):
    return StockCount.objects.select_for_update().select_related('site').get(pk=pk)


@transaction.atomic
def start_count(user, site, category='', blind=True, note=''):
    _need(user, 'add_stockcount')
    open_counts = StockCount.objects.filter(site=site, status__in=['counting', 'submitted', 'recount'])
    if (open_counts if not category else open_counts.filter(category__in=[category, ''])).exists():
        raise ValidationError(f'A count is already open at {site.code} for these items — finish it first.')
    bal = StockBalance.objects.filter(site=site, status='available').select_related('product')
    if category:
        bal = bal.filter(product__category=category)
    have = {b.product_id: b for b in bal}
    products = Product.objects.filter(active=True)
    if category:
        products = products.filter(category=category)
    products = [p for p in products if p.pk in have and have[p.pk].quantity > 0] if not category else list(products)
    if not products:
        raise ValidationError('Nothing to count for this selection.')
    c = StockCount.objects.create(number=next_number('CNT', site), site=site, category=category, blind=blind, note=(note or '')[:200], created_by=user)
    for p in sorted(products, key=lambda p: (p.category, p.name)):
        b = have.get(p.pk)
        StockCountLine.objects.create(count=c, product=p, system_qty=b.quantity if b else ZERO, unit_cost=b.avg_cost if b else ZERO)
    _log(user, site, 'Stock count started', c.number, f'{len(products)} item(s){" · blind" if blind else ""}')
    return c


@transaction.atomic
def save_count(user, pk, counted, reasons, submit=False):
    """counted: {line_pk: qty or None}. In 'recount' only flagged lines are re-entered (into recounted)."""
    _need(user, 'add_stockcount')
    c = _count_lock(pk)
    if c.status not in ('counting', 'recount'):
        raise ValidationError('This count is not open for entry.')
    for l in c.lines.all():
        if c.status == 'recount' and not l.recount_needed:
            continue
        q = counted.get(l.pk)
        if q is not None and q < 0:
            raise ValidationError(f'{l.product.name}: count cannot be negative.')
        if c.status == 'recount':
            l.recounted = q
        else:
            l.counted = q
        l.reason = (reasons.get(l.pk) or l.reason or '')[:150]
        l.save()
    if submit:
        missing = [l.product.name for l in c.lines.all() if (l.recounted if c.status == 'recount' and l.recount_needed else l.counted) is None]
        if missing:
            raise ValidationError('Enter a count for every item (0 if none found): ' + ', '.join(missing[:5]) + ('…' if len(missing) > 5 else ''))
        c.status, c.submitted_by = 'submitted', user
        c.save()
        _log(user, c.site, 'Stock count submitted', c.number, f'variance ₹{inr(sum((l.variance_value or 0) for l in c.lines.all()))}')
    return c


@transaction.atomic
def review_count(user, pk, action, recount_lines=(), note=''):
    """action 'recount' sends chosen lines back; 'approve' posts each variance as a Count adjustment."""
    _need(user, 'approve_stockcount')
    c = _count_lock(pk)
    if c.status != 'submitted':
        raise ValidationError('Only submitted counts can be reviewed.')
    if user.pk in (c.submitted_by_id,):
        raise ValidationError('The person who submitted the count cannot approve it (maker-checker).')
    note = (note or '').strip()
    lines = list(c.lines.select_related('product'))
    if action == 'recount':
        chosen = [l for l in lines if l.pk in set(recount_lines)]
        if not chosen:
            raise ValidationError('Tick the items to recount.')
        for l in lines:
            l.recount_needed = l in chosen
            if l.recount_needed:
                l.recounted = None
            l.save()
        c.status = 'recount'
        c.save()
        _log(user, c.site, 'Recount requested', c.number, ', '.join(l.product.code for l in chosen))
        return c
    if action != 'approve':
        raise ValidationError('Unknown action.')
    changed = [l for l in lines if l.variance]
    if changed and not note:
        raise ValidationError('Record why the variances are accepted (e.g. bag breakage, weighbridge difference).')
    for l in changed:
        move_stock(user, c.site, l.product, l.variance, f'cnt:{c.pk}:line:{l.pk}', 'Adjustment', f'{c.number} · {l.reason or note}'[:200])
    c.status, c.approved_by, c.approved_at, c.note = 'posted', user, timezone.now(), (note or c.note)[:200]
    c.save()
    _log(user, c.site, 'Stock count posted', c.number, f'{len(changed)} adjustment(s) · net ₹{inr(sum((l.variance_value for l in changed), ZERO))}')
    return c


# ---------- Ageing, slow-moving, min/max (FR-INV-007) ----------

IN_KINDS = ('Opening', 'Receipt', 'Production', 'TransferIn', 'Return')
OUT_KINDS = ('Dispatch', 'TransferOut', 'Consumption', 'PurchaseReturn')


def analysis(sites, slow_days=60):
    today = timezone.localdate()
    out = []
    for b in StockBalance.objects.filter(site__in=sites, status='available', quantity__gt=0).select_related('site', 'product'):
        mv = StockMovement.objects.filter(site=b.site, product=b.product, status='available')
        last_in = mv.filter(kind__in=IN_KINDS, quantity__gt=0).order_by('-created_at').values_list('created_at', flat=True).first()
        last_out = mv.filter(kind__in=OUT_KINDS, quantity__lt=0).order_by('-created_at').values_list('created_at', flat=True).first()
        age = (today - timezone.localdate(last_in)).days if last_in else None
        idle = (today - timezone.localdate(last_out)).days if last_out else None
        bucket = '—' if age is None else '0–30' if age <= 30 else '31–60' if age <= 60 else '61–90' if age <= 90 else '90+'
        p = b.product
        out.append({'b': b, 'last_in': last_in, 'last_out': last_out, 'age': age, 'idle': idle, 'bucket': bucket,
                    'slow': (idle if idle is not None else age if age is not None else 0) > slow_days, 'excess': bool(p.max_level) and b.quantity > p.max_level,
                    'low': p.reorder_level > 0 and b.quantity <= p.reorder_level})
    out.sort(key=lambda r: (-(r['idle'] if r['idle'] is not None else 10 ** 6), r['b'].site.code))
    return out
