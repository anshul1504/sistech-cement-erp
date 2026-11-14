"""Procure-to-pay, part 1 (SRS §4.2, FR-PUR-001…003): purchase request → RFQ → quote comparison → award → purchase order.

Controls enforced here (screens and the future API both call these functions):
- a purchase request is approved or rejected by someone other than the requester (rejection needs a reason);
- an RFQ can only ask for what is still pending on an approved request;
- quotes are compared on landed cost (rate + supplier freight); awarding anyone other than the lowest valid quote (L1) needs a reason;
- expired quotes cannot be awarded; the award raises a draft PO linked line-by-line to the request;
- PO quantities can never exceed what the request approved (checked again at submit and approval);
- POs above the organisation's approval limit need the senior "high-value purchase" permission (see services.transition_order).
"""
from .gst import inr
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .gst import next_number, qtxt
from .models import (PurchaseRequest, PurchaseRequestLine, RFQ, RFQLine, SupplierQuote, SupplierQuoteLine, Order, OrderLine, Partner,
                     Product, StockBalance, AuditEvent, Organization)

ZERO = Decimal('0')
OPEN_PO = ['Draft', 'Submitted', 'Approved', 'Part received']


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, site, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, site=site, action=action, reference=reference, detail=detail[:250])


# ---------- Stock position ----------

def stock_at(site, product):
    return StockBalance.objects.filter(site=site, product=product, status='available').values_list('quantity', flat=True).first() or ZERO


def on_order(site, product):
    """Open purchase orders not yet received + approved / waiting requests not yet ordered."""
    po = ZERO
    for line in OrderLine.objects.filter(order__kind='Purchase', order__site=site, order__status__in=OPEN_PO, product=product).select_related('order'):
        po += line.to_receive
    pr = ZERO
    for line in PurchaseRequestLine.objects.filter(request__site=site, request__status__in=['draft', 'submitted', 'approved'], product=product):
        pr += line.pending_quantity
    return po, pr


def reorder_suggestions(sites):
    """Items below reorder level at each site after counting what is already on order or requested."""
    out = []
    products = list(Product.objects.filter(active=True, reorder_level__gt=0).order_by('category', 'name'))
    for site in sites:
        for p in products:
            stock = stock_at(site, p)
            if stock >= p.reorder_level:
                continue
            po, pr = on_order(site, p)
            if stock + po + pr >= p.reorder_level:
                continue
            gap = p.reorder_level - stock - po - pr
            out.append({'site': site, 'product': p, 'stock': stock, 'po': po, 'pr': pr, 'level': p.reorder_level,
                        'suggest': max(gap, p.reorder_quantity or ZERO).quantize(Decimal('0.001')), 'key': f'{site.pk}:{p.pk}'})
    return out


# ---------- Purchase request ----------

def _clean_lines(lines):
    problems, seen, out = [], set(), []
    for product, qty, rate, spec in lines:
        qty = Decimal(qty or 0)
        if qty <= 0:
            problems.append(f'{product.name}: quantity must be more than zero.')
        if product.pk in seen:
            problems.append(f'{product.name} is listed twice — put the total on one line.')
        if not product.active:
            problems.append(f'{product.name} is inactive.')
        seen.add(product.pk)
        out.append((product, qty, rate, (spec or '')[:150]))
    if not out:
        problems.append('Add at least one item.')
    return out, problems


