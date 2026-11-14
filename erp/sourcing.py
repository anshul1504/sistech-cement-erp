"""Asset-light sourcing: SISTECH cement and TMT are made by manufacturing partners at their plants.

- Supply contracts fix the partner's rate per product and plant; a different user approves; approving a new rate ends the old one
  the day before it starts, so history stays true.
- Purchase orders to a partner plant take the contract rate when no rate is typed, and are refused above it.
- The sourcing advisor ranks plants for a dealer order on landed cost = contract rate + contracted freight to the dealer's district,
  showing free stock and this month's capacity use; plants without a freight lane are listed last with a warning.
- The scorecard shows each partner's commitment, receipts, quality acceptance, on-time delivery and rate discipline.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .gst import qtxt, inr
from .models import SupplyContract, Site, Product, FreightRate, GRNLine, OrderLine, AuditEvent, StockBalance, clean_place

ZERO = Decimal('0')


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def current(site, product, on=None):
    on = on or timezone.localdate()
    return SupplyContract.objects.filter(site=site, product=product, status='approved', valid_from__lte=on).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=on)).order_by('-valid_from').first()


@transaction.atomic
def save_contract(user, partner, site, product, rate, valid_from, valid_to=None, monthly_quantity=0, reference='', notes=''):
    _need(user, 'add_supplycontract')
    problems = []
    if not partner.manufacturing_partner:
        problems.append(f'{partner.name} is not marked as a manufacturing partner.')
    if site.operated_by_id and site.operated_by_id != partner.pk:
        problems.append(f'{site.code} is operated by {site.operated_by.name}.')
    if Decimal(rate or 0) <= 0:
        problems.append('Enter the agreed rate.')
    if valid_to and valid_to < valid_from:
        problems.append('End date cannot be before the start date.')
    if SupplyContract.objects.filter(site=site, product=product, status='draft').exists():
        problems.append('A rate for this plant and product is already waiting for approval.')
    if problems:
        raise ValidationError(problems)
    c = SupplyContract.objects.create(partner=partner, site=site, product=product, rate=rate, valid_from=valid_from, valid_to=valid_to,
                                      monthly_quantity=monthly_quantity or 0, reference=(reference or '')[:60], notes=(notes or '')[:200], created_by=user)
    AuditEvent.objects.create(actor=user, site=site, action='Supply rate proposed', reference=f'contract:{c.pk}', detail=f'{partner.name} · {product.code} ₹{inr(c.rate, 2)} from {valid_from}')
    return c


@transaction.atomic
def decide_contract(user, pk, approve, note=''):
    _need(user, 'approve_supplycontract')
    c = SupplyContract.objects.select_for_update().select_related('site', 'product', 'partner').get(pk=pk)
    if c.status != 'draft':
        raise ValidationError('Only rates waiting for approval can be decided.')
    if c.created_by_id == user.pk:
        raise ValidationError('The person who proposed the rate cannot approve it (maker-checker).')
    if not approve and not (note or '').strip():
        raise ValidationError('Give the reason for rejecting.')
    if approve:
        for old in SupplyContract.objects.filter(site=c.site, product=c.product, status='approved').exclude(pk=c.pk).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=c.valid_from)):
            old.valid_to = c.valid_from - timedelta(days=1)
            if old.valid_to < old.valid_from:
                old.status = 'ended'
            old.save()
    c.status, c.approved_by, c.approved_at = ('approved' if approve else 'rejected'), user, timezone.now()
    if note: c.notes = note.strip()[:200]
    c.save()
    AuditEvent.objects.create(actor=user, site=c.site, action='Supply rate approved' if approve else 'Supply rate rejected', reference=f'contract:{c.pk}',
                              detail=f'{c.partner.name} · {c.product.code} ₹{inr(c.rate, 2)}' + (f' · {note}' if note else ''))
    return c


def contract_problems(order):
    """PO lines to a partner plant may not exceed the approved contract rate."""
    out = []
    for l in order.lines.select_related('product'):
        c = current(order.site, l.product, order.created_at.date() if order.created_at else None)
        if c and l.rate > c.rate:
            out.append(f'{l.product.name}: ₹{inr(l.rate, 2)} is above the contract rate ₹{inr(c.rate, 2)} at {order.site.code}.')
    return out


# ---------- Sourcing advisor ----------

def _mt_per_unit(product):
    from .stock import mt_factor
    return mt_factor(product)


def _freight(site, district, product, qty, on):
    lanes = FreightRate.objects.filter(origin=site, destination__iexact=district, active=True, valid_from__lte=on, transporter__isnull=True, vehicle_type='').filter(
        Q(valid_to__isnull=True) | Q(valid_to__gte=on)).order_by('-valid_from')
    lane = lanes.first()
    if not lane:
        return None, None
    f = _mt_per_unit(product)
    if f is None:
        return None, lane
    mt = max(Decimal(qty) * f, lane.min_mt or ZERO)
    total = lane.rate * mt if lane.basis == 'per_mt' else lane.rate
    return (total / Decimal(qty)).quantize(Decimal('0.01')), lane


def month_receipts(site, product=None, on=None):
    on = on or timezone.localdate()
    qs = GRNLine.objects.filter(grn__site=site, grn__status='posted', grn__posted_at__date__gte=on.replace(day=1), grn__posted_at__date__lte=on)
    if product: qs = qs.filter(product=product)
    return qs.aggregate(a=Sum('accepted'), h=Sum('hold'))


def options(product, qty, district, on=None):
    """Plants that can supply this product to this district, cheapest landed cost first."""
    on = on or timezone.localdate()
    district = clean_place(district)
    qty = Decimal(qty or 0) or Decimal('1')
    out = []
    sites = Site.objects.filter(active=True, kind='Plant').select_related('operated_by')
    for site in sites:
        c = current(site, product, on)
        stock = StockBalance.objects.filter(site=site, product=product, status='available').values_list('quantity', flat=True).first() or ZERO
        if not c and not stock:
            continue
        rate = c.rate if c else (StockBalance.objects.filter(site=site, product=product, status='available').values_list('avg_cost', flat=True).first() or ZERO)
        freight, lane = _freight(site, district, product, qty, on) if district else (None, None)
        got = month_receipts(site, product, on)
        received = (got['a'] or ZERO) + (got['h'] or ZERO)
        f = _mt_per_unit(product) or ZERO
        cap_month = (site.capacity_tpa / 12) if site.capacity_tpa else None
        used = (received * f / cap_month * 100).quantize(Decimal('0.1')) if cap_month and f else None
        committed = c.monthly_quantity if c else ZERO
        out.append({'site': site, 'contract': c, 'rate': rate, 'freight': freight, 'lane': lane, 'landed': rate + freight if freight is not None else None,
                    'stock': stock, 'covers': stock >= qty, 'received': received, 'committed': committed, 'used_pct': used})
    out.sort(key=lambda o: (o['landed'] is None, o['landed'] or o['rate'], not o['covers']))
    if out and out[0]['landed'] is not None:
        out[0]['best'] = True
    return out


# ---------- Scorecard ----------

def scorecard(partner, months=3):
    on = timezone.localdate()
    since = (on.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    rows = []
    for site in partner.operated_sites.filter(active=True):
        lines = GRNLine.objects.filter(grn__site=site, grn__status='posted', grn__posted_at__date__gte=since).select_related('grn__order', 'po_line')
        received = sum((l.received for l in lines), ZERO)
        accepted = sum((l.accepted + l.qc_accepted for l in lines), ZERO)
        grns = {l.grn for l in lines}
        on_time = sum(1 for g in grns if g.posted_at.date() <= g.order.due_date)
        over = 0
        for l in lines:
            c = current(site, l.product, l.grn.posted_at.date())
            if c and l.po_line.rate > c.rate:
                over += 1
        open_po = sum((l.to_receive for l in OrderLine.objects.filter(order__kind='Purchase', order__site=site, order__status__in=['Approved', 'Part received'])), ZERO)
        contracts = [c for c in site.supply_contracts.filter(status='approved').select_related('product') if c.is_current()]
        this_month = month_receipts(site, None, on)
        rows.append({'site': site, 'received': received, 'accepted': accepted, 'acceptance': (accepted / received * 100).quantize(Decimal('0.1')) if received else None,
                     'receipts': len(grns), 'on_time': (Decimal(on_time) / len(grns) * 100).quantize(Decimal('0.1')) if grns else None, 'over_rate': over,
                     'open_po': open_po, 'contracts': contracts, 'month_received': (this_month['a'] or ZERO) + (this_month['h'] or ZERO),
                     'month_committed': sum((c.monthly_quantity for c in contracts), ZERO)})
    return {'since': since, 'rows': rows}
