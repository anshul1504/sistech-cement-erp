"""Historical controls, independent month-end review and year-end carry-forward.

Effective dates, not present-day allocation status, determine cutoff balances.
Close reviews retain evidence and a fingerprint of the books they reviewed.
"""
from accounts.delegation import authorize, can_approve
import calendar
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import gl
from .finance_access import permitted, require_company_finance
from .gst import fy_start_month
from .models import (Account, AuditEvent, BankStatementLine, CreditNote, DebitNote, FinancialPeriod,
                     FinancialYearClose, FinancialYearCloseHistory, FixedAsset, DepreciationEntry, FreightBill, FreightRecoveryAdjustment, Invoice, JournalEntry, JournalLine,
                     PaymentAllocation, PeriodBankConfirmation, PeriodCloseReview, Receipt,
                     StockMovement, SupplierInvoice, SupplierPayment)

ZERO = Decimal('0')
CENT = Decimal('.01')


def _sum(qs, field):
    return qs.aggregate(total=Sum(field))['total'] or ZERO


def historical_checks(as_of):
    """Reconstruct balances from source documents, including later returns/bounces."""
    receipts = Receipt.objects.filter(date__lte=as_of).filter(Q(status='posted') | Q(status='bounced', bounced_on__gt=as_of))
    ar = _sum(Invoice.objects.filter(date__lte=as_of), 'grand_total') - _sum(receipts, 'amount') - _sum(CreditNote.objects.filter(date__lte=as_of), 'total')
    movements = StockMovement.objects.filter(created_at__date__lte=as_of, kind__in=gl.STOCK_MAP)
    inventory = sum((m.value for m in movements), ZERO)
    payments = SupplierPayment.objects.filter(date__lte=as_of).filter(Q(status='paid') | Q(status='failed', returned_on__gt=as_of))
    paid = PaymentAllocation.objects.filter(payment__in=payments)
    ap = (_sum(SupplierInvoice.objects.filter(invoice_date__lte=as_of, status__in=['matched', 'approved']), 'billed_total')
          + _sum(FixedAsset.objects.filter(credit_purchase=True, put_to_use__lte=as_of), 'cost')
          - _sum(DebitNote.objects.filter(date__lte=as_of), 'total')
          - _sum(paid.filter(freight_bill__isnull=True), 'amount'))
    freight = (_sum(FreightBill.objects.filter(status='approved', bill_date__lte=as_of), 'payable')
               - _sum(FreightRecoveryAdjustment.objects.filter(status='approved',on__lte=as_of), 'amount')
               - _sum(paid.filter(freight_bill__isnull=False), 'amount'))
    rows = []
    for name, keys, expected, sign in [
        ('Trade receivables', ['ar'], ar, 1),
        ('Inventory', ['inventory_rm', 'inventory_stores', 'inventory_fg'], inventory, 1),
        ('Supplier payables', ['ap'], ap, -1),
        ('Transporter payables', ['ap_freight'], freight, -1),
    ]:
        lines = gl._posted(end=as_of).filter(account__system_key__in=keys)
        actual = (_sum(lines, 'debit') - _sum(lines, 'credit')) * sign
        rows.append(dict(name=name, gl=actual, sub=expected, diff=actual - expected))
    return rows