@transaction.atomic
def save_request(user, site, needed_by, source, priority, purpose, lines, pr=None):
    """Create a draft request, or replace the lines of an existing draft. lines: [(Product, qty, est_rate|None, spec)]."""
    _need(user, 'add_purchaserequest' if not pr else 'change_purchaserequest')
    lines, problems = _clean_lines(lines)
    if not (purpose or '').strip():
        problems.append('Say what the material is for.')
    if needed_by and needed_by < timezone.localdate() and not pr:
        problems.append('Needed-by date cannot be in the past.')
    if pr and pr.status != 'draft':
        problems.append('Only draft requests can be changed.')
    if problems:
        raise ValidationError(problems)
    if pr:
        pr.site, pr.needed_by, pr.source, pr.priority, pr.purpose = site, needed_by, source, priority, purpose.strip()[:150]
        pr.save()
        pr.lines.all().delete()
    else:
        pr = PurchaseRequest.objects.create(number=next_number('PR', site), site=site, needed_by=needed_by, source=source, priority=priority,
                                            purpose=purpose.strip()[:150], created_by=user)
    for product, qty, rate, spec in lines:
        PurchaseRequestLine.objects.create(request=pr, product=product, quantity=qty, estimated_rate=rate, specification=spec, stock_at_request=stock_at(site, product))
    _log(user, site, 'Purchase request saved', pr.number, f'{len(lines)} item(s) · {pr.get_source_display()}')
    return pr


@transaction.atomic
def request_action(user, pk, action, note=''):
    pr = PurchaseRequest.objects.select_for_update().get(pk=pk)
    note = (note or '').strip()
    if action == 'submit':
        _need(user, 'add_purchaserequest')
        if pr.status != 'draft': raise ValidationError('Only draft requests can be submitted.')
        if not pr.lines.exists(): raise ValidationError('Add at least one item.')
        pr.status = 'submitted'
    elif action in ('approve', 'reject'):
        _need(user, 'approve_purchaserequest')
        if pr.status != 'submitted': raise ValidationError('Only requests waiting for approval can be decided.')
        if pr.created_by_id == user.pk: raise ValidationError('The person who raised the request cannot approve or reject it (maker-checker).')
        if action == 'reject' and not note: raise ValidationError('Give the reason for rejecting.')
        pr.status = 'approved' if action == 'approve' else 'rejected'
        pr.decided_by, pr.decided_at, pr.decision_note = user, timezone.now(), note[:200]
    elif action == 'cancel':
        _need(user, 'add_purchaserequest')
        if pr.status not in ('draft', 'submitted', 'approved'): raise ValidationError('This request is already closed.')
        if OrderLine.objects.filter(pr_line__request=pr).exclude(order__status='Cancelled').exists() or pr.rfqs.filter(status='open').exists():
            raise ValidationError('A purchase order or open RFQ uses this request — cancel that first.')
        if not note: raise ValidationError('Give the reason for cancelling.')
        pr.status, pr.decision_note = 'cancelled', note[:200]
    else:
        raise ValidationError('Unknown action.')
    pr.save()
    _log(user, pr.site, f'Purchase request {action}', pr.number, note)
    return pr


def refresh_requests(order):
    """After a PO is approved or cancelled: close fully ordered requests, reopen ones that have pending quantity again."""
    for pr in PurchaseRequest.objects.filter(lines__po_lines__order=order).distinct():
        pending = any(l.pending_quantity > 0 for l in pr.lines.all())
        if pr.status == 'approved' and not pending:
            pr.status = 'closed'
            pr.save(update_fields=['status'])
        elif pr.status == 'closed' and pending:
            pr.status = 'approved'
            pr.save(update_fields=['status'])


def check_po_against_requests(order):
    """Every PO line linked to a request must match the approved item and stay within what is still pending."""
    problems = []
    for line in order.lines.select_related('product', 'pr_line__request', 'pr_line__product'):
        pl = line.pr_line
        if not pl:
            continue
        if pl.request.status not in ('approved', 'closed'):
            problems.append(f'{pl.request.number} is {pl.request.get_status_display().lower()}.')
        if pl.product_id != line.product_id:
            problems.append(f'{line.product.name} is not the item approved on {pl.request.number} ({pl.product.name}).')
        free = pl.quantity - pl.ordered_quantity(exclude_order=order.pk)
        if line.quantity > free:
            problems.append(f'{line.product.name}: {qtxt(line.quantity)} ordered but only {qtxt(max(free, ZERO))} {line.product.unit} left on {pl.request.number}.')
    return problems


