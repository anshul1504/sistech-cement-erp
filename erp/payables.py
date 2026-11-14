"""Supplier invoices, three-way match and debit notes (SRS §4.2, FR-PUR-008…010).

Three-way match, line by line: supplier's billed quantity ≤ accepted GRN quantity not yet billed (+ quantity tolerance),
billed rate ≤ PO rate (+ rate tolerance), billed GST % = PO GST %. The header must add up to its lines (₹1 rounding) and the
GSTIN must be the supplier's. Clean invoices are Matched and payable; anything else waits in the exception queue for a
different user who pays as billed (with a note), pays the matched value (difference raised as a debit note) or rejects.

Duplicate check (FR-PUR-009): same supplier + invoice number (ignoring case, spaces and punctuation), or same supplier +
GSTIN + date + amount, is refused. All amounts are INR.

Debit notes (FR-PUR-010) link to the GRN. A 'return' sends accepted stock back (stock reduces); its value counts only for
quantity the supplier has already billed, so nothing is claimed twice. Other reasons are value claims without stock movement.
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from .gst import next_number, qtxt, inr
from .models import SupplierInvoice, SupplierInvoiceLine, DebitNote, DebitNoteLine, GRNLine, AuditEvent, Organization
from .services import move_stock, require_site

ZERO = Decimal('0')
CENT = Decimal('0.01')
LIVE = ('matched', 'exception', 'approved')


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, site, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, site=site, action=action, reference=reference, detail=detail[:250])


def money(v):
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def invoice_key(text):
    return ''.join(ch for ch in (text or '').upper() if ch.isalnum())[:40]


def tolerances():
    org = Organization.objects.filter(pk=1).values('match_qty_tolerance_pct', 'match_price_tolerance_pct').first() or {}
    return org.get('match_qty_tolerance_pct') or ZERO, org.get('match_price_tolerance_pct') or ZERO


# ---------- Quantities per GRN line ----------

def billed_qty(gl, exclude_invoice=None):
    qs = gl.invoice_lines.filter(invoice__status__in=LIVE)
    if exclude_invoice:
        qs = qs.exclude(invoice_id=exclude_invoice)
    return qs.aggregate(q=Sum('quantity'))['q'] or ZERO


def returned_qty(gl, from_stock=True):
    return gl.debit_lines.filter(from_stock=from_stock).aggregate(q=Sum('quantity'))['q'] or ZERO


def open_to_bill(gl, exclude_invoice=None):
    """Accepted (incl. after QC) − sent back − already billed."""
    return max(gl.usable - returned_qty(gl) - billed_qty(gl, exclude_invoice), ZERO)


def billable_lines(supplier):
    lines = GRNLine.objects.filter(grn__supplier=supplier, grn__status='posted').select_related('grn__site', 'grn__order', 'product', 'po_line').order_by('grn__posted_at', 'pk')
    out = []
    for gl in lines:
        gl.open = open_to_bill(gl)
        if gl.open > 0:
            out.append(gl)
    return out


# ---------- Supplier invoice ----------

def _match(supplier, header, rows, exclude=None):
    """Returns (line results, issues, matched_taxable, matched_tax)."""
    qty_tol, rate_tol = tolerances()
    issues, results = [], []
    m_taxable = m_tax = b_taxable = b_tax = ZERO
    for gl, (qty, rate, gst) in rows.items():
        po = gl.po_line
        open_qty = open_to_bill(gl, exclude)
        cap_qty = open_qty * (1 + qty_tol / 100)
        cap_rate = (po.rate * (1 + rate_tol / 100)).quantize(CENT)
        issue = []
        if qty > cap_qty:
            issue.append(f'billed {qtxt(qty)} but only {qtxt(open_qty)} {gl.product.unit} accepted and not yet billed')
        if rate > cap_rate:
            issue.append(f'rate ₹{inr(rate)} above PO ₹{inr(po.rate)}')
        if gst != po.gst_rate:
            issue.append(f'GST {qtxt(gst)}% but PO has {qtxt(po.gst_rate)}%')
        mq, mr = min(qty, cap_qty), min(rate, cap_rate)
        results.append((gl, qty, rate, gst, po.rate, po.gst_rate, open_qty, mq, mr, '; '.join(issue)))
        if issue:
            issues.append(f'{gl.product.name} ({gl.grn.number}): ' + '; '.join(issue) + '.')
        mv = money(mq * mr)
        m_taxable += mv
        m_tax += money(mv * po.gst_rate / 100)
        bv = money(qty * rate)
        b_taxable += bv
        b_tax += money(bv * gst / 100)
    if abs(header['billed_taxable'] - b_taxable) > 1:
        issues.append(f'Taxable value on invoice ₹{inr(header["billed_taxable"])} ≠ lines ₹{inr(b_taxable)}.')
    if abs(header['billed_tax'] - b_tax) > 1:
        issues.append(f'GST on invoice ₹{inr(header["billed_tax"])} ≠ lines ₹{inr(b_tax)}.')
    if abs(header['billed_total'] - header['billed_taxable'] - header['billed_tax']) > 1:
        issues.append('Invoice total is not taxable value + GST (more than ₹1 round-off).')
    gstin = (header.get('supplier_gstin') or '').strip().upper()
    if supplier.gstin and gstin != supplier.gstin.upper():
        issues.append(f'GSTIN on invoice ({gstin or "blank"}) is not {supplier.name}\'s GSTIN {supplier.gstin}.')
    if not supplier.gstin and header['billed_tax'] > 0:
        issues.append(f'{supplier.name} has no GSTIN in the master but charges GST — check registration before claiming input credit.')
    return results, issues, m_taxable, m_tax


@transaction.atomic
def create_invoice(user, supplier, header, rows, notes=''):
    """header: invoice_number, invoice_date, supplier_gstin, billed_taxable, billed_tax, billed_total. rows: {GRNLine: (qty, rate, gst%)}."""
    _need(user, 'add_supplierinvoice')
    problems = []
    key = invoice_key(header.get('invoice_number'))
    if not key:
        problems.append("Enter the supplier's invoice number.")
    date = header.get('invoice_date')
    if not date:
        problems.append('Enter the invoice date.')
    elif date > timezone.localdate():
        problems.append('Invoice date cannot be in the future.')
    for k in ('billed_taxable', 'billed_tax', 'billed_total'):
        if header.get(k) is None or header[k] < 0:
            problems.append('Enter the taxable value, GST and total from the invoice.')
            break
    live = SupplierInvoice.objects.select_for_update().filter(supplier=supplier, status__in=LIVE)
    if key and live.filter(invoice_key=key).exists():
        problems.append(f'Invoice {header["invoice_number"]} from {supplier.name} is already entered.')
    elif date and header.get('billed_total') is not None and live.filter(invoice_date=date, billed_total=header['billed_total'],
                                                                          supplier_gstin=(header.get('supplier_gstin') or '').strip().upper()).exists():
        problems.append(f'An invoice from {supplier.name} dated {date:%d %b %Y} for ₹{inr(header["billed_total"])} is already entered — possible duplicate.')
    rows = {gl.pk: v for gl, v in rows.items() if v[0] and v[0] > 0}
    fresh = {gl.pk: gl for gl in GRNLine.objects.select_for_update().filter(pk__in=rows).select_related('grn__site', 'grn__order', 'product', 'po_line')}
    rows = {fresh[pk]: v for pk, v in rows.items()}
    if not rows:
        problems.append('Enter the billed quantity for at least one received item.')
    sites = set()
    for gl, (qty, rate, gst) in rows.items():
        if gl.grn.supplier_id != supplier.pk or gl.grn.status != 'posted':
            raise ValidationError('Item is not from a posted receipt of this supplier.')
        if rate is None or rate < 0 or gst is None or gst < 0 or gst > 28:
            problems.append(f'{gl.product.name}: enter the billed rate and GST %.')
        require_site(user, gl.grn.site)
        sites.add(gl.grn.site_id)
    if len(sites) > 1:
        problems.append('One supplier invoice is for one plant (GST registration). Enter receipts of each plant separately.')
    if problems:
        raise ValidationError(problems)
    from .gl import check_open
    check_open(date)
    header = {**header, 'supplier_gstin': (header.get('supplier_gstin') or '').strip().upper()}
    results, issues, m_taxable, m_tax = _match(supplier, header, rows)
    first = next(iter(rows))
    terms = first.grn.order.payment_terms_days
    terms = supplier.payment_terms_days if terms is None else terms
    inv = SupplierInvoice.objects.create(number=next_number('SINV', first.grn.site), supplier=supplier, site=first.grn.site, invoice_number=header['invoice_number'].strip()[:40],
                                         invoice_key=key, invoice_date=date, supplier_gstin=header['supplier_gstin'], billed_taxable=header['billed_taxable'],
                                         billed_tax=header['billed_tax'], billed_total=header['billed_total'], matched_taxable=m_taxable, matched_tax=m_tax,
                                         status='exception' if issues else 'matched', issues=issues, payable=ZERO if issues else header['billed_total'],
                                         due_date=date + timedelta(days=terms or 0), notes=(notes or '')[:200], created_by=user)
    for gl, qty, rate, gst, po_rate, po_gst, open_qty, mq, mr, issue in results:
        SupplierInvoiceLine.objects.create(invoice=inv, grn_line=gl, quantity=qty, rate=rate, gst_rate=gst, po_rate=po_rate, po_gst_rate=po_gst, open_qty=open_qty,
                                           matched_qty=mq, matched_rate=mr, issue=issue[:200])
    if inv.status == 'matched':
        from .gl import post_supplier_invoice
        post_supplier_invoice(user, inv)
    _log(user, inv.site, 'Supplier invoice entered', inv.number, f'{supplier.name} · {inv.invoice_number} · ₹{inr(inv.billed_total)} · {inv.get_status_display()}')
    return inv


@transaction.atomic
def decide(user, pk, decision, note):
    _need(user, 'approve_supplierinvoice')
    inv = SupplierInvoice.objects.select_for_update().select_related('supplier', 'site').get(pk=pk)
    require_site(user, inv.site)
    note = (note or '').strip()
    if inv.status != 'exception':
        raise ValidationError('Only invoices in the exception queue need a decision.')
    if inv.created_by_id == user.pk:
        raise ValidationError('The person who entered the invoice cannot decide it (maker-checker).')
    if decision not in ('billed', 'matched', 'reject'):
        raise ValidationError('Unknown decision.')
    if not note:
        raise ValidationError('Record why (e.g. rate revised by mail of 12 Nov, or short supply).')
    inv.decision, inv.decided_by, inv.decided_at, inv.decision_note = decision, user, timezone.now(), note[:200]
    if decision == 'reject':
        inv.status, inv.payable = 'rejected', ZERO
    elif decision == 'billed':
        inv.status, inv.payable = 'approved', inv.billed_total
    else:
        diff_taxable = money(inv.billed_taxable - inv.matched_taxable)
        diff_tax = money(inv.billed_tax - inv.matched_tax)
        if diff_taxable < 0 or diff_tax < 0 or diff_taxable + diff_tax <= 0:
            raise ValidationError('Billed value is not above the matched value — pay as billed instead.')
        dn = DebitNote.objects.create(number=next_number('DN', inv.site), supplier=inv.supplier, site=inv.site, supplier_invoice=inv, reason='rate',
                                      remarks=f'Difference on invoice {inv.invoice_number}: {note}'[:200], taxable=diff_taxable, tax=diff_tax,
                                      total=money(inv.billed_total - inv.matched_taxable - inv.matched_tax), created_by=user)
        for l in inv.lines.select_related('grn_line__product'):
            d = money(l.billed_value - l.matched_value)
            if d > 0:
                DebitNoteLine.objects.create(note=dn, grn_line=l.grn_line, product=l.grn_line.product, quantity=max(l.quantity - l.matched_qty, ZERO),
                                             rate=l.rate, gst_rate=l.gst_rate, amount=d)
        inv.status, inv.payable = 'approved', inv.billed_total - dn.total
        _log(user, inv.site, 'Debit note raised', dn.number, f'{inv.supplier.name} · ₹{inr(dn.total)} · against {inv.invoice_number}')
    inv.save()
    if inv.status == 'approved':
        from .gl import post_supplier_invoice, post_debit_note
        post_supplier_invoice(user, inv)
        for dn in inv.debit_notes.all():
            post_debit_note(user, dn)
    _log(user, inv.site, f'Supplier invoice {inv.get_decision_display().lower()}', inv.number, f'payable ₹{inr(inv.payable)} · {note}')
    return inv


# ---------- Debit notes ----------

@transaction.atomic
def create_debit_note(user, grn, reason, remarks, quantities, vehicle=''):
    """quantities: {GRNLine: qty}. reason 'return' sends accepted stock back; others are value claims on what was received."""
    _need(user, 'add_debitnote')
    grn = type(grn).objects.select_for_update().select_related('site', 'supplier').get(pk=grn.pk)
    require_site(user, grn.site)
    remarks = (remarks or '').strip()
    problems = []
    if grn.status != 'posted':
        problems.append('Debit notes are raised against posted receipts.')
    if reason not in dict(DebitNote._meta.get_field('reason').choices):
        problems.append('Choose the reason.')
    if not remarks:
        problems.append('Describe the issue (quality report, return reference…).')
    stock = reason == 'return'
    picked = {gl.pk: Decimal(q) for gl, q in quantities.items() if q and Decimal(q) > 0}
    fresh = {gl.pk: gl for gl in GRNLine.objects.select_for_update().filter(pk__in=picked).select_related('product', 'po_line')}
    picked = {fresh[pk]: q for pk, q in picked.items()}
    if not picked:
        problems.append('Enter a quantity for at least one item.')
    for gl in picked:
        if gl.grn_id != grn.pk:
            raise ValidationError('Item does not belong to this receipt.')
        q = picked[gl]
        if stock:
            room = gl.usable - returned_qty(gl)
            if q > room:
                problems.append(f'{gl.product.name}: only {qtxt(room)} {gl.product.unit} accepted and not yet returned.')
        else:
            room = gl.received - returned_qty(gl, from_stock=False)
            if q > room:
                problems.append(f'{gl.product.name}: claim is more than the {qtxt(room)} {gl.product.unit} received and not yet claimed.')
    if stock and not (vehicle or '').strip():
        problems.append('Enter the vehicle taking the material back.')
    if problems:
        raise ValidationError(problems)
    dn = DebitNote.objects.create(number=next_number('DN', grn.site), supplier=grn.supplier, site=grn.site, grn=grn, reason=reason, remarks=remarks[:200],
                                  vehicle_number=''.join(ch for ch in (vehicle or '').upper() if ch.isalnum())[:15], created_by=user)
    taxable = tax = ZERO
    for gl, q in picked.items():
        rate, gst = gl.po_line.rate, gl.po_line.gst_rate
        valued = q
        if stock:
            # Value only what has been billed and not already credited by earlier returns; the rest just stops being billable.
            already = sum((l.amount / l.rate for l in gl.debit_lines.filter(from_stock=True) if l.rate), ZERO)
            valued = min(q, max(billed_qty(gl) - already, ZERO))
        amount = money(valued * rate)
        DebitNoteLine.objects.create(note=dn, grn_line=gl, product=gl.product, from_stock=stock, quantity=q, rate=rate, gst_rate=gst, amount=amount)
        taxable += amount
        tax += money(amount * gst / 100)
        if stock:
            move_stock(user, grn.site, gl.product, -q, f'dn:{dn.pk}:line:{gl.pk}', 'PurchaseReturn', f'{dn.number} → {grn.supplier.name}')
    dn.taxable, dn.tax, dn.total = taxable, tax, money(taxable + tax)
    dn.save(update_fields=['taxable', 'tax', 'total'])
    from .gl import post_debit_note
    post_debit_note(user, dn)
    _log(user, grn.site, 'Debit note raised', dn.number, f'{grn.supplier.name} · {dn.get_reason_display()} · ₹{inr(dn.total)} · {grn.number}')
    return dn


# ---------- Payables ----------

def standalone_debits(supplier):
    """Debit notes not already netted inside an approved invoice's payable."""
    return DebitNote.objects.filter(supplier=supplier).exclude(supplier_invoice__decision='matched')


def balance(supplier):
    inv = SupplierInvoice.objects.filter(supplier=supplier, status__in=['matched', 'approved']).aggregate(p=Sum('payable'))['p'] or ZERO
    dn = standalone_debits(supplier).aggregate(t=Sum('total'))['t'] or ZERO
    from .models import FixedAsset, PaymentAllocation
    assets = FixedAsset.objects.filter(supplier=supplier, credit_purchase=True).aggregate(t=Sum('cost'))['t'] or ZERO
    paid = PaymentAllocation.objects.filter(payment__payee=supplier, payment__status='paid', freight_bill__isnull=True).aggregate(t=Sum('amount'))['t'] or ZERO
    return {'invoices': inv, 'debits': dn, 'assets': assets, 'payments': paid, 'balance': inv + assets - dn - paid}
