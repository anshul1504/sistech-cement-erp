"""Sales order controls (SRS §5.2 FR-SLS-004…010, §5.11 FR-AR-002…004).

- Submission checks: active customer, rates present, duplicate request (same customer, plant and items within 24 h).
- Approval (different user): overdue and credit exposure, below-list price, stock available-to-promise. A credit breach can be
  approved only by a user with the credit-override permission, with a reason and an expiry date; below-list lines need the
  price-exception permission. Plants that require ATP refuse a shortage; others record it as a backorder. The position seen at
  approval is stored on the order (approval_checks) so it can be audited later.
- Concurrent approvals for the same plant and products are serialised by locking the stock rows.
- Hold / release, revision after approval (new draft revision, old one marked Revised), cancellation and short-close with reasons.
- Dispatch planning re-checks credit unless a valid override exists (FR-AR-003).
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from .gst import inr
from .models import Order, OrderLine, StockBalance, Partner, InvoiceLine, DispatchLine, AuditEvent

ZERO = Decimal('0')
OPEN = ('Approved', 'Part dispatched', 'On hold')


def _q(x):
    return f'{inr(x, 3)}'.replace('.000', '')


# ---------- Position ----------

def atp(order):
    """Per line: available stock at the plant, reserved by other open orders, free, and shortage for this order's pending quantity."""
    rows = []
    for l in order.lines.select_related('product'):
        available = StockBalance.objects.filter(site=order.site, product=l.product, status='available').aggregate(q=Sum('quantity'))['q'] or ZERO
        reserved = ZERO
        for o in OrderLine.objects.filter(order__kind='Sale', order__site=order.site, order__status__in=OPEN, product=l.product).exclude(order=order):
            reserved += o.pending_quantity
        need = l.pending_quantity if order.status in OPEN else l.quantity
        free = available - reserved
        rows.append({'line': l, 'available': available, 'reserved': reserved, 'free': free, 'need': need, 'short': max(need - max(free, ZERO), ZERO)})
    return rows


def credit_problems(order, include_self=True):
    from .ledger import exposure, overdue
    partner = order.partner
    out = []
    late = overdue(partner)
    if late:
        out.append(f'{len(late)} invoice{"s" if len(late) > 1 else ""} past due (₹{inr(sum(i.outstanding for i in late), 0)}, oldest {late[0].number}).')
    # Approval: this order is not in the pipeline yet, so add it. Dispatch: it is approved and already counted in the pipeline.
    exp = exposure(partner, exclude_order=order.pk if include_self else None)
    total = exp['total'] + (order.grand_total if include_self else ZERO)
    if total > partner.credit_limit:
        out.append(f'Credit limit exceeded: outstanding ₹{inr(exp["balance"], 0)} + {"other " if include_self else ""}open orders ₹{inr(exp["pipeline"], 0)}'
                   + (f' + this order ₹{inr(order.grand_total, 0)}' if include_self else '') + f' > limit ₹{inr(partner.credit_limit, 0)}.')
    return out


def progress(order):
    """Ordered / loaded-or-planned / dispatched / invoiced / delivered / pending per line, and an overall stage label."""
    lines = list(order.lines.select_related('product'))
    inv = {r['product']: r['q'] for r in InvoiceLine.objects.filter(invoice__order=order).values('product').annotate(q=Sum('quantity'))}
    dl = DispatchLine.objects.filter(order_line__order=order).exclude(dispatch__status='cancelled').select_related('dispatch')
    planned, sent, delivered = {}, {}, {}
    for d in dl:
        q = d.quantity
        key = d.order_line_id
        if d.dispatch.stock_posted:
            sent[key] = sent.get(key, ZERO) + q
            if d.dispatch.status == 'delivered':
                delivered[key] = delivered.get(key, ZERO) + q
        else:
            planned[key] = planned.get(key, ZERO) + q
    out = []
    for l in lines:
        s = sent.get(l.pk, ZERO)
        if not order.site.uses_weighbridge and order.status == 'Completed' and not dl:
            s = l.quantity  # direct dispatch (no gate) posts the whole order
        out.append({'line': l, 'planned': planned.get(l.pk, ZERO), 'sent': s, 'delivered': delivered.get(l.pk, ZERO),
                    'invoiced': min(inv.get(l.product_id, ZERO), l.quantity), 'pending': max(l.quantity - s - planned.get(l.pk, ZERO), ZERO),
                    'pct': min(int(s * 100 / l.quantity), 100) if l.quantity else 0})
    return out


