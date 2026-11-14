"""Customer accounts: settlement of receipts and credit notes against invoices (oldest due first), balances, ageing, ledger, credit exposure.

Every screen (dealer page, ledger, ageing, order approval) reads from here so they always agree.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

ZERO = Decimal('0.00')
AGE_BUCKETS = [('current', 'Not due'), ('d30', '1–30 days'), ('d60', '31–60 days'), ('d90', '61–90 days'), ('d90p', '90+ days')]


def _sum(qs, field):
    return qs.aggregate(t=Sum(field))['t'] or ZERO


def credit_unapplied(cn):
    return cn.total - _sum(cn.settlements.all(), 'amount')


@transaction.atomic
def allocate(partner):
    """Apply every unapplied credit (credit notes first, then receipts by date) to open invoices, oldest due date first."""
    from .models import Partner, Invoice, Receipt, CreditNote, Settlement
    Partner.objects.select_for_update().get(pk=partner.pk)
    invoices = [i for i in Invoice.objects.filter(partner=partner).order_by('due_date', 'date', 'id') if i.outstanding > 0]
    if not invoices:
        return 0
    sources = [('credit_note', c, credit_unapplied(c)) for c in CreditNote.objects.filter(partner=partner).order_by('date', 'id')]
    sources += [('receipt', r, r.unallocated) for r in Receipt.objects.filter(partner=partner, status='posted').order_by('date', 'id')]
    made = 0
    for kind, source, free in sources:
        for inv in invoices:
            if free <= 0:
                break
            due = inv.outstanding
            if due <= 0:
                continue
            amount = min(free, due)
            Settlement.objects.create(invoice=inv, amount=amount, **{kind: source})
            free -= amount
            made += 1
    return made


def balance(partner):
    """Positive = customer owes us; negative = advance with us."""
    from .models import Invoice, Receipt, CreditNote
    billed = _sum(Invoice.objects.filter(partner=partner), 'grand_total')
    received = _sum(Receipt.objects.filter(partner=partner, status='posted'), 'amount')
    credited = _sum(CreditNote.objects.filter(partner=partner), 'total')
    return billed - received - credited


def open_invoices(partner=None):
    from .models import Invoice
    qs = Invoice.objects.select_related('partner').prefetch_related('settlements').order_by('due_date', 'date', 'id')
    if partner:
        qs = qs.filter(partner=partner)
    return [i for i in qs if i.outstanding > 0]


def ageing(invoices, on=None):
    on = on or timezone.localdate()
    out = {k: ZERO for k, _ in AGE_BUCKETS}
    for inv in invoices:
        days = (on - inv.due_date).days
        key = 'current' if days <= 0 else 'd30' if days <= 30 else 'd60' if days <= 60 else 'd90' if days <= 90 else 'd90p'
        out[key] += inv.outstanding
    out['total'] = sum((out[k] for k, _ in AGE_BUCKETS), ZERO)
    out['overdue'] = out['total'] - out['current']
    return out


def uninvoiced_orders(partner):
    """Approved / part-dispatched / dispatched orders whose value is not fully invoiced yet."""
    from .models import Order
    return Order.objects.filter(kind='Sale', partner=partner, status__in=['Approved', 'Part dispatched', 'Completed']) \
        .select_related('site', 'partner').prefetch_related('lines', 'invoices')


def uninvoiced_value(order):
    invoiced = sum((i.grand_total for i in order.invoices.all()), ZERO)
    return max(order.grand_total - invoiced, ZERO)


def exposure(partner, exclude_order=None):
    orders = uninvoiced_orders(partner)
    if exclude_order:
        orders = orders.exclude(pk=exclude_order)
    pipeline = sum((uninvoiced_value(o) for o in orders), ZERO)
    return {'balance': balance(partner), 'pipeline': pipeline, 'total': balance(partner) + pipeline}


def overdue(partner, grace_days=0):
    cutoff = timezone.localdate() - timedelta(days=grace_days)
    return [i for i in open_invoices(partner) if i.due_date < cutoff]


def ledger_rows(partner, start=None, end=None):
    """Chronological statement with running balance. Rows before `start` roll into the opening balance."""
    from .models import Invoice, Receipt, CreditNote
    rows = []
    for i in Invoice.objects.filter(partner=partner):
        rows.append({'date': i.date, 'kind': 'Invoice', 'ref': i.number, 'obj': i, 'debit': i.grand_total, 'credit': ZERO, 'note': f'Due {i.due_date:%d %b %Y}', 'seq': (i.created_at, 1)})
    for r in Receipt.objects.filter(partner=partner):
        rows.append({'date': r.date, 'kind': 'Receipt', 'ref': r.number, 'obj': r, 'debit': ZERO, 'credit': r.amount, 'note': f'{r.get_mode_display()} {r.reference}'.strip(), 'seq': (r.created_at, 2)})
        if r.status == 'bounced':
            rows.append({'date': r.bounced_on or r.date, 'kind': 'Cheque bounced', 'ref': r.number, 'obj': r, 'debit': r.amount, 'credit': ZERO, 'note': r.bounce_reason, 'seq': (r.created_at, 3)})
    for c in CreditNote.objects.filter(partner=partner).select_related('invoice'):
        rows.append({'date': c.date, 'kind': 'Credit note', 'ref': c.number, 'obj': c, 'debit': ZERO, 'credit': c.total, 'note': f'{c.get_reason_display()} · {c.invoice.number}', 'seq': (c.created_at, 2)})
    rows.sort(key=lambda r: (r['date'], r['seq']))
    opening, out, running = ZERO, [], ZERO
    for r in rows:
        running += r['debit'] - r['credit']
        if start and r['date'] < start:
            opening = running
            continue
        if end and r['date'] > end:
            continue
        r['balance'] = running
        out.append(r)
    return opening, out
