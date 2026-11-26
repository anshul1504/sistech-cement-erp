"""Outbound truck flow (SRS §4.4 / FR-LOG-001…012): plan → gate-in → tare → loading → gross → weight check → invoice → gate-out → POD.

Control points enforced here (every screen and future API goes through these functions):
- vehicles with expired / missing documents, blacklisted vehicles and drivers with an expired licence cannot enter;
- weighbridge readings are never edited — a reweigh adds a linked reading with a reason (FR-LOG-005);
- manual readings need a reason (FR-LOG-003); gross must exceed tare (FR-LOG-004);
- net vs loaded quantity outside the plant tolerance, or overload, needs approval by a different user (FR-LOG-009);
- stock posts once, at the plant's configured event, with a unique source per line (FR-LOG-008);
- gate-out needs a posted invoice and no open exception.
"""
from accounts.delegation import authorize, can_approve
from .gst import inr
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .gst import next_number, qtxt
from .models import Dispatch, DispatchLine, Weighment, Order, AuditEvent, StockMovement
from .services import move_stock, require_site

GATE_CHECKS = [('licence', 'Driver licence seen'), ('rc', 'RC & permit in vehicle'), ('puc', 'PUC certificate'), ('tarpaulin', 'Tarpaulin / cover available'),
               ('safety', 'Driver has safety shoes & helmet'), ('clean', 'Body clean, dry, no leftover material'), ('no_passengers', 'No unauthorised passengers')]
OPEN_STATES = ['planned', 'gate_in', 'tared', 'loading', 'loaded', 'weighed']


def _log(user, d, action, detail=''):
    AuditEvent.objects.create(actor=user, site=d.site, action=action, reference=d.number, detail=detail)


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _state(d, *allowed):
    if d.status not in allowed:
        raise ValidationError(f'{d.number} is "{d.get_status_display()}" — this step is not possible now.')


def _lock(pk, user):
    obj = Dispatch.objects.select_for_update().select_related('site', 'order', 'vehicle', 'driver', 'order__partner').get(pk=pk)
    require_site(user, obj.site)
    return obj


# ---------- Planning ----------

def vehicle_problems(vehicle, exclude=None):
    out = []
    if not vehicle.active: out.append(f'{vehicle} is inactive.')
    if vehicle.blacklisted: out.append(f'{vehicle} is blacklisted: {vehicle.blacklist_reason or "no reason recorded"}.')
    for label, when in vehicle.document_issues():
        out.append(f'{vehicle}: {label} {"expired on " + when.strftime("%d %b %Y") if when else "not recorded"}.')
    busy = Dispatch.objects.filter(vehicle=vehicle, status__in=OPEN_STATES)
    if exclude: busy = busy.exclude(pk=exclude)
    if busy.exists(): out.append(f'{vehicle} is already on {busy.first().number}.')
    return out