def stage(order, rows=None):
    """Display stage per FR-SLS-006, derived from status and documents (no duplicate state to keep in sync)."""
    st = order.status
    if st in ('Draft', 'Submitted', 'On hold', 'Cancelled', 'Revised'):
        return {'Submitted': 'Approval pending'}.get(st, st)
    if order.kind != 'Sale':
        return st
    rows = rows if rows is not None else progress(order)
    total = sum((r['line'].quantity for r in rows), ZERO)
    sent = sum((r['sent'] for r in rows), ZERO)
    if st == 'Completed':
        if order.short_close_reason:
            return 'Closed'
        if rows and all(r['delivered'] >= r['line'].quantity for r in rows):
            return 'Delivered'
        if rows and all(r['invoiced'] >= r['line'].quantity for r in rows):
            return 'Invoiced'
        return 'Dispatched'
    if any(r['planned'] for r in rows):
        return 'Dispatch planned' if not sent else 'Part dispatched'
    return 'Part dispatched' if sent and sent < total else st


# ---------- Workflow ----------

def submit_checks(order, confirm_duplicate=False):
    problems = []
    if order.partner.kind not in ('Dealer', 'Distributor', 'Retailer', 'Institutional'):
        problems.append('Choose a customer (dealer, distributor, retailer or institutional buyer).')
    if not order.partner.active:
        problems.append(f'{order.partner.name} is inactive.')
    for l in order.lines.select_related('product'):
        if l.rate <= 0:
            problems.append(f'{l.product.name}: enter the rate.')
        if not l.product.active:
            problems.append(f'{l.product.name} is inactive.')
    if not confirm_duplicate:
        mine = sorted((l.product_id, l.quantity) for l in order.lines.all())
        since = timezone.now() - timedelta(hours=24)
        for o in Order.objects.filter(kind='Sale', partner=order.partner, site=order.site, created_at__gte=since,
                                      status__in=['Submitted', 'Approved', 'Part dispatched', 'On hold']).exclude(pk=order.pk).prefetch_related('lines'):
            if sorted((l.product_id, l.quantity) for l in o.lines.all()) == mine:
                problems.append(f'Possible duplicate of {o.number} (same customer, plant and items in the last 24 hours). Tick “Not a duplicate” to submit anyway.')
                break
    return problems