# ---------- RFQ ----------

@transaction.atomic
def create_rfq(user, pr, suppliers, quotes_due, terms, quantities):
    """quantities: {PurchaseRequestLine: qty}."""
    _need(user, 'add_rfq')
    pr = PurchaseRequest.objects.select_for_update().get(pk=pr.pk)
    problems = []
    if pr.status != 'approved': problems.append('Only approved purchase requests can go for quotation.')
    if pr.rfqs.filter(status='open').exists(): problems.append(f'{pr.rfqs.filter(status="open").first().number} is already open for this request.')
    suppliers = [s for s in suppliers if s.kind == 'Supplier']
    if not suppliers: problems.append('Invite at least one supplier.')
    for s in suppliers:
        if not s.active: problems.append(f'{s.name} is inactive.')
    if not quotes_due or quotes_due < timezone.localdate(): problems.append('Quotes-due date must be today or later.')
    picked = {l: Decimal(q) for l, q in quantities.items() if q and Decimal(q) > 0}
    if not picked: problems.append('Enter a quantity for at least one item.')
    for l, q in picked.items():
        if l.request_id != pr.pk: raise ValidationError('Item does not belong to this request.')
        if q > l.pending_quantity: problems.append(f'{l.product.name}: only {qtxt(l.pending_quantity)} {l.product.unit} pending.')
    if problems:
        raise ValidationError(problems)
    rfq = RFQ.objects.create(number=next_number('RFQ', pr.site), request=pr, site=pr.site, quotes_due=quotes_due, terms=(terms or '')[:200], created_by=user)
    rfq.suppliers.set(suppliers)
    for l, q in picked.items():
        RFQLine.objects.create(rfq=rfq, pr_line=l, product=l.product, quantity=q)
    _log(user, pr.site, 'RFQ created', rfq.number, f'{pr.number} · {len(suppliers)} supplier(s)')
    return rfq


@transaction.atomic
def save_quote(user, rfq, supplier, data, rates):
    """data: reference, quote_date, valid_until, freight, lead_time_days, payment_terms_days, notes. rates: {RFQLine: (rate, gst)}."""
    _need(user, 'add_rfq')
    rfq = RFQ.objects.select_for_update().get(pk=rfq.pk)
    problems = []
    if rfq.status != 'open': problems.append('This RFQ is no longer collecting quotes.')
    if not rfq.suppliers.filter(pk=supplier.pk).exists(): problems.append(f'{supplier.name} was not invited to {rfq.number}.')
    lines = list(rfq.lines.select_related('product'))
    for l in lines:
        rate, gst = rates.get(l, (None, None))
        if rate is None: problems.append(f'{l.product.name}: enter the quoted rate.')
        elif rate <= 0: problems.append(f'{l.product.name}: rate must be more than zero.')
        if gst is None or gst < 0 or gst > 28: problems.append(f'{l.product.name}: GST % must be between 0 and 28.')
    if data.get('freight') is not None and data['freight'] < 0: problems.append('Freight cannot be negative.')
    if data.get('valid_until') and data.get('quote_date') and data['valid_until'] < data['quote_date']: problems.append('Valid-until cannot be before the quote date.')
    if problems:
        raise ValidationError(problems)
    quote, created = SupplierQuote.objects.update_or_create(rfq=rfq, supplier=supplier, defaults={**data, 'created_by': user})
    for l in lines:
        rate, gst = rates[l]
        SupplierQuoteLine.objects.update_or_create(quote=quote, rfq_line=l, defaults={'rate': rate, 'gst_rate': gst})
    _log(user, rfq.site, 'Quote recorded' if created else 'Quote revised', rfq.number, f'{supplier.name} · landed ₹{inr(quote.landed, 2)}')
    return quote


def supplier_quality(supplier):
    """Share of received quantity accepted on this supplier's GRNs (None until there is receiving history)."""
    try:
        from .models import GRNLine
    except ImportError:
        return None
    agg = GRNLine.objects.filter(grn__supplier=supplier, grn__status='posted').aggregate(r=Sum('received'), a=Sum('accepted'))
    if not agg['r']:
        return None
    return (agg['a'] or ZERO) / agg['r'] * 100