@transaction.atomic
def plan(user, order, vehicle, driver, quantities, transporter=None, bay='', planned_for=None, lr_number=''):
    """quantities: {OrderLine: qty}. Creates the dispatch advice and its queue token."""
    _need(user, 'add_dispatch')
    order = Order.objects.select_for_update().select_related('site').get(pk=order.pk)
    require_site(user, order.site)
    if order.kind == 'Sale' and order.status == 'On hold':
        raise ValidationError(f'{order.number} is on hold: {order.hold_reason}')
    if order.kind != 'Sale' or order.status not in ('Approved', 'Part dispatched'):
        raise ValidationError('Only approved sales orders can be dispatched.')
    from .sales import dispatch_block
    blocked = dispatch_block(order)
    if blocked:
        raise ValidationError(blocked + ['Collect payment, or ask a credit approver to re-approve the order with an override.'])
    if not order.site.uses_weighbridge:
        raise ValidationError(f'{order.site.code} does not use the gate & weighbridge flow — dispatch from the order page.')
    problems = vehicle_problems(vehicle)
    if not driver.active: problems.append(f'{driver.name} is inactive.')
    if not driver.licence_valid: problems.append(f"{driver.name}'s licence expired on {driver.licence_valid_until:%d %b %Y}.")
    from .models import OrderLine
    from decimal import InvalidOperation
    persisted={row.pk:row for row in order.lines.select_related('product')}
    picked={}
    for supplied,raw in quantities.items():
        if supplied.pk not in persisted:raise ValidationError('Item does not belong to this order.')
        try:
            qty=Decimal(str(raw or 0))
            if not qty.is_finite():raise InvalidOperation
            if not qty:continue
            OrderLine._meta.get_field('quantity').clean(qty,persisted[supplied.pk])
        except (InvalidOperation,TypeError,ValueError,ValidationError):raise ValidationError('Dispatch quantities must be finite positive values with at most three decimal places.') from None
        picked[persisted[supplied.pk]]=qty
    if not picked: problems.append('Enter a quantity for at least one item.')
    expected = Decimal('0')
    for line, qty in picked.items():
        if line.order_id != order.pk: raise ValidationError('Item does not belong to this order.')
        if qty > line.pending_quantity: problems.append(f'{line.product.name}: only {qtxt(line.pending_quantity)} {line.product.unit} left to dispatch.')
        kg = DispatchLine(order_line=line, planned_quantity=qty).unit_kg()
        expected += (kg or 0) * qty
    if expected > vehicle.capacity_mt * 1000:
        problems.append(f'Planned load {expected / 1000:.2f} MT is more than {vehicle} capacity {vehicle.capacity_mt} MT.')
    if problems:
        raise ValidationError(problems)
    from .reservations import allocate
    commitments={row.order_line_id:row for row in allocate(user,order,'Reviewed dispatch-advice stock allocation')}
    for line,qty in picked.items():
        planned=sum((row.quantity for row in line.dispatch_lines.filter(dispatch__stock_posted=False).exclude(dispatch__status='cancelled')),Decimal(0))
        if qty>commitments[line.pk].remaining-planned:raise ValidationError(f'{line.product.name}: allocate sufficient free stock before planning this truck.')
    day = planned_for or timezone.localdate()
    token = (Dispatch.objects.filter(site=order.site, planned_for=day).aggregate(m=Max('token'))['m'] or 0) + 1
    d = Dispatch.objects.create(number=next_number('DSP', order.site), token=token, site=order.site, order=order, vehicle=vehicle, driver=driver,
                                transporter=transporter or vehicle.transporter, bay=bay, planned_for=day, lr_number=lr_number, created_by=user)
    for line, qty in picked.items():
        DispatchLine.objects.create(dispatch=d, order_line=line, planned_quantity=qty)
    _log(user, d, 'Dispatch planned', f'{order.number} · {vehicle} · token {token} · {expected / 1000:.2f} MT')
    return d


# ---------- Gate ----------

@transaction.atomic
def gate_in(user, pk, checklist):
    _need(user, 'gate_dispatch')
    d = _lock(pk, user)
    _state(d, 'planned')
    problems = vehicle_problems(d.vehicle, exclude=d.pk)
    if not d.driver.licence_valid: problems.append(f"{d.driver.name}'s licence has expired.")
    missing = [label for key, label in GATE_CHECKS if not checklist.get(key)]
    if missing: problems.append('Gate checklist incomplete: ' + ', '.join(missing) + '.')
    if problems:
        raise ValidationError(problems)
    d.gate_checklist = {k: True for k, _ in GATE_CHECKS}
    d.status, d.gate_in_at, d.gate_in_by = 'gate_in', timezone.now(), user
    d.save()
    _log(user, d, 'Gate-in', f'{d.vehicle} · {d.driver.name}')
    return d


# ---------- Weighbridge ----------

@transaction.atomic
def weigh(user, pk, kind, weight_kg, source='manual', reason=''):
    """Record tare or gross. If a current reading exists this is a reweigh: new row, old one linked, reason required."""
    _need(user, 'weigh_dispatch')
    d = _lock(pk, user)
    reason = (reason or '').strip()
    try:
        weight = int(Decimal(str(weight_kg)))
    except Exception:
        raise ValidationError('Enter the weight in kilograms.')
    if weight <= 0:
        raise ValidationError('Weight must be more than zero.')
    if source == 'manual' and not reason:
        raise ValidationError('Manual entry needs a reason (e.g. scale interface offline).')
    current = d.reading(kind)
    if current:
        _state(d, 'tared', 'loading', 'loaded', 'weighed') if kind == 'tare' else _state(d, 'weighed')
        if not reason:
            raise ValidationError('A reweigh needs a reason.')
    else:
        _state(d, 'gate_in') if kind == 'tare' else _state(d, 'loaded')
    if weight > 80000:
        raise ValidationError('Reading above 80,000 kg — check the scale.')
    if kind == 'gross':
        tare = d.tare
        if weight <= tare.weight_kg:
            raise ValidationError(f'Gross {weight:,} kg must be more than tare {tare.weight_kg:,} kg.')
    if kind == 'tare' and d.vehicle.tare_kg and abs(weight - d.vehicle.tare_kg) > d.vehicle.tare_kg * Decimal('0.10') and not reason:
        raise ValidationError(f'Tare differs from the usual {d.vehicle.tare_kg:,} kg by more than 10% — record the reason.')
    w = Weighment.objects.create(dispatch=d, kind=kind, weight_kg=weight, source=source, scale_id=d.site.scale_id, reason=reason, captured_by=user)
    if current:
        current.superseded_by = w
        current.save(update_fields=['superseded_by'])
        _log(user, d, 'Reweigh', f'{kind} {current.weight_kg:,} → {weight:,} kg · {reason}')
    else:
        _log(user, d, f'{kind.title()} weight', f'{weight:,} kg ({source}){" · " + reason if reason else ""}')
    if kind == 'tare' and d.status == 'gate_in':
        d.status = 'tared'
    if kind == 'gross':
        d.status = 'weighed'
    if d.gross:
        check_weight(d)
    d.save()
    return w