def approve(user, order, override_reason='', override_until=None, backorder_reason=''):
    """Called inside transition_order's transaction with the order row locked."""
    if order.created_by_id == user.pk:
        raise ValidationError('A different user must approve this order (maker-checker).')
    if not order.partner.active:
        raise ValidationError('The partner is inactive.')
    Partner.objects.select_for_update().get(pk=order.partner_id)            # serialise credit decisions per dealer
    list(StockBalance.objects.select_for_update().filter(site=order.site, product__in=order.lines.values('product')))  # and stock per plant
    today = timezone.localdate()
    checks = {'at': timezone.now().isoformat(timespec='seconds'), 'by': user.get_username()}
    credit = credit_problems(order)
    if credit:
        if not user.has_perm('erp.override_credit_sales_order'):
            raise ValidationError(credit + ['A user with credit-override authority must approve it, or collect payment first.'])
        if not (override_reason or '').strip():
            raise ValidationError(credit + ['Record why the credit rule is overridden.'])
        if not override_until or override_until < today or override_until > today + timedelta(days=30):
            raise ValidationError('Give the override an expiry date within the next 30 days.')
        order.credit_override_reason, order.credit_override_until, order.credit_override_by = override_reason.strip()[:200], override_until, user
        checks['credit_override'] = credit
    below = [l for l in order.lines.select_related('product') if l.below_list]
    if below:
        if not user.has_perm('erp.approve_price_exception'):
            raise ValidationError(f'{len(below)} line(s) below list price — a user with price-exception authority must approve.')
        checks['below_list'] = [{'product': l.product.code, 'rate': str(l.rate), 'list': str(l.list_rate)} for l in below]
    short = [r for r in atp(order) if r['short'] > 0]
    if short:
        text = [f'{r["line"].product.name}: short {_q(r["short"])} {r["line"].product.unit} (free {_q(max(r["free"], ZERO))})' for r in short]
        if order.site.atp_required:
            raise ValidationError(text + [f'{order.site.code} approves only against free stock.'])
        if not (backorder_reason or '').strip():
            raise ValidationError(text + ['Not enough free stock — record the backorder reason (e.g. partner supply due on a date).'])
        checks['backorder'] = {'lines': text, 'reason': backorder_reason.strip()[:200]}
    order.approval_checks = checks
    order.status, order.approved_by = 'Approved', user


def hold(user, order, reason):
    if not user.has_perm('erp.hold_sales_order'):
        raise PermissionDenied
    if order.status not in ('Submitted', 'Approved', 'Part dispatched'):
        raise ValidationError('Only open orders can be put on hold.')
    if not (reason or '').strip():
        raise ValidationError('Give the reason for the hold.')
    order.held_from, order.hold_reason, order.status = order.status, reason.strip()[:200], 'On hold'


def release(user, order):
    if not user.has_perm('erp.hold_sales_order'):
        raise PermissionDenied
    if order.status != 'On hold':
        raise ValidationError('The order is not on hold.')
    order.status, order.held_from = order.held_from or 'Approved', ''


def dispatch_block(order):
    """Credit re-check when a truck is planned (FR-AR-003). A valid override recorded at approval lets the order through."""
    if order.status == 'On hold':
        return [f'{order.number} is on hold: {order.hold_reason}']
    if order.credit_override_until and order.credit_override_until >= timezone.localdate():
        return []
    return credit_problems(order, include_self=False)


@transaction.atomic
def revise(user, pk):
    """FR-SLS-007: an approved / submitted order becomes 'Revised' and a new draft revision is opened for changes and re-approval."""
    from accounts.roles import order_permission
    from .services import scope, audit
    order = scope(Order.objects.select_for_update(), user).get(pk=pk)
    if not order_permission(user, order.kind, 'create'):
        raise PermissionDenied
    if order.kind != 'Sale' or order.status not in ('Submitted', 'Approved', 'On hold'):
        raise ValidationError('Only submitted or approved sales orders can be revised.')
    if order.dispatches.exclude(status='cancelled').exists() or order.invoices.exists():
        raise ValidationError('Trucks or invoices exist on this order — short-close it and raise a new order instead.')
    base = order.number.split('-R')[0]
    new = Order.objects.get(pk=order.pk)
    new.pk = new.id = None
    new.number, new.revision, new.revised_from, new.status = f'{base}-R{order.revision + 1}', order.revision + 1, order, 'Draft'
    new.approved_by, new.approval_checks, new.created_by = None, {}, user
    new.credit_override_reason, new.credit_override_until, new.credit_override_by = '', None, None
    new.hold_reason = new.held_from = ''
    new.save()
    for l in order.lines.all():
        l.pk = l.id = None
        l.order = new
        l.save()
    order.status = 'Revised'
    order.save(update_fields=['status'])
    audit(user, 'Order revised', order, f'Revision {new.revision}: {new.number}')
    audit(user, 'Order created', new, f'Revision of {order.number}')
    return new