def compare(rfq):
    """Quote comparison: per-line rates with landed rate (freight spread by value), totals, L1 ranking and supplier facts."""
    lines = list(rfq.lines.select_related('product'))
    quotes = list(rfq.quotes.select_related('supplier').prefetch_related('lines'))
    today = timezone.localdate()
    for q in quotes:
        q.rate_by_line = {ql.rfq_line_id: ql for ql in q.lines.all()}
        q.valid = q.is_valid(today)
        q.quality = supplier_quality(q.supplier)
        taxable = q.taxable
        q.landed_rate = {}
        for l in lines:
            ql = q.rate_by_line.get(l.pk)
            if ql:
                share = (ql.rate * l.quantity / taxable * q.freight) if taxable else ZERO
                q.landed_rate[l.pk] = (ql.rate + share / l.quantity).quantize(Decimal('0.01'))
    ranked = sorted([q for q in quotes if q.valid], key=lambda q: (q.landed, q.lead_time_days, -q.payment_terms_days))
    for i, q in enumerate(ranked, 1):
        q.rank = f'L{i}'
    for q in quotes:
        if not q.valid:
            q.rank = 'Expired'
    rows = []
    for l in lines:
        cells = []
        best = min((q.landed_rate[l.pk] for q in quotes if q.valid and l.pk in q.landed_rate), default=None)
        for q in quotes:
            ql = q.rate_by_line.get(l.pk)
            cells.append({'quote': q, 'rate': ql.rate if ql else None, 'gst': ql.gst_rate if ql else None, 'landed': q.landed_rate.get(l.pk),
                          'best': best is not None and q.landed_rate.get(l.pk) == best})
        rows.append({'line': l, 'cells': cells})
    return {'lines': lines, 'quotes': quotes, 'rows': rows, 'l1': ranked[0] if ranked else None}