def check_weight(d):
    """Net vs loaded quantity (tolerance) and vehicle capacity (overload). Sets / clears the exception; a new reading needs a new approval."""
    net = d.net_kg
    expected = Decimal('0')
    unknown = False
    for l in d.lines.select_related('order_line__product'):
        kg = l.unit_kg()
        if kg is None: unknown = True
        else: expected += kg * l.quantity
    d.expected_kg = expected
    d.variance_kg = Decimal(net) - expected
    issues = []
    tol = expected * d.site.weight_tolerance_pct / 100
    if not unknown and expected and abs(d.variance_kg) > tol:
        issues.append(f'Net {net:,} kg vs loaded {inr(expected, 0)} kg ({"+" if d.variance_kg > 0 else ""}{inr(d.variance_kg, 0)} kg, allowed ±{inr(tol, 0)} kg)')
    if net > d.vehicle.capacity_mt * 1000:
        issues.append(f'Overload: net {net:,} kg > capacity {d.vehicle.capacity_mt} MT')
    d.exception = '; '.join(issues)[:200]
    d.exception_approved_by, d.exception_note = None, ''


def exception_makers(dispatch):
    return {dispatch.created_by_id,dispatch.loaded_by_id,*dispatch.weighments.values_list('captured_by_id',flat=True)} - {None}


def can_approve_exception(user,dispatch):
    from .models import SiteAccess
    return (dispatch.status=='weighed' and bool(dispatch.exception) and not dispatch.exception_approved_by_id
        and user.has_perm('erp.view_dispatch') and user.pk not in exception_makers(dispatch)
        and (user.is_superuser or SiteAccess.objects.filter(user=user,site=dispatch.site).exists())
        and can_approve(user,'approve_weight_exception',dispatch.site,makers=exception_makers(dispatch)))


@transaction.atomic
def approve_exception(user, pk, note, expected_readings=None):
    _need(user, 'view_dispatch')
    d = _lock(pk, user)
    _state(d, 'weighed')
    if not d.exception:
        raise ValidationError('There is no weight exception to approve.')
    reading_pair=f'{d.tare.pk}:{d.gross.pk}'
    if expected_readings is not None and expected_readings!=reading_pair:
        raise ValidationError('Weighing evidence changed or is missing. Reopen the trip and review its current tare/gross readings.')
    if d.exception_approved_by_id:raise ValidationError('This weight exception already has a retained approval. Reweigh before requesting a new review.')
    if user.pk in exception_makers(d):
        raise ValidationError('The person who weighed or loaded the truck cannot approve its exception.')
    authorize(user,'approve_weight_exception',d.site,makers=exception_makers(d),reference=f'{d.number}:T{d.tare.pk}:G{d.gross.pk}')
    if not (note or '').strip():
        raise ValidationError('Write why this is acceptable (e.g. bag weights checked, 3 bags re-filled).')
    d.exception_approved_by, d.exception_note = user, note.strip()[:200]
    d.save(update_fields=['exception_approved_by', 'exception_note'])
    _log(user, d, 'Weight exception approved', f'{d.exception} · {d.exception_note}')
    return d


# ---------- Loading ----------

@transaction.atomic
def start_loading(user, pk, bay=''):
    _need(user, 'load_dispatch')
    d = _lock(pk, user)
    _state(d, 'tared')
    d.status, d.loading_started_at = 'loading', timezone.now()
    if bay: d.bay = bay[:30]
    d.save()
    _log(user, d, 'Loading started', d.bay)
    return d


