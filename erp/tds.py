"""TDS on vendor bills (SRS FR-FIN-016) — applies only to parties whose TDS section and rate the tax advisor has set.

Deduction: on a supplier invoice (base = taxable value billed) or freight bill (base = approved payable), amount =
base × rate, rounded to the rupee, capped at what is still due. Posts Dr Trade payable (party) / Cr TDS payable and reduces
the bill's open amount for payment. Reversal (only before the challan) posts the opposite entry.
Challan: deposits one month and section — all undeposited deductions of that month/section — Dr TDS payable / Cr bank.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .finance_access import require_company_finance
from .gst import next_number
from .models import TdsDeduction, TdsChallan, SupplierInvoice, FreightBill, AuditEvent

ZERO = Decimal('0')


def _need(user, perm):
    require_company_finance(user)
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def deducted(bill):
    field = 'supplier_invoice' if isinstance(bill, SupplierInvoice) else 'freight_bill'
    return TdsDeduction.objects.filter(**{field: bill}, status='posted').aggregate(t=Sum('amount'))['t'] or ZERO


def suggestions():
    """Open bills of TDS-configured parties without a deduction yet, with the computed amount."""
    from .treasury import open_items
    from .models import Partner
    out = []
    for p in Partner.objects.filter(tds_rate__gt=0).exclude(tds_section=''):
        for item in open_items(p):
            bill = item['obj']
            if item['kind'] not in ('sinv', 'frb') or deducted(bill):
                continue
            base = bill.billed_taxable if item['kind'] == 'sinv' else bill.payable
            amt = min((base * p.tds_rate / 100).quantize(Decimal('1')), item['amount'])
            if amt > 0:
                out.append({'payee': p, 'kind': item['kind'], 'bill': bill, 'ref': item['ref'], 'date': item['date'], 'base': base, 'amount': amt, 'due': item['amount']})
    return out


@transaction.atomic
def deduct(user, kind, pk, on=None, note=''):
    _need(user, 'add_tdsdeduction')
    from . import gl
    from .treasury import open_items
    bill = (SupplierInvoice if kind == 'sinv' else FreightBill).objects.select_for_update().get(pk=pk)
    payee = bill.supplier if kind == 'sinv' else bill.transporter
    if not payee.tds_section or not payee.tds_rate:
        raise ValidationError(f'{payee.name} has no TDS section / rate — set it on the partner after your tax advisor confirms.')
    if deducted(bill):
        raise ValidationError('TDS is already deducted on this bill.')
    item = next((i for i in open_items(payee) if i['obj'].pk == bill.pk and i['kind'] == kind), None)
    if not item or item['amount'] <= 0:
        raise ValidationError('The bill is not open for payment.')
    base = bill.billed_taxable if kind == 'sinv' else bill.payable
    amount = min((base * payee.tds_rate / 100).quantize(Decimal('1')), item['amount'])
    if amount <= 0:
        raise ValidationError('Nothing to deduct.')
    on = on or timezone.localdate()
    d = TdsDeduction.objects.create(number=next_number('TDS', None, on), payee=payee, section=payee.tds_section, rate=payee.tds_rate, base=base, amount=amount, date=on,
                                    note=(note or '')[:200], created_by=user, **({'supplier_invoice': bill} if kind == 'sinv' else {'freight_bill': bill}))
    payable = gl.acct('ap' if kind == 'sinv' else 'ap_freight')
    gl.post(user, f'tds:{d.pk}', on, getattr(bill, 'site', None), f'TDS {d.section} @ {payee.tds_rate}% on {item["ref"]} · {payee.name}',
            [(payable, amount, ZERO, payee, item['ref']), (gl.acct('tds_payable'), ZERO, amount, payee, d.section)])
    AuditEvent.objects.create(actor=user, action='TDS deducted', reference=d.number, detail=f'{payee.name} · {item["ref"]} · {d.section} ₹{amount}')
    return d


@transaction.atomic
def reverse(user, pk, reason):
    _need(user, 'add_tdsdeduction')
    from . import gl
    d = TdsDeduction.objects.select_for_update().get(pk=pk)
    if d.status != 'posted':
        raise ValidationError('Already reversed.')
    if d.challan_id:
        raise ValidationError('This TDS is already deposited by challan — it cannot be reversed here.')
    if not (reason or '').strip():
        raise ValidationError('Give the reason.')
    bill = d.supplier_invoice or d.freight_bill
    from .models import PaymentAllocation
    paid = PaymentAllocation.objects.filter(**({'supplier_invoice': bill} if d.supplier_invoice_id else {'freight_bill': bill})).exclude(payment__status__in=['cancelled', 'failed']).exists()
    if paid:
        raise ValidationError('A payment already uses this bill — cancel it first.')
    today = timezone.localdate()
    gl.reverse(user, gl.JournalEntry.objects.get(source=f'tds:{d.pk}'), today, reason)
    d.status, d.reversed_on, d.note = 'reversed', today, reason.strip()[:200]
    d.save()
    AuditEvent.objects.create(actor=user, action='TDS reversed', reference=d.number, detail=reason[:200])
    return d


@transaction.atomic
def deposit(user, section, month, bank, challan_number, paid_on, bsr=''):
    _need(user, 'add_tdschallan')
    from . import gl
    month = month.replace(day=1)
    nxt = date(month.year + (month.month // 12), month.month % 12 + 1, 1)
    rows = TdsDeduction.objects.select_for_update().filter(section=section, status='posted', challan__isnull=True, date__gte=month, date__lt=nxt)
    total = rows.aggregate(t=Sum('amount'))['t'] or ZERO
    if not total:
        raise ValidationError('No undeposited TDS for that month and section.')
    if not (challan_number or '').strip():
        raise ValidationError('Enter the challan / CIN number.')
    if not bank or not bank.is_bank or not bank.active:
        raise ValidationError('Choose the bank account.')
    c = TdsChallan.objects.create(number=next_number('TCH', None, paid_on), section=section, month=month, amount=total, bank=bank, challan_number=challan_number.strip()[:40],
                                  bsr_code=(bsr or '')[:10], paid_on=paid_on, created_by=user)
    rows.update(challan=c)
    gl.post(user, f'tdsch:{c.pk}', paid_on, None, f'TDS {section} deposited for {month:%b %Y} · {c.challan_number}',
            [(gl.acct('tds_payable'), total, ZERO, None, c.challan_number), (bank, ZERO, total, None, c.challan_number)])
    AuditEvent.objects.create(actor=user, action='TDS deposited', reference=c.number, detail=f'{section} {month:%b %Y} ₹{total} {c.challan_number}')
    return c


def register(month=None):
    qs = TdsDeduction.objects.select_related('payee', 'supplier_invoice', 'freight_bill', 'challan')
    if month:
        nxt = date(month.year + (month.month // 12), month.month % 12 + 1, 1)
        qs = qs.filter(date__gte=month, date__lt=nxt)
    return qs
