"""Dealer order requests (dealer self-service, SRS §5.1 dealer portal).

A linked dealer login asks for products and quantities with a needed-by date. The dealer sees indicative list rates and
the credit still available, but nothing is committed: the sales team accepts the request into a DRAFT sales order at the
plant or depot it chooses, priced from the price list for that plant, and the normal submit → independent approval
(credit, price, stock checks) follows. The sales team can also decline with a reason; the dealer can cancel while waiting.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.roles import order_permission
from .gst import next_number, price_for
from .models import DealerOrderRequest, DealerOrderRequestLine, Order, OrderLine, Product, Site, AuditEvent

ZERO = Decimal('0')
MAX_LINES = 10


def products():
    return Product.objects.filter(active=True, category__in=['Cement', 'TMT']).order_by('category', 'name')


def indicative(partner, product, qty=0):
    rule = price_for(product, None, partner, qty)
    return rule.rate if rule else None


def credit_room(partner):
    from .partner_views import credit_position
    c = credit_position(partner)
    return {'limit': c['limit'], 'exposure': c['exposure'], 'available': c['available'], 'overdue': c['overdue']}


def my_sites(user):
    qs = Site.objects.filter(active=True)
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


def _log(user, action, ref, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=ref, detail=(detail or '')[:250])


@transaction.atomic
def submit(user, partner, required_by, lines, ship_to='', note=''):
    """lines: [(product, quantity)]. Returns the request."""
    today = timezone.localdate()
    problems = []
    clean = [(p, q) for p, q in lines if p and q]
    allowed = set(products().values_list('pk', flat=True))
    if not clean:
        problems.append('Add at least one product with a quantity.')
    if len(clean) > MAX_LINES:
        problems.append(f'At most {MAX_LINES} products per request.')
    if len({p.pk for p, _ in clean}) != len(clean):
        problems.append('Each product only once — add the quantities together.')
    for p, q in clean:
        if p.pk not in allowed:
            problems.append(f'{p.name} cannot be ordered here.')
        if q <= 0 or q > 100000:
            problems.append(f'{p.name}: enter a quantity above 0.')
    if not required_by or required_by < today or required_by > today + timedelta(days=90):
        problems.append('Needed-by date must be between today and 90 days from now.')
    if problems:
        raise ValidationError(problems)
    req = DealerOrderRequest.objects.create(number=next_number('DOR', None, today), partner=partner, requested_by=user, required_by=required_by,
                                            ship_to=(ship_to or partner.shipping_address or partner.address or '').strip()[:500], note=(note or '').strip()[:300])
    estimate = ZERO
    for p, q in clean:
        rate = indicative(partner, p, q)
        DealerOrderRequestLine.objects.create(request=req, product=p, quantity=q, indicative_rate=rate)
        if rate:
            estimate += rate * q * (1 + Decimal(str(p.gst_rate or 0)) / 100)
    req.estimate = estimate.quantize(Decimal('0.01'))
    req.save(update_fields=['estimate'])
    _log(user, 'Dealer order requested', req.number, f'{partner.name} · {len(clean)} product(s) · ₹{req.estimate}')
    return req


@transaction.atomic
def cancel(user, partner, pk):
    req = DealerOrderRequest.objects.select_for_update().filter(pk=pk, partner=partner).first()
    if req is None:
        raise ValidationError('Request not found.')
    if req.status != 'submitted':
        raise ValidationError('Only a request still waiting for Sistech can be cancelled.')
    req.status, req.decided_at = 'cancelled', timezone.now()
    req.save(update_fields=['status', 'decided_at'])
    _log(user, 'Dealer order request cancelled', req.number, partner.name)
    return req


def queue():
    """All dealer requests for the sales team (anyone who may create sales orders); the plant is chosen on accepting."""
    return DealerOrderRequest.objects.select_related('partner', 'requested_by', 'order', 'decided_by')


@transaction.atomic
def accept(user, pk, site):
    if not order_permission(user, 'Sale', 'create'):
        raise PermissionDenied
    req = DealerOrderRequest.objects.select_for_update().select_related('partner').get(pk=pk)
    if req.status != 'submitted':
        raise ValidationError('This request is no longer waiting.')
    if not site or not my_sites(user).filter(pk=site.pk).exists():
        raise ValidationError('Choose a plant or depot you work at.')
    if not req.partner.active:
        raise ValidationError(f'{req.partner.name} is inactive.')
    missing = []
    priced = []
    for l in req.lines.select_related('product'):
        rule = price_for(l.product, site, req.partner, l.quantity)
        if not rule:
            missing.append(l.product.name)
        if l.product.gst_rate is None:
            missing.append(f'{l.product.name} (no GST rate)')
        priced.append((l, rule))
    if missing:
        raise ValidationError(f'No list price at {site.code} for: {", ".join(missing)}. Add the price or raise the order manually.')
    order = Order.objects.create(number=next_number('SO', site), kind='Sale', site=site, partner=req.partner, due_date=req.required_by,
                                 ship_to=req.ship_to, customer_ref=req.number[:40], created_by=user,
                                 delivery_instructions=(req.note or '')[:200])
    for l, rule in priced:
        OrderLine.objects.create(order=order, product=l.product, quantity=l.quantity, rate=rule.rate, list_rate=rule.rate, price_rule=rule,
                                 gst_rate=l.product.gst_rate or 0, hsn=l.product.hsn)
    req.status, req.order, req.decided_by, req.decided_at = 'accepted', order, user, timezone.now()
    req.save(update_fields=['status', 'order', 'decided_by', 'decided_at'])
    _log(user, 'Dealer order request accepted', req.number, f'Draft {order.number} at {site.code}')
    from .services import audit
    audit(user, 'Order created', order)
    return order


@transaction.atomic
def reject(user, pk, reason):
    if not order_permission(user, 'Sale', 'create'):
        raise PermissionDenied
    req = DealerOrderRequest.objects.select_for_update().get(pk=pk)
    if req.status != 'submitted':
        raise ValidationError('This request is no longer waiting.')
    if not (reason or '').strip():
        raise ValidationError('Tell the dealer why — the reason is shown to them.')
    req.status, req.reason, req.decided_by, req.decided_at = 'rejected', reason.strip()[:200], user, timezone.now()
    req.save(update_fields=['status', 'reason', 'decided_by', 'decided_at'])
    _log(user, 'Dealer order request declined', req.number, reason)
    return req