def source_gaps(as_of):
    """Documents requiring a posting but lacking its source-keyed journal."""
    posted = set(JournalEntry.objects.filter(status='posted', date__lte=as_of).values_list('source', flat=True))
    expected = []
    for model, prefix, query in [
        (Invoice, 'inv', dict(date__lte=as_of, grand_total__gt=0)),
        (CreditNote, 'cn', dict(date__lte=as_of, total__gt=0)),
        (Receipt, 'rct', dict(date__lte=as_of, amount__gt=0)),
        (SupplierInvoice, 'sinv', dict(invoice_date__lte=as_of, status__in=['matched', 'approved'], billed_total__gt=0)),
        (DebitNote, 'dn', dict(date__lte=as_of, total__gt=0)),
        (FreightBill, 'frb', dict(bill_date__lte=as_of, status='approved', payable__gt=0)),
        (FixedAsset, 'fa', dict(put_to_use__lte=as_of)),
    ]:
        expected.extend((f'{prefix}:{pk}', label) for pk, label in model.objects.filter(**query).values_list('pk', 'code' if model is FixedAsset else 'bill_number' if model is FreightBill else 'number'))
    expected.extend((f'freight-recovery:{pk}', f'Recovery {pk} / {reference}') for pk,reference in
        FreightRecoveryAdjustment.objects.filter(status='approved',on__lte=as_of).exclude(amount=0).values_list('pk','bill__bill_number'))
    for movement in StockMovement.objects.filter(created_at__date__lte=as_of, kind__in=gl.STOCK_MAP).exclude(unit_cost=None):
        if movement.value:
            expected.append((f'stk:{movement.pk}', movement.source))
    for asset in FixedAsset.objects.filter(status='disposed', disposed_on__lte=as_of):
        expected.append((f'fad:{asset.pk}', asset.code))
    for depreciation in DepreciationEntry.objects.filter(month__lte=as_of).select_related('asset'):
        end = depreciation.month.replace(day=calendar.monthrange(depreciation.month.year, depreciation.month.month)[1])
        if end <= as_of:
            expected.append((f'dep:{depreciation.asset_id}:{depreciation.month:%Y%m}', depreciation.asset.code))
    missing = [dict(source=source, reference=label) for source, label in expected if source not in posted]
    for receipt in Receipt.objects.filter(status='bounced', bounced_on__lte=as_of):
        original = JournalEntry.objects.filter(source=f'rct:{receipt.pk}').first()
        if not original or not JournalEntry.objects.filter(reversal_of=original, status='posted', date__lte=as_of).exists():
            missing.append(dict(source=f'rct-return:{receipt.pk}', reference=receipt.number))
    for payment in SupplierPayment.objects.filter(status__in=['paid', 'failed'], date__lte=as_of):
        from .treasury import offset_totals
        needs_journal=payment.mode!='offset' or any(offset_totals(payment))
        if not needs_journal:
            if payment.status=='failed' and not payment.returned_on:missing.append(dict(source=f'pay-return-date:{payment.pk}',reference=payment.number))
            continue
        original = JournalEntry.objects.filter(source=f'pay:{payment.pk}', status='posted', date__lte=as_of).first()
        if not original:
            missing.append(dict(source=f'pay:{payment.pk}', reference=payment.number))
        if payment.status == 'failed' and not payment.returned_on:
            missing.append(dict(source=f'pay-return-date:{payment.pk}', reference=payment.number))
        if payment.status == 'failed' and payment.returned_on and payment.returned_on <= as_of and (not original or not JournalEntry.objects.filter(reversal_of=original, date__lte=as_of, status='posted').exists()):
            missing.append(dict(source=f'pay-return:{payment.pk}', reference=payment.number))
    return missing


def _fingerprint(as_of):
    # Include attribution and amounts, not just totals; equal-and-opposite changes invalidate a review too.
    digest = hashlib.sha256()
    rows = JournalLine.objects.filter(entry__status='posted', entry__date__lte=as_of).order_by('pk').values_list(
        'pk', 'entry_id', 'entry__date', 'entry__site_id', 'account_id', 'partner_id', 'debit', 'credit')
    for row in rows.iterator(chunk_size=2000):
        digest.update(json.dumps(row, default=str, separators=(',', ':')).encode())
        digest.update(b'\n')
    return digest.hexdigest()



def checklist(period):
    """A current view of books effective through this period's end."""
    from .treasury import reconciliation
    end = period.end
    checks, gaps = historical_checks(end), source_gaps(end)
    blockers = []
    if end >= timezone.localdate():
        blockers.append('The month must finish before it can be closed.')
    if JournalEntry.objects.filter(date__lte=end, status='draft').exists():
        blockers.append('Approve or reject outstanding draft journal vouchers through this month.')
    if any(c['diff'] for c in checks):
        blockers.append('Reconcile historical control-account differences.')
    if gaps:
        blockers.append(f'{len(gaps)} source document(s) are missing ledger postings.')
    if SupplierInvoice.objects.filter(invoice_date__lte=end, status='exception').exists():
        blockers.append('Resolve supplier invoice exceptions through this month.')
    confirmations = {c.bank_id: c for c in period.bank_confirmations.select_related('bank', 'prepared_by')}
    banks = []
    for bank in Account.objects.filter(is_bank=True).exclude(system_key='cash'):
        rec = reconciliation(bank, end)
        if not rec['book'] and not bank.lines.filter(entry__date__lte=end, entry__status='posted').exists() and not bank.statement_lines.filter(date__lte=end).exists():
            continue
        evidence = confirmations.get(bank.pk)
        expected = rec['book'] - rec['adj']
        difference = evidence.closing_balance - expected if evidence else None
        unmatched = rec['unmatched'].count()
        if not evidence:
            blockers.append(f'{bank.code}: enter the bank statement closing balance and reference.')
        elif difference:
            blockers.append(f'{bank.code}: the confirmed bank balance does not reconcile.')
        if unmatched:
            blockers.append(f'{bank.code}: match outstanding bank statement rows.')
        banks.append(dict(bank=bank, confirmation=evidence, book=rec['book'], outstanding=rec['adj'], expected=expected, difference=difference, unmatched=unmatched))
    snapshot = dict(as_of=str(end), fingerprint=_fingerprint(end), controls=[{k: str(v) for k, v in c.items()} for c in checks],
                    banks=[dict(bank=b['bank'].pk, confirmation=b['confirmation'].pk if b['confirmation'] else None,
                                balance=str(b['confirmation'].closing_balance) if b['confirmation'] else None,
                                reference=b['confirmation'].statement_reference if b['confirmation'] else '',
                                expected=str(b['expected']), unmatched=b['unmatched']) for b in banks],
                    statement_matches=[list(row) for row in BankStatementLine.objects.filter(date__lte=end).order_by('pk').values_list('pk', 'matched_line_id')],
                    gaps=gaps, blockers=blockers)
    return dict(checks=checks, gaps=gaps, blockers=blockers, banks=banks, snapshot=snapshot)