def _po_lines_from(quote):
    """Supplier freight is part of the value of supply (GST s.15), so it is spread into the line rate pro rata to value."""
    lines = list(quote.lines.select_related('rfq_line__product', 'rfq_line__pr_line'))
    taxable = sum((ql.rate * ql.rfq_line.quantity for ql in lines), ZERO)
    out = []
    for ql in lines:
        rl = ql.rfq_line
        share = (ql.rate * rl.quantity / taxable * quote.freight) if taxable and quote.freight else ZERO
        rate = (ql.rate + share / rl.quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        out.append((rl, rate, ql.gst_rate))
    return out


@transaction.atomic
def award(user, rfq_pk, quote_pk, reason='', tolerance=ZERO, delivery_terms=''):
    _need(user, 'award_rfq')
    rfq = RFQ.objects.select_for_update().select_related('request', 'site').get(pk=rfq_pk)
    if rfq.status != 'open':
        raise ValidationError('This RFQ has already been awarded or cancelled.')
    quote = rfq.quotes.select_related('supplier').get(pk=quote_pk)
    reason = (reason or '').strip()
    if not quote.is_valid():
        raise ValidationError(f"{quote.supplier.name}'s quote expired on {quote.valid_until:%d %b %Y}. Ask for a revised quote.")
    if not quote.supplier.active:
        raise ValidationError(f'{quote.supplier.name} is inactive.')
    l1 = compare(rfq)['l1']
    if l1 and l1.pk != quote.pk and not reason:
        raise ValidationError(f'{quote.supplier.name} is not the lowest landed cost (L1 is {l1.supplier.name} at ₹{inr(l1.landed, 2)}). Give the reason for choosing them.')
    note = f'From {rfq.number} / {rfq.request.number}.' + (f' Rate includes supplier freight ₹{inr(quote.freight, 2)}.' if quote.freight else '')
    order = Order.objects.create(number=next_number('PO', rfq.site), kind='Purchase', site=rfq.site, partner=quote.supplier, created_by=user,
                                 due_date=max(rfq.request.needed_by, timezone.localdate() + timedelta(days=quote.lead_time_days)),
                                 customer_ref=quote.reference[:40], quote=quote, award_reason=(reason or 'Lowest landed cost (L1)')[:200],
                                 payment_terms_days=quote.payment_terms_days, receipt_tolerance_pct=tolerance or ZERO, delivery_terms=(delivery_terms or '')[:120], notes=note)
    for rl, rate, gst in _po_lines_from(quote):
        OrderLine.objects.create(order=order, product=rl.product, quantity=rl.quantity, rate=rate, gst_rate=gst, hsn=rl.product.hsn, pr_line=rl.pr_line)
    rfq.status = 'awarded'
    rfq.save(update_fields=['status'])
    _log(user, rfq.site, 'RFQ awarded', rfq.number, f'{quote.supplier.name} · {order.number} · ₹{inr(quote.landed, 2)}' + (f' · {reason}' if reason else ''))
    _log(user, rfq.site, 'Order created', order.number, f'Awarded from {rfq.number}')
    return order


@transaction.atomic
def cancel_rfq(user, pk, note):
    _need(user, 'add_rfq')
    rfq = RFQ.objects.select_for_update().get(pk=pk)
    if rfq.status != 'open':
        raise ValidationError('Only open RFQs can be cancelled.')
    if not (note or '').strip():
        raise ValidationError('Give the reason for cancelling.')
    rfq.status = 'cancelled'
    rfq.save(update_fields=['status'])
    _log(user, rfq.site, 'RFQ cancelled', rfq.number, note.strip())
    return rfq


@transaction.atomic
def direct_po(user, pr, supplier, reason, quantities, rates):
    """Single-source purchase without RFQ (OEM spare, rate contract, emergency). Reason is mandatory and shown to the PO approver."""
    if not user.has_perm('erp.create_purchase_order'):
        raise PermissionDenied
    pr = PurchaseRequest.objects.select_for_update().get(pk=pr.pk)
    problems = []
    if pr.status != 'approved': problems.append('Only approved purchase requests can be ordered.')
    if supplier.kind != 'Supplier' or not supplier.active: problems.append('Choose an active supplier.')
    if not (reason or '').strip(): problems.append('Say why this is bought without quotations (single source, rate contract, breakdown…).')
    picked = {l: Decimal(q) for l, q in quantities.items() if q and Decimal(q) > 0}
    if not picked: problems.append('Enter a quantity for at least one item.')
    for l, q in picked.items():
        if l.request_id != pr.pk: raise ValidationError('Item does not belong to this request.')
        if q > l.pending_quantity: problems.append(f'{l.product.name}: only {qtxt(l.pending_quantity)} {l.product.unit} pending.')
        if not rates.get(l) or rates[l] <= 0: problems.append(f'{l.product.name}: enter the agreed rate.')
        if l.product.gst_rate is None: problems.append(f'{l.product.name} has no GST rate. Set it in Products first.')
    if problems:
        raise ValidationError(problems)
    order = Order.objects.create(number=next_number('PO', pr.site), kind='Purchase', site=pr.site, partner=supplier, created_by=user,
                                 due_date=max(pr.needed_by, timezone.localdate()), award_reason=('Direct: ' + reason.strip())[:200],
                                 payment_terms_days=supplier.payment_terms_days, notes=f'From {pr.number} without RFQ.')
    for l, q in picked.items():
        OrderLine.objects.create(order=order, product=l.product, quantity=q, rate=rates[l], gst_rate=l.product.gst_rate, hsn=l.product.hsn, pr_line=l)
    _log(user, pr.site, 'Order created', order.number, f'Direct from {pr.number} · {reason.strip()}')
    return order


def approval_limit():
    return Organization.objects.filter(pk=1).values_list('po_approval_limit', flat=True).first() or Decimal('500000')