@transaction.atomic
def batches_for(site, product):
    """Released batches of this product at this site, or released bulk batches packed into this bag SKU."""
    from .models import Production
    from django.db.models import Q
    return Production.objects.filter(site=site, status='Released').filter(Q(product=product) | Q(packing_runs__bag_product=product)).distinct().order_by('-released_at')


@transaction.atomic
def complete_loading(user, pk, quantities, seal='', team='', damaged_bags=0, notes='', batches=None):
    """quantities: {DispatchLine.pk: actual qty}. Actual may be lower than planned, never higher. batches: {DispatchLine.pk: Production.pk} (optional)."""
    _need(user, 'load_dispatch')
    d = _lock(pk, user)
    _state(d, 'loading')
    problems = []
    lines = list(d.lines.select_related('order_line__product'))
    for l in lines:
        raw = quantities.get(l.pk, quantities.get(str(l.pk)))
        try:
            qty = Decimal(str(raw)) if raw not in (None, '') else l.planned_quantity
        except Exception:
            problems.append(f'{l.order_line.product.name}: enter a number.'); continue
        if qty < 0 or qty > l.planned_quantity:
            problems.append(f'{l.order_line.product.name}: loaded quantity must be between 0 and planned {qtxt(l.planned_quantity)}.'); continue
        l.loaded_quantity = qty
        b = (batches or {}).get(l.pk, (batches or {}).get(str(l.pk)))
        if b:
            batch = batches_for(d.site, l.order_line.product).filter(pk=b).first()
            if not batch:
                problems.append(f'{l.order_line.product.name}: choose a released batch of this product at {d.site.code}.'); continue
            l.batch = batch
    if not problems and not any(l.loaded_quantity for l in lines):
        problems.append('Nothing was loaded — cancel the dispatch instead.')
    if problems:
        raise ValidationError(problems)
    for l in lines:
        l.save(update_fields=['loaded_quantity', 'batch'])
    d.status, d.loading_done_at, d.loaded_by = 'loaded', timezone.now(), user
    d.seal_number, d.loading_team, d.damaged_bags, d.loading_notes = seal.strip()[:30], team.strip()[:80], int(damaged_bags or 0), notes.strip()[:200]
    d.save()
    if d.site.stock_post_event == 'load_complete':
        post_stock(user, d)
    _log(user, d, 'Loading complete', ', '.join(f'{l.order_line.product.code} {qtxt(l.loaded_quantity)}' for l in lines) + (f' · seal {d.seal_number}' if d.seal_number else ''))
    return d


def post_stock(user, d):
    """Issue stock once per dispatch line (unique source id); refuses negative stock."""
    if d.stock_posted:
        return
    for l in d.lines.select_related('order_line__product'):
        if l.quantity:
            move_stock(user, d.site, l.order_line.product, -l.quantity, f'dispatch:{d.pk}:line:{l.pk}', 'Dispatch', f'{d.number} · {d.vehicle}',reservation_line=l.order_line)
    d.stock_posted = True
    d.save(update_fields=['stock_posted'])


# ---------- Invoice & exit ----------

@transaction.atomic
def invoice(user, pk, eway_bill=''):
    _need(user, 'post_invoice')
    d = _lock(pk, user)
    _state(d, 'weighed')
    if hasattr(d, 'invoice'):
        raise ValidationError(f'Already invoiced as {d.invoice.number}.')
    if d.exception_open:
        raise ValidationError('Weight exception needs supervisor approval before invoicing.')
    from .invoice_views import seller_problems, create_invoice, _Billable
    problems = seller_problems(d.order)
    if problems:
        raise ValidationError(problems)
    billables = [_Billable(l.order_line, l.loaded_quantity) for l in d.lines.select_related('order_line__product') if l.loaded_quantity]
    transport = {'vehicle_number': d.vehicle.registration, 'transporter': d.transporter.name if d.transporter else '', 'lr_number': d.lr_number,
                 'eway_bill': ''.join(ch for ch in (eway_bill or '') if ch.isdigit())[:12]}
    inv = create_invoice(user, d.order, billables, transport, dispatch=d)
    _log(user, d, 'Dispatch invoiced', inv.number)
    return inv