@transaction.atomic
def confirm_bank(user, period_pk, bank, balance, reference):
    gl.lock_finance()
    permitted(user, 'prepare_periodclose')
    period = FinancialPeriod.objects.select_for_update().get(pk=period_pk)
    if period.status != 'open':
        raise ValidationError('Reopen the month before changing its evidence.')
    if not bank.is_bank or bank.system_key == 'cash':
        raise ValidationError('Choose a bank account.')
    try:
        balance = Decimal(balance)
        if not balance.is_finite():
            raise ValueError
        balance = balance.quantize(CENT)
    except (ValueError, ArithmeticError):
        raise ValidationError('Enter a finite bank balance.')
    if not (reference or '').strip():
        raise ValidationError('Record the bank statement reference covering the month end.')
    candidate = PeriodBankConfirmation(period=period, bank=bank, closing_balance=balance, statement_reference=reference.strip()[:200], prepared_by=user)
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    confirmation, _ = PeriodBankConfirmation.objects.update_or_create(period=period, bank=bank, defaults=dict(
        closing_balance=balance, statement_reference=reference.strip()[:200], prepared_by=user))
    AuditEvent.objects.create(actor=user, action='Close bank balance confirmed', reference=str(period), detail=f'{bank.code} · {balance} · {reference}'[:250])
    return confirmation


@transaction.atomic
def prepare_period(user, on, note, *, tax_reviewed=False, stock_reviewed=False, bank_reviewed=False):
    gl.lock_finance()
    permitted(user, 'prepare_periodclose')
    period = gl.period_for(on)
    period = FinancialPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status != 'open':
        raise ValidationError('This month is already closed.')
    if not all((tax_reviewed, stock_reviewed, bank_reviewed)) or not (note or '').strip():
        raise ValidationError('Confirm tax, stock and bank review and record the review evidence.')
    result = checklist(period)
    if result['blockers']:
        raise ValidationError(result['blockers'])
    review = PeriodCloseReview.objects.create(period=period, prepared_by=user, note=note.strip(),
        tax_reviewed=True, stock_reviewed=True, bank_reviewed=True, snapshot=result['snapshot'])
    AuditEvent.objects.create(actor=user, action='Period close prepared', reference=str(period), detail=note[:250])
    return review


@transaction.atomic
def decide_period(user, start, close, reason=''):
    gl.lock_finance()
    require_company_finance(user)
    if not can_approve(user, 'close_period'):
        raise PermissionDenied
    period = gl.period_for(start)
    period = FinancialPeriod.objects.select_for_update().get(pk=period.pk)
    if close:
        if period.status == 'closed':
            raise ValidationError('The month is already closed.')
        review = PeriodCloseReview.objects.select_for_update().filter(period=period).order_by('-prepared_at', '-pk').first()
        if not review or review.approved_at:
            raise ValidationError('An accountant must prepare a fresh close review first.')
        authorize(user, 'close_period', makers=(review.prepared_by_id,), reference=str(period))
        if review.prepared_by_id == user.pk:
            raise ValidationError('The review maker cannot approve the month close (maker-checker).')
        result = checklist(period)
        if result['blockers']:
            raise ValidationError(result['blockers'])
        if result['snapshot'] != review.snapshot:
            raise ValidationError('The books or bank evidence changed after review. Prepare a fresh review.')
        review.approved_by, review.approved_at = user, timezone.now()
        review.save(update_fields=['approved_by', 'approved_at'])
        period.status, period.closed_by, period.closed_at = 'closed', user, timezone.now()
    else:
        authorize(user, 'close_period', reference=str(period))
        if period.status != 'closed':
            raise ValidationError('Only a closed month can be reopened.')
        if FinancialYearClose.objects.filter(start__lte=period.start, end__gte=period.end, status__in=['closed', 'reopen_requested']).exists():
            raise ValidationError('Approve year-end reopening before reopening a month in that year.')
        if FinancialPeriod.objects.filter(status='closed', start__gt=period.start).exists():
            raise ValidationError('Reopen later closed months first to preserve historical balances.')
        if not (reason or '').strip():
            raise ValidationError('Record why the month must be reopened.')
        period.status, period.closed_by, period.closed_at = 'open', None, None
    period.save()
    AuditEvent.objects.create(actor=user, action='Period closed' if close else 'Period reopened', reference=str(period), detail=(reason or f'Independent review approved by {user.get_username()}')[:250])
    return period


def _year_history(row, user, action, note):
    FinancialYearCloseHistory.objects.create(year_close=row, revision=row.revision, action=action, actor=user,
        note=note, snapshot=dict(review=row.snapshot, start=str(row.start), end=str(row.end), status=row.status,
                                 prepared_by=row.prepared_by_id, prepared_at=str(row.prepared_at)))


def year_bounds(on):
    month = fy_start_month()
    year = on.year if on.month >= month else on.year - 1
    start = date(year, month, 1)
    return start, date(year + 1, month, 1) - timedelta(days=1)


def year_preview(start, end):
    balances = gl._posted(start, end).exclude(entry__kind='yearend').filter(account__type__in=['income', 'expense']).values('entry__site_id', 'account_id').annotate(d=Sum('debit'), c=Sum('credit'))
    groups = defaultdict(list)
    for row in balances:
        net = row['d'] - row['c']
        if net:
            groups[row['entry__site_id']].append((row['account_id'], net))
    return dict(groups), -sum((net for rows in groups.values() for _, net in rows), ZERO)


def _year_snapshot(start, end):
    periods = list(FinancialPeriod.objects.filter(start__gte=start, end__lte=end).order_by('start').values_list('start', 'status'))
    return dict(start=str(start), end=str(end), fingerprint=_fingerprint(end), periods=[[str(day), status] for day, status in periods])


def _check_year(start, end):
    if end >= timezone.localdate():
        raise ValidationError('The financial year must finish before carry-forward.')
    months = []
    cursor = start
    while cursor <= end:
        months.append(gl.period_for(cursor))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    locked = list(FinancialPeriod.objects.select_for_update().filter(pk__in=[p.pk for p in months]).order_by('start'))
    if len(locked) != 12 or any(p.status != 'closed' for p in locked):
        raise ValidationError('All twelve months must be independently reviewed and closed before year-end.')
    if any(c['diff'] for c in historical_checks(end)) or source_gaps(end):
        raise ValidationError('Reconcile the year-end source documents and control accounts first.')
    return _year_snapshot(start, end)


@transaction.atomic
def prepare_year(user, on, note):
    gl.lock_finance()
    permitted(user, 'prepare_yearclose')
    if not (note or '').strip():
        raise ValidationError('Record the year-end review evidence.')
    start, end = year_bounds(on)
    row, _ = FinancialYearClose.objects.get_or_create(start=start, defaults=dict(end=end, prepared_by=user, note=note.strip()))
    row = FinancialYearClose.objects.select_for_update().get(pk=row.pk)
    if row.status in ('closed', 'reopen_requested'):
        raise ValidationError('The year is already closed or awaiting reopening approval.')
    if FinancialYearClose.objects.exclude(pk=row.pk).filter(start__gt=start, status__in=['closed', 'reopen_requested']).exists():
        raise ValidationError('Close earlier financial years before later ones.')
    if FinancialYearClose.objects.exclude(pk=row.pk).filter(start__lte=end, end__gte=start).exists():
        raise ValidationError('This financial year overlaps an existing close. Review the financial-year configuration.')
    snapshot = _check_year(start, end)
    if row.status in ('reopened', 'rejected'):
        row.revision += 1
    row.status, row.prepared_by, row.note, row.snapshot = 'proposed', user, note.strip(), snapshot
    row.decided_by, row.decided_at = None, None
    row.save()
    _year_history(row, user, 'Prepared', note)
    AuditEvent.objects.create(actor=user, action='Year close prepared', reference=str(row), detail=note[:250])
    return row