@transaction.atomic
def gate_out(user, pk):
    _need(user, 'gate_dispatch')
    d = _lock(pk, user)
    _state(d, 'weighed')
    if d.exception_open:
        raise ValidationError('Weight exception is not approved.')
    if not hasattr(d, 'invoice'):
        raise ValidationError('Post the tax invoice before the truck leaves.')
    if d.order.partner and not d.seal_number and d.vehicle.vehicle_type == 'bulker':
        raise ValidationError('Bulkers must be sealed — record the seal number at loading.')
    post_stock(user, d)
    d.status, d.gate_out_at, d.gate_out_by = 'gate_out', timezone.now(), user
    d.save()
    tare = d.tare
    if tare:
        d.vehicle.tare_kg = tare.weight_kg
        d.vehicle.save(update_fields=['tare_kg', 'updated_at'])
    refresh_order(d.order)
    from .freight import snapshot
    snapshot(d)
    _log(user, d, 'Gate-out', f'{d.vehicle} · invoice {d.invoice.number} · net {d.net_kg:,} kg')
    return d


def refresh_order(order):
    order = Order.objects.select_for_update().get(pk=order.pk)
    lines = list(order.lines.all())
    if order.status not in ('Approved', 'Part dispatched', 'On hold'):
        return
    done = all(l.dispatched_quantity() >= l.quantity for l in lines)
    some = any(l.dispatched_quantity() > 0 for l in lines)
    new = 'Completed' if done else 'Part dispatched' if some else 'Approved'
    if order.status == 'On hold':  # a truck already inside finished: remember where the order is, keep it on hold
        if new == 'Completed':
            order.status, order.held_from, order.hold_reason = 'Completed', '', ''
        else:
            order.held_from = new
        order.save(update_fields=['status', 'held_from', 'hold_reason'])
    elif new != order.status:
        order.status = new
        order.save(update_fields=['status'])


@transaction.atomic
def deliver(user, pk, received_by, receiver_mobile='', shortage=0, damaged=0, notes='', file=None):
    _need(user, 'pod_dispatch')
    d = _lock(pk, user)
    return record_pod(user, d, received_by, receiver_mobile, shortage, damaged, notes, file)


def record_pod(user, d, received_by, receiver_mobile='', shortage=0, damaged=0, notes='', file=None, via=''):
    """POD on a locked, in-transit trip. Callers check who may do it (staff: perm + plant; driver app: own trip)."""
    _state(d, 'gate_out')
    from .pod import details,validate_file
    from accounts.security import access_allowed
    if not access_allowed(user):raise PermissionDenied
    file=validate_file(file)
    values=details(d,received_by,receiver_mobile,shortage,damaged,notes)
    shortage,damaged=Decimal(values['shortage_qty']),Decimal(values['damaged_qty'])
    d.status,d.delivered_at,d.pod_recorded_by='delivered',timezone.now(),user
    for field,value in values.items():setattr(d,field,Decimal(value) if field in ['shortage_qty','damaged_qty'] else value)
    if file:
        d.pod_file, d.pod_file_type = file
    d.save()
    _log(user, d, 'Delivered (POD)', f'{d.received_by}' + (f' · short {qtxt(shortage)} · damaged {qtxt(damaged)}' if shortage or damaged else '') + (f' · {via}' if via else ''))
    return d


@transaction.atomic
def cancel(user, pk, reason):
    d = _lock(pk, user)
    if not (user.has_perm('erp.add_dispatch') or user.has_perm('erp.gate_dispatch')):
        raise PermissionDenied
    _state(d, *OPEN_STATES)
    if hasattr(d, 'invoice'):
        raise ValidationError(f'Invoice {d.invoice.number} is posted — raise a credit note instead of cancelling the truck.')
    if not (reason or '').strip():
        raise ValidationError('Give a reason for cancelling.')
    if d.stock_posted:  # stock was issued at load-complete: put it back with its own source id
        for l in d.lines.select_related('order_line__product'):
            if l.quantity:
                out = StockMovement.objects.filter(source=f'dispatch:{d.pk}:line:{l.pk}').first()
                move_stock(user, d.site, l.order_line.product, l.quantity, f'dispatch:{d.pk}:line:{l.pk}:unload', 'Return', f'{d.number} cancelled', unit_cost=out.unit_cost if out else None, value=abs(out.value) if out else None)
                from .reservations import restore
                restore(user,l.order_line,l.quantity,f'dispatch:{d.pk}:line:{l.pk}')
        d.stock_posted = False
    d.status, d.cancel_reason = 'cancelled', reason.strip()[:150]
    d.save()
    refresh_order(d.order)
    from .reservations import allocate
    allocate(user,d.order,'Reallocate returned/cancelled dispatch stock')
    _log(user, d, 'Dispatch cancelled', d.cancel_reason)
    return d