@transaction.atomic
def decide_year(user, pk, approve, reason=''):
    gl.lock_finance()
    require_company_finance(user)
    row = FinancialYearClose.objects.select_for_update().get(pk=pk)
    authorize(user, 'approve_yearclose', makers=(row.prepared_by_id,), reference=str(row))
    if row.status != 'proposed':
        raise ValidationError('Only a proposed year close can be decided.')
    if row.prepared_by_id == user.pk:
        raise ValidationError('The year-close maker cannot approve it (maker-checker).')
    if approve:
        snapshot = _check_year(row.start, row.end)
        if snapshot != row.snapshot:
            raise ValidationError('Year-end books changed after review. Prepare a fresh proposal.')
        from .models import Site
        groups, _ = year_preview(row.start, row.end)
        for site_id, balances in groups.items():
            lines = [(Account.objects.get(pk=aid), -net if net < 0 else ZERO, net if net > 0 else ZERO, None, 'Year-end transfer') for aid, net in balances]
            profit = -sum((net for _, net in balances), ZERO)
            lines.append((gl.acct('retained'), -profit if profit < 0 else ZERO, profit if profit > 0 else ZERO, None, 'Profit / loss carried forward'))
            gl.post(user, f'yearclose:{row.pk}:{row.revision}:{site_id or 0}', row.end, Site.objects.get(pk=site_id) if site_id else None,
                    f'Year-end carry-forward {row}', lines, kind='yearend', year_close=row)
        row.status = 'closed'
    else:
        if not (reason or '').strip():
            raise ValidationError('Record why the year close is rejected.')
        row.status = 'rejected'
    row.decided_by, row.decided_at = user, timezone.now()
    row.save()
    _year_history(row, user, 'Closed' if approve else 'Rejected', reason or row.note)
    AuditEvent.objects.create(actor=user, action='Year closed' if approve else 'Year close rejected', reference=str(row), detail=(reason or row.note)[:250])
    return row


@transaction.atomic
def request_year_reopen(user, pk, reason):
    gl.lock_finance()
    permitted(user, 'prepare_yearclose')
    row = FinancialYearClose.objects.select_for_update().get(pk=pk)
    if row.status != 'closed' or not (reason or '').strip():
        raise ValidationError('Choose a closed year and record the reason for reopening.')
    if FinancialYearClose.objects.filter(start__gt=row.start, status__in=['closed', 'reopen_requested']).exists():
        raise ValidationError('Reopen later closed years first.')
    row.status, row.reopen_requested_by, row.reopen_reason = 'reopen_requested', user, reason.strip()
    row.save()
    _year_history(row, user, 'Reopen requested', reason)
    AuditEvent.objects.create(actor=user, action='Year reopen requested', reference=str(row), detail=reason[:250])
    return row


@transaction.atomic
def approve_year_reopen(user, pk, approve, reason=''):
    gl.lock_finance()
    require_company_finance(user)
    row = FinancialYearClose.objects.select_for_update().get(pk=pk)
    authorize(user, 'approve_yearclose', makers=(row.prepared_by_id, row.reopen_requested_by_id), reference=str(row))
    if row.status != 'reopen_requested':
        raise ValidationError('There is no pending reopening request.')
    if row.reopen_requested_by_id == user.pk:
        raise ValidationError('The reopening maker cannot approve it (maker-checker).')
    if approve:
        if FinancialYearClose.objects.filter(start__gt=row.start, status__in=['closed', 'reopen_requested']).exists():
            raise ValidationError('Reopen later closed years first.')
        for entry in row.entries.filter(reversal_of__isnull=True, reversed_by__isnull=True).prefetch_related('lines__account'):
            gl.post(user, f'yearreopen:{entry.pk}', row.end, entry.site, f'Year reopened: {row.reopen_reason}'[:250],
                    [(line.account, line.credit, line.debit, line.partner, line.narration, line.cost_centre) for line in entry.lines.all()],
                    kind='yearend', year_close=row, reversal_of=entry)
        row.status = 'reopened'
    else:
        if not (reason or '').strip():
            raise ValidationError('Record why the reopening request is rejected.')
        row.status = 'closed'
    row.decided_by, row.decided_at = user, timezone.now()
    row.save()
    _year_history(row, user, 'Reopened' if approve else 'Reopen rejected', reason or row.reopen_reason)
    AuditEvent.objects.create(actor=user, action='Year reopened' if approve else 'Year reopen rejected', reference=str(row), detail=(reason or row.reopen_reason)[:250])
    return row
