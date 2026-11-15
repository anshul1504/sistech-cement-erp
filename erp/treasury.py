"""Treasury and statutory: supplier payments (FR-FIN-007), bank reconciliation (FR-FIN-008), GST summary (FR-FIN-011),
fixed assets and depreciation (FR-FIN-012), cash flow (FR-FIN-011).

Controls:
- payments are proposed against approved bills only, never above what is outstanding; a different user approves; release
  needs the UTR / cheque number; a failed payment reverses its voucher and frees the bills again;
- the payee's bank account must be on file (verified KYC is a master-data control) for NEFT / RTGS;
- statement rows are imported once (duplicate rows ignored); a ledger line can be matched to one statement row only;
- depreciation posts once per asset per month; disposal books gain or loss against book value.
"""
from accounts.delegation import authorize, can_approve
import calendar
import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction, IntegrityError
from django.db.models import Sum, Q
from django.utils import timezone

from .finance_access import require_company_finance
from . import gl
from .gst import next_number, inr, qtxt
from .models import (SupplierPayment, PaymentAllocation, SupplierInvoice, FreightBill, DebitNote, BankStatementLine, JournalLine, Account, FixedAsset,
                     DepreciationEntry, Invoice, InvoiceLine, CreditNote, AuditEvent, Partner, PAYMENT_MODES, ASSET_CATEGORIES)

ZERO = Decimal('0')
CENT = Decimal('0.01')
LIVE = ('proposed', 'approved', 'paid')


def _need(user, perm):
    from accounts.security import access_allowed
    if not access_allowed(user):raise PermissionDenied
    require_company_finance(user)
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=reference, detail=detail[:250])


# ---------- Payables open items ----------

def allocated(field, obj):
    return PaymentAllocation.objects.filter(**{field: obj}, payment__status__in=LIVE).aggregate(t=Sum('amount'))['t'] or ZERO


def open_items(payee):
    """Bills of this payee still to pay, oldest due first, plus unused standalone debit notes (to adjust)."""
    items = []
    for inv in SupplierInvoice.objects.filter(supplier=payee, status__in=['matched', 'approved']).order_by('due_date', 'pk'):
        due = inv.payable - allocated('supplier_invoice', inv)
        if due > 0:
            items.append({'kind': 'sinv', 'obj': inv, 'ref': inv.invoice_number, 'date': inv.invoice_date, 'due_date': inv.due_date, 'amount': due})
    for fb in FreightBill.objects.filter(transporter=payee, status='approved').order_by('bill_date', 'pk'):
        from .freight_recovery import effective_payable,clear_for_payment
        due = effective_payable(fb) - allocated('freight_bill', fb)
        if due and (due<0 or clear_for_payment(fb)):
            items.append({'kind': 'frb', 'obj': fb, 'ref': fb.bill_number, 'date': fb.bill_date, 'due_date': fb.bill_date, 'amount': due})
    for asset in FixedAsset.objects.filter(supplier=payee, credit_purchase=True).order_by('put_to_use', 'pk'):
        due = asset.cost - allocated('fixed_asset', asset)
        if due > 0:
            items.append({'kind': 'asset', 'obj': asset, 'ref': asset.code, 'date': asset.put_to_use, 'due_date': asset.put_to_use, 'amount': due})
    for dn in DebitNote.objects.filter(supplier=payee).exclude(supplier_invoice__decision='matched').order_by('date'):
        left = dn.total + allocated('debit_note', dn)  # allocations are negative
        if left > 0:
            items.append({'kind': 'dn', 'obj': dn, 'ref': dn.number, 'date': dn.date, 'due_date': dn.date, 'amount': -left})
    return items


def payees():
    """Suppliers / transporters with something to pay."""
    ids = set(SupplierInvoice.objects.filter(status__in=['matched', 'approved']).values_list('supplier', flat=True)) | \
        set(FreightBill.objects.filter(status='approved').values_list('transporter', flat=True)) | \
        set(FixedAsset.objects.filter(credit_purchase=True).values_list('supplier', flat=True)) | set(DebitNote.objects.values_list('supplier',flat=True))
    out = []
    today = timezone.localdate()
    for p in Partner.objects.filter(pk__in=ids).order_by('name'):
        items = open_items(p)
        total = sum((i['amount'] for i in items), ZERO)
        if items:
            out.append({'payee': p, 'total': total, 'overdue': sum((i['amount'] for i in items if i['amount'] > 0 and i['due_date'] and i['due_date'] < today), ZERO),
                        'count': len([i for i in items if i['amount'] > 0])})
    return out


@transaction.atomic
def propose(user, payee, bank, picks, mode='neft', on=None, note=''):
    """picks: {('sinv'|'frb'|'dn', pk): amount}. Credit selections are positive here and stored negative."""
    _need(user, 'add_supplierpayment')
    # Lock the stable payee row before calculating remaining allocations.
    payee = Partner.objects.select_for_update().get(pk=payee.pk)
    problems = []
    if mode not in dict(PAYMENT_MODES):
        problems.append('Choose a valid bank payment mode.')
    if on and on > timezone.localdate():
        problems.append('Payment date cannot be in the future.')
    if mode=='offset':
        if bank is not None:problems.append('A non-cash offset must not select a bank account.')
        if not (note or '').strip() or len(note.strip())>200:problems.append('Record a non-cash offset reason of up to 200 characters.')
    elif not bank or not bank.is_bank or not bank.active or bank.system_key == 'cash':
        problems.append('Choose the bank account to pay from.')
    if mode=='refund' and (not (note or '').strip() or len(note.strip())>200):problems.append('Record a vendor refund reason of up to 200 characters.')
    if mode in ('neft', 'rtgs') and not (payee.bank_account_number and payee.bank_ifsc):
        problems.append(f'{payee.name} has no bank account / IFSC on file — update the partner master (verified) first.')
    items = {(i['kind'], i['obj'].pk): i for i in open_items(payee)}
    allocs = []
    for key, amt in picks.items():
        try:
            amt = Decimal(amt or 0)
            if not amt.is_finite():
                raise InvalidOperation
            if mode in ['offset','refund'] and amt!=amt.quantize(CENT):raise ValidationError('Credit amounts must use exact paise, with at most two decimal places.')
            amt = amt.quantize(CENT)
            PaymentAllocation._meta.get_field('amount').clean(amt,None)
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError('Payment amounts must be finite numbers.')
        if not amt:
            continue
        item = items.get(key)
        if not item:
            raise ValidationError('A selected bill is not open for this payee.')
        limit = abs(item['amount'])
        if amt < 0 or amt > limit:
            problems.append(f'{item["ref"]}: pay between ₹0 and ₹{inr(limit, 2)}.')
        allocs.append((item, -amt if item['amount']<0 else amt))
    total = sum((a for _, a in allocs), ZERO)
    SupplierPayment._meta.get_field('amount').clean(total,None)
    if total > max(sum((item['amount'] for item in items.values()), ZERO), ZERO):
        problems.append('Payment exceeds the net open balance; apply available debit notes or reduce the payment.')
    if not allocs:
        problems.append('Select at least one bill.')
    elif mode=='offset':
        if total!=0 or not any(a>0 for _,a in allocs) or not any(a<0 for _,a in allocs):problems.append('A non-cash offset requires equal positive bills and negative credits; the net must be exactly zero.')
    elif mode=='refund':
        if total>=0 or any(a>=0 for _,a in allocs):problems.append('A vendor refund selects recognized credits only, with a strictly negative signed total.')
    elif total <= 0:
        problems.append('Debit notes cannot exceed the bills being paid.')
    if mode == 'cash':
        problems.append('Supplier payments are made through the bank.')
    if problems:
        raise ValidationError(problems)
    pay = SupplierPayment.objects.create(number=next_number('PAY', None, on), payee=payee, bank=bank, date=on or timezone.localdate(), mode=mode, amount=total,
                                         note=(note or '')[:200], proposal_note=(note or '').strip()[:200], created_by=user, beneficiary_name=payee.name,
                                         beneficiary_account=payee.bank_account_number, beneficiary_ifsc=payee.bank_ifsc)
    for item, amt in allocs:
        PaymentAllocation.objects.create(payment=pay, amount=amt, **{{'sinv': 'supplier_invoice', 'frb': 'freight_bill', 'dn': 'debit_note', 'asset': 'fixed_asset'}[item['kind']]: item['obj']})
    if mode in ['offset','refund']:check_offset(pay)
    _log(user, 'Payment proposed', pay.number, f'{pay.date} · {payee.name} · ₹{inr(total, 2)} · {len(allocs)} item(s)')
    return pay


@transaction.atomic
def decide(user, pk, approve, note=''):
    require_company_finance(user)
    pay = SupplierPayment.objects.select_for_update().select_related('payee').get(pk=pk)
    authorize(user, 'approve_supplierpayment', makers=(pay.created_by_id,), reference=pay.number)
    if pay.status != 'proposed' and not (not approve and pay.status == 'approved'):
        raise ValidationError('Only proposed payments can be approved; proposed or approved payments can be cancelled.')
    if approve:
        Partner.objects.select_for_update(no_key=True).get(pk=pay.payee_id)
        from .freight_recovery import check_payment
        check_payment(pay)
        if pay.mode in ['offset','refund']:check_offset(pay)
    if pay.created_by_id == user.pk:
        raise ValidationError('The person who proposed the payment cannot approve it (maker-checker).')
    if not approve and not (note or '').strip():
        raise ValidationError('Give the reason for cancelling.')
    pay.status, pay.approved_by = ('approved' if approve else 'cancelled'), user
    if note: pay.note = note.strip()[:200]
    if approve:pay.approval_note=(note or '').strip()[:200]
    pay.save()
    _log(user, 'Payment approved' if approve else 'Payment cancelled', pay.number, f'{pay.payee.name} · ₹{inr(pay.amount, 2)}')
    return pay


@transaction.atomic
def release(user, pk, reference, on=None):
    require_company_finance(user)
    pay = SupplierPayment.objects.select_for_update(of=('self',)).select_related('payee', 'bank').get(pk=pk)
    if pay.mode=='refund':raise ValidationError('Use the explicit incoming refund receipt action; no outgoing bank release applies.')
    if pay.mode=='offset':raise ValidationError('Use the explicit non-cash settlement action; no bank release or UTR applies.')
    authorize(user, 'release_supplierpayment', makers=(pay.created_by_id,), reference=pay.number)
    if pay.status != 'approved':
        raise ValidationError('Only approved payments can be released.')
    if pay.created_by_id == user.pk:
        raise ValidationError('The payment maker cannot release it (maker-checker).')
    Partner.objects.select_for_update(no_key=True).get(pk=pay.payee_id)
    from .freight_recovery import check_payment
    check_payment(pay)
    if on and on > timezone.localdate():
        raise ValidationError('Payment date cannot be in the future.')
    if not pay.bank.active:
        raise ValidationError('The payment bank account is inactive.')
    if (pay.beneficiary_name, pay.beneficiary_account, pay.beneficiary_ifsc) != (pay.payee.name, pay.payee.bank_account_number, pay.payee.bank_ifsc):
        raise ValidationError('Beneficiary details changed after proposal. Cancel and propose a fresh payment for approval.')
    reference = (reference or '').strip()
    if not reference:
        raise ValidationError('Enter the UTR / cheque number from the bank.')
    pay.status, pay.reference, pay.paid_by, pay.paid_at = 'paid', reference[:40], user, timezone.now()
    if on: pay.date = on
    pay.save()
    ap = sum((a.amount for a in pay.allocations.all() if a.freight_bill_id is None), ZERO)
    apf = sum((a.amount for a in pay.allocations.all() if a.freight_bill_id), ZERO)
    lines = [(pay.bank, 0, pay.amount, None, reference)]
    if ap: lines.append((gl.acct('ap'), max(ap,ZERO), max(-ap,ZERO), pay.payee, pay.number))
    if apf: lines.append((gl.acct('ap_freight'), max(apf,ZERO), max(-apf,ZERO), pay.payee, pay.number))
    gl.post(user, f'pay:{pay.pk}', pay.date, None, f'Payment {pay.number} · {pay.payee.name} · {pay.get_mode_display()} {reference}', lines)
    _log(user, 'Payment released', pay.number, f'UTR {reference} · ₹{inr(pay.amount, 2)}')
    return pay


@transaction.atomic
def fail(user, pk, reason):
    require_company_finance(user)
    pay = SupplierPayment.objects.select_for_update().get(pk=pk)
    authorize(user, 'release_supplierpayment', makers=(pay.created_by_id,), reference=pay.number)
    if pay.mode=='refund':raise ValidationError('Use the explicit refund reversal action.')
    if pay.mode=='offset':raise ValidationError('Use the explicit settlement reversal action; this is not a returned bank payment.')
    if pay.status != 'paid':
        raise ValidationError('Only released payments can be marked failed.')
    if not (reason or '').strip():
        raise ValidationError('Give the bank\'s return reason.')
    Partner.objects.select_for_update(no_key=True).get(pk=pay.payee_id)
    gl.reverse_source(user, f'pay:{pay.pk}', reason=f'Payment returned: {reason}')
    pay.status, pay.note, pay.returned_on = 'failed', reason.strip()[:200], timezone.localdate()
    pay.reversal_reason=reason.strip()[:200];pay.returned_by=user;pay.returned_at=timezone.now()
    pay.save()
    _log(user, 'Payment failed', pay.number, reason)
    return pay


def check_offset(pay):
    """Payee mutex is held. Recheck the reserved zero-cash source set on every stage."""
    from .freight_recovery import check_payment
    allocations=list(pay.allocations.select_related('supplier_invoice','freight_bill','fixed_asset','debit_note'))
    refund=pay.mode=='refund'
    if refund:
        if not pay.bank_id or not pay.bank.active or not pay.bank.is_bank or pay.bank.system_key=='cash' or pay.amount>=0 or not allocations or any(a.amount>=0 for a in allocations) or sum((a.amount for a in allocations),ZERO)!=pay.amount:raise ValidationError('A vendor refund requires an active receiving bank and negative credit allocations matching its incoming amount.')
    elif pay.mode!='offset' or pay.bank_id is not None or pay.amount!=0 or sum((a.amount for a in allocations),ZERO)!=0 or not any(a.amount>0 for a in allocations) or not any(a.amount<0 for a in allocations):
        raise ValidationError('A non-cash offset requires matching bills and credits with zero cash and no bank.')
    gl.check_open(pay.date)
    if pay.date>timezone.localdate():raise ValidationError('Settlement date cannot be in the future.')
    # Stable vendor lock serializes reservations. Lock each source category in a fixed order too.
    for model,field,date_field,vendor_field in [(SupplierInvoice,'supplier_invoice','invoice_date','supplier_id'),
        (FixedAsset,'fixed_asset','put_to_use','supplier_id'),(DebitNote,'debit_note','date','supplier_id'),(FreightBill,'freight_bill','bill_date','transporter_id')]:
        ids=[getattr(a,field+'_id') for a in allocations if getattr(a,field+'_id')]
        for obj in model.objects.select_for_update().filter(pk__in=ids).order_by('pk'):
            if getattr(obj,vendor_field)!=pay.payee_id:raise ValidationError('A settlement source belongs to another payee.')
            if getattr(obj,date_field)>pay.date:raise ValidationError('Settlement cannot precede its source documents.')
            if field=='supplier_invoice' and (obj.status not in ['matched','approved'] or allocated(field,obj)>obj.payable):raise ValidationError('The supplier invoice is no longer available for this offset.')
            if field=='fixed_asset' and (not obj.credit_purchase or allocated(field,obj)>obj.cost):raise ValidationError('The asset payable is no longer available for this offset.')
            if field=='debit_note' and (obj.supplier_invoice_id and obj.supplier_invoice.decision=='matched' or -allocated(field,obj)>obj.total):raise ValidationError('The debit note credit is no longer available for this offset.')
            if field=='freight_bill':
                dates=list(obj.recovery_adjustments.filter(status='approved').values_list('on',flat=True))
                dates.extend(obj.allocations.filter(payment__status='paid').values_list('payment__date',flat=True))
                if dates and max(dates)>pay.date:raise ValidationError('Settlement cannot precede recognized freight recovery or its paid credit backing.')
    check_payment(pay)


def offset_totals(pay):
    rows=list(pay.allocations.all())
    ap=sum((row.amount for row in rows if row.freight_bill_id is None),ZERO)
    freight=sum((row.amount for row in rows if row.freight_bill_id),ZERO)
    return ap,freight


def _settlement_read(user):
    from accounts.security import access_allowed
    if not access_allowed(user) or not user.has_perm('erp.view_supplierpayment'):raise PermissionDenied
    require_company_finance(user)


@transaction.atomic
def settle(user,pk,finding):
    _settlement_read(user)
    pay=SupplierPayment.objects.select_for_update().get(pk=pk)
    authorize(user,'release_supplierpayment',makers=(pay.created_by_id,),reference=pay.number)
    if pay.created_by_id==user.pk:raise ValidationError('The settlement maker cannot execute their own offset (maker-checker).')
    if pay.status!='approved' or pay.mode!='offset':raise ValidationError('Only an approved non-cash offset can be settled.')
    finding=(finding or '').strip()
    if not finding or len(finding)>200:raise ValidationError('Record a settlement finding of up to 200 characters.')
    Partner.objects.select_for_update(no_key=True).get(pk=pay.payee_id)
    check_offset(pay)
    ap,freight=offset_totals(pay)
    # Within one control account the source allocations cancel, so no empty journal exists.
    if ap or freight:
        gl.post(user,f'pay:{pay.pk}',pay.date,None,f'Non-cash settlement {pay.number}',[
            (gl.acct('ap'),max(ap,ZERO),max(-ap,ZERO),pay.payee,finding),
            (gl.acct('ap_freight'),max(freight,ZERO),max(-freight,ZERO),pay.payee,finding)])
    pay.status='paid';pay.paid_by=user;pay.paid_at=timezone.now();pay.note=finding;pay.settlement_note=finding;pay.save()
    _log(user,'Non-cash offset settled',pay.number,finding)
    return pay


@transaction.atomic
def reverse_offset(user,pk,reason):
    _settlement_read(user)
    pay=SupplierPayment.objects.select_for_update().get(pk=pk)
    authorize(user,'release_supplierpayment',makers=(pay.created_by_id,),reference=pay.number)
    if pay.created_by_id==user.pk:raise ValidationError('The settlement maker cannot reverse their own offset (maker-checker).')
    if pay.mode!='offset' or pay.status!='paid':raise ValidationError('Only a completed non-cash offset can be reversed.')
    reason=(reason or '').strip()
    if not reason or len(reason)>200:raise ValidationError('Record a settlement reversal reason of up to 200 characters.')
    Partner.objects.select_for_update(no_key=True).get(pk=pay.payee_id)
    gl.check_open(timezone.localdate())
    ap,freight=offset_totals(pay)
    if ap or freight:
        original=gl.JournalEntry.objects.filter(source=f'pay:{pay.pk}',status='posted').first()
        if not original or hasattr(original,'reversed_by'):raise ValidationError('The original settlement journal is missing or already reversed; reconcile it before reversal.')
        gl.reverse_source(user,f'pay:{pay.pk}',reason=f'Non-cash offset reversed: {reason}')
    pay.status='failed';pay.returned_on=timezone.localdate();pay.note=reason;pay.reversal_reason=reason;pay.returned_by=user;pay.returned_at=timezone.now();pay.save()
    _log(user,'Non-cash offset reversed',pay.number,reason)
    return pay


@transaction.atomic
def receive_refund(user,pk,reference,finding,on=None):
    _settlement_read(user)
    pay=SupplierPayment.objects.select_for_update().get(pk=pk)
    authorize(user,'release_supplierpayment',makers=(pay.created_by_id,),reference=pay.number)
    if user.pk==pay.created_by_id:raise ValidationError('The refund maker cannot record their own receipt (maker-checker).')
    if pay.mode!='refund' or pay.status!='approved':raise ValidationError('Only an approved vendor refund can be received.')
    reference=(reference or '').strip();finding=(finding or '').strip()
    if not reference or len(reference)>40 or not finding or len(finding)>200:raise ValidationError('Record the incoming bank reference (up to 40 characters) and receipt finding (up to 200).')
    # Bank mutex serializes duplicate incoming references across vendors.
    Account.objects.select_for_update(no_key=True).get(pk=pay.bank_id)
    Partner.objects.select_for_update(no_key=True).get(pk=pay.payee_id)
    if SupplierPayment.objects.filter(mode='refund',bank=pay.bank,status__in=['paid','failed'],reference__iexact=reference).exclude(pk=pay.pk).exists():raise ValidationError('This incoming bank reference is already recorded; reconcile it before entering another receipt.')
    pay.date=on or timezone.localdate()
    check_offset(pay)
    ap,freight=offset_totals(pay)
    lines=[(pay.bank,-pay.amount,0,None,reference)]
    if ap:lines.append((gl.acct('ap'),0,-ap,pay.payee,finding))
    if freight:lines.append((gl.acct('ap_freight'),0,-freight,pay.payee,finding))
    gl.post(user,f'pay:{pay.pk}',pay.date,None,f'Vendor refund {pay.number} · {reference}',lines)
    pay.status='paid';pay.reference=reference;pay.paid_by=user;pay.paid_at=timezone.now();pay.settlement_note=finding;pay.note=finding;pay.save()
    _log(user,'Vendor refund received',pay.number,f'{reference} · {finding}')
    return pay


@transaction.atomic
def reverse_refund(user,pk,reason):
    _settlement_read(user)
    pay=SupplierPayment.objects.select_for_update().get(pk=pk)
    authorize(user,'release_supplierpayment',makers=(pay.created_by_id,),reference=pay.number)
    if user.pk==pay.created_by_id:raise ValidationError('The refund maker cannot reverse their own receipt (maker-checker).')
    if pay.mode!='refund' or pay.status!='paid':raise ValidationError('Only a received vendor refund can be reversed.')
    reason=(reason or '').strip()
    if not reason or len(reason)>200:raise ValidationError('Record a refund reversal reason of up to 200 characters.')
    Partner.objects.select_for_update(no_key=True).get(pk=pay.payee_id)
    original=gl.JournalEntry.objects.filter(source=f'pay:{pay.pk}',status='posted').first()
    if not original or hasattr(original,'reversed_by'):raise ValidationError('The original refund journal is missing or already reversed.')
    gl.reverse_source(user,f'pay:{pay.pk}',reason=f'Vendor refund reversed: {reason}')
    pay.status='failed';pay.returned_on=timezone.localdate();pay.returned_by=user;pay.returned_at=timezone.now();pay.reversal_reason=reason;pay.note=reason;pay.save()
    _log(user,'Vendor refund reversed',pay.number,reason)
    return pay


def bank_file(payments):
    """Generic bulk-payment CSV (beneficiary, account, IFSC, amount, mode, reference) for the bank portal."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Payment ref', 'Beneficiary name', 'Account number', 'IFSC', 'Amount', 'Mode', 'Narration'])
    for p in payments:
        if p.mode in ['offset','refund']:continue
        if (p.beneficiary_name, p.beneficiary_account, p.beneficiary_ifsc) != (p.payee.name, p.payee.bank_account_number, p.payee.bank_ifsc):
            raise ValidationError(f'{p.number}: beneficiary details changed. Cancel and propose a fresh payment before exporting.')
        w.writerow([p.number, p.beneficiary_name, p.beneficiary_account, p.beneficiary_ifsc, f'{p.amount:.2f}', p.get_mode_display(), f'Sistech {p.number}'[:30]])
    return buf.getvalue()


# ---------- Bank reconciliation ----------

def _date(text):
    text = (text or '').strip()
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y', '%d-%b-%Y', '%d %b %Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValidationError(f'Date "{text}" is not recognised (use DD/MM/YYYY).')


def _amt(text):
    text = (text or '').replace(',', '').replace('₹', '').strip()
    return Decimal(text) if text else ZERO


@transaction.atomic
def import_statement(user, bank, raw):
    """CSV with columns: date, description, reference, withdrawal, deposit[, balance]. Header row optional."""
    _need(user, 'add_bankstatementline')
    if not bank or not bank.active or not bank.is_bank or bank.system_key == 'cash':
        raise ValidationError('Choose an active bank account.')
    gl.lock_finance()
    rows = list(csv.reader(io.StringIO(raw)))
    if rows and not any(ch.isdigit() for ch in (rows[0][0] if rows[0] else '')):
        rows = rows[1:]
    made = skipped = 0
    for i, r in enumerate(rows, 1):
        if not r or not any(c.strip() for c in r):
            continue
        if len(r) < 5:
            raise ValidationError(f'Row {i}: expected date, description, reference, withdrawal, deposit.')
        try:
            wd, dp = _amt(r[3]), _amt(r[4])
            bal = _amt(r[5]) if len(r) > 5 and r[5].strip() else None
        except Exception:
            raise ValidationError(f'Row {i}: amounts must be numbers.')
        if not all(v.is_finite() for v in (wd, dp) + ((bal,) if bal is not None else ())):
            raise ValidationError(f'Row {i}: amounts must be finite numbers.')
        if wd < 0 or dp < 0 or (wd and dp) or (not wd and not dp):
            raise ValidationError(f'Row {i}: each row is either a withdrawal or a deposit.')
        on = _date(r[0])
        if BankStatementLine.objects.filter(bank=bank, date=on, description=r[1].strip()[:200], reference=r[2].strip()[:60], withdrawal=wd, deposit=dp).exists():
            skipped += 1
            continue
        gl.check_open(on)
        try:
            with transaction.atomic():
                BankStatementLine.objects.create(bank=bank, date=on, description=r[1].strip()[:200], reference=r[2].strip()[:60], withdrawal=wd, deposit=dp,
                                                 balance=bal, imported_by=user)
            made += 1
        except IntegrityError:
            skipped += 1
    _log(user, 'Bank statement imported', bank.code, f'{made} row(s), {skipped} duplicate(s) skipped')
    return made, skipped


def candidates(st, days=5):
    """Unmatched ledger lines on the same bank with the same amount and a nearby date."""
    qs = JournalLine.objects.filter(account=st.bank, entry__status='posted', statement_line__isnull=True,
                                    entry__date__range=(st.date - timedelta(days=days), st.date + timedelta(days=days)))
    qs = qs.filter(debit=st.deposit) if st.deposit else qs.filter(credit=st.withdrawal)
    return qs.select_related('entry')


@transaction.atomic
def auto_match(user, bank):
    _need(user, 'change_bankstatementline')
    gl.lock_finance()
    n = 0
    for st in BankStatementLine.objects.select_for_update().filter(bank=bank, matched_line__isnull=True).order_by('date'):
        gl.check_open(st.date)
        cands = list(candidates(st))
        ref = (st.reference or '').strip().upper()
        exact = [c for c in cands if ref and ref in {c.narration.strip().upper(), c.entry.source or ''}]
        pick = exact[0] if len(exact) == 1 else None
        if pick:
            st.matched_line, st.matched_by, st.matched_at = pick, user, timezone.now()
            st.save()
            n += 1
    if n:
        _log(user, 'Bank auto-match', bank.code, f'{n} row(s) matched')
    return n


@transaction.atomic
def match(user, st_pk, line_pk):
    _need(user, 'change_bankstatementline')
    gl.lock_finance()
    st = BankStatementLine.objects.select_for_update().get(pk=st_pk)
    gl.check_open(st.date)
    if line_pk is None:
        st.matched_line = None
        st.matched_by = st.matched_at = None
        st.save()
        _log(user, 'Bank match removed', str(st.pk), st.bank.code)
        return st
    line = JournalLine.objects.select_for_update().filter(pk=line_pk, account=st.bank, entry__status='posted').first()
    if line is None:
        raise ValidationError('Choose a posted ledger line from this bank account.')
    if hasattr(line, 'statement_line') and line.statement_line.pk != st.pk:
        raise ValidationError('That ledger line is already matched.')
    if (st.deposit and line.debit != st.deposit) or (st.withdrawal and line.credit != st.withdrawal):
        raise ValidationError('Amounts differ — record the bank charge / interest as a journal voucher first.')
    st.matched_line, st.matched_by, st.matched_at = line, user, timezone.now()
    st.save()
    _log(user, 'Bank line matched', str(st.pk), f'{st.bank.code} · {line.entry.number}')
    return st


def reconciliation(bank, as_of):
    book = JournalLine.objects.filter(account=bank, entry__status='posted', entry__date__lte=as_of).aggregate(d=Sum('debit'), c=Sum('credit'))
    book_bal = (book['d'] or ZERO) - (book['c'] or ZERO)
    stmt = BankStatementLine.objects.filter(bank=bank, date__lte=as_of)
    last = stmt.exclude(balance=None).order_by('-date', '-pk').first()
    unpresented = JournalLine.objects.filter(account=bank, entry__status='posted', entry__date__lte=as_of, ).filter(Q(statement_line__isnull=True) | Q(statement_line__date__gt=as_of)).select_related('entry').order_by('entry__date')
    unmatched = stmt.filter(Q(matched_line__isnull=True) | Q(matched_line__entry__date__gt=as_of)).order_by('date')
    return {'book': book_bal, 'statement': last.balance if last else None, 'statement_date': last.date if last else None,
            'unpresented': unpresented, 'unmatched': unmatched,
            'adj': sum((l.debit - l.credit for l in unpresented), ZERO), 'stmt_only': sum((s.amount for s in unmatched), ZERO)}


# ---------- GST summary ----------

def gst_summary(start, end, site=None):
    inv = Invoice.objects.filter(date__range=(start, end))
    cns = CreditNote.objects.filter(date__range=(start, end))
    sinv = SupplierInvoice.objects.filter(invoice_date__range=(start, end), status__in=['matched', 'approved'])
    dns = DebitNote.objects.filter(date__range=(start, end))
    if site:
        inv, cns, sinv, dns = inv.filter(site=site), cns.filter(invoice__site=site), sinv.filter(site=site), dns.filter(site=site)
    agg = lambda qs, *f: {k: qs.aggregate(t=Sum(k))['t'] or ZERO for k in f}
    out = agg(inv, 'taxable', 'cgst', 'sgst', 'igst')
    b2b = agg(inv.exclude(buyer_gstin=''), 'taxable')
    cn = agg(cns, 'taxable', 'cgst', 'sgst', 'igst')
    inward = {'taxable': sinv.aggregate(t=Sum('billed_taxable'))['t'] or ZERO, 'tax': sinv.aggregate(t=Sum('billed_tax'))['t'] or ZERO}
    dn = {'taxable': dns.aggregate(t=Sum('taxable'))['t'] or ZERO, 'tax': dns.aggregate(t=Sum('tax'))['t'] or ZERO}
    output_tax = out['cgst'] + out['sgst'] + out['igst'] - cn['cgst'] - cn['sgst'] - cn['igst']
    input_tax = inward['tax'] - dn['tax']
    hsn = defaultdict(lambda: {'qty': ZERO, 'taxable': ZERO, 'tax': ZERO, 'unit': '', 'rate': ZERO})
    lines = InvoiceLine.objects.filter(invoice__in=inv)
    for l in lines:
        h = hsn[(l.hsn or '—', l.gst_rate, l.unit)]
        h['qty'] += l.quantity; h['taxable'] += l.taxable; h['tax'] += l.cgst + l.sgst + l.igst; h['unit'] = l.unit; h['rate'] = l.gst_rate
    by_rate = defaultdict(lambda: {'taxable': ZERO, 'tax': ZERO})
    for l in lines:
        by_rate[l.gst_rate]['taxable'] += l.taxable; by_rate[l.gst_rate]['tax'] += l.cgst + l.sgst + l.igst
    return {'outward': out, 'b2b_taxable': b2b['taxable'], 'b2c_taxable': out['taxable'] - b2b['taxable'], 'invoices': inv.count(), 'b2b_count': inv.exclude(buyer_gstin='').count(),
            'credit': cn, 'credit_count': cns.count(), 'inward': inward, 'debit': dn, 'output_tax': output_tax, 'input_tax': input_tax, 'net': output_tax - input_tax,
            'hsn': sorted(((k[0], v) for k, v in hsn.items()), key=lambda x: x[0]), 'by_rate': sorted(by_rate.items())}


# ---------- Fixed assets ----------

@transaction.atomic
def capitalise(user, data, funded_by):
    """data: code, name, category, site, location, supplier, put_to_use, cost, salvage, method, life_years, wdv_rate. funded_by: 'ap' (supplier) or bank Account."""
    _need(user, 'add_fixedasset')
    problems = []
    code = (data.get('code') or '').strip().upper()
    if not code or not (data.get('name') or '').strip(): problems.append('Enter the asset code and name.')
    if FixedAsset.objects.filter(code=code).exists(): problems.append(f'Asset code {code} is already used.')
    cost, salvage = Decimal(data.get('cost') or 0), Decimal(data.get('salvage') or 0)
    if not cost.is_finite() or not salvage.is_finite():
        raise ValidationError('Asset amounts must be finite numbers.')
    if data.get('category') not in dict(ASSET_CATEGORIES): problems.append('Choose an asset category.')
    if data.get('method') not in ('slm', 'wdv'): problems.append('Choose a depreciation method.')
    if not data.get('site') or not data['site'].active: problems.append('Choose an active plant or depot.')
    if not data.get('put_to_use') or data['put_to_use'] > timezone.localdate(): problems.append('Put-to-use date cannot be in the future.')
    for field, maximum in [('life_years', Decimal(999)), ('wdv_rate', Decimal(100))]:
        value = data.get(field)
        if value is not None and (not Decimal(value).is_finite() or not 0 < Decimal(value) <= maximum):
            problems.append('Useful life must be positive and WDV rate must be between 0 and 100%.')
    if funded_by != 'ap' and (not funded_by or not funded_by.is_bank or not funded_by.active):
        problems.append('Choose an active funding account.')
    if cost <= 0: problems.append('Enter the cost.')
    if salvage < 0 or salvage >= cost: problems.append('Residual value must be below cost.')
    if data.get('method') == 'slm' and not data.get('life_years'): problems.append('Enter the useful life for straight-line depreciation.')
    if data.get('method') == 'wdv' and not data.get('wdv_rate'): problems.append('Enter the WDV rate.')
    if funded_by == 'ap' and not data.get('supplier'): problems.append('Choose the supplier when the asset is bought on credit.')
    if problems:
        raise ValidationError(problems)
    a = FixedAsset.objects.create(code=code[:30], name=data['name'].strip()[:120], category=data['category'], site=data['site'], location=(data.get('location') or '')[:100],
                                  supplier=data.get('supplier'), credit_purchase=funded_by == 'ap', put_to_use=data['put_to_use'], cost=cost, salvage=salvage, method=data['method'],
                                  life_years=data.get('life_years'), wdv_rate=data.get('wdv_rate'), created_by=user)
    credit = (gl.acct('ap'), 0, cost, data.get('supplier'), a.code) if funded_by == 'ap' else (funded_by, 0, cost, None, a.code)
    gl.post(user, f'fa:{a.pk}', a.put_to_use, a.site, f'Asset capitalised {a.code} · {a.name}', [(gl.acct(a.category), cost, 0, None, a.code), credit])
    _log(user, 'Asset capitalised', a.code, f'{a.name} · ₹{inr(cost, 2)}')
    return a


def monthly_depreciation(a, month):
    if a.depreciation.filter(month__gt=month).exists():
        raise ValidationError('Depreciation must be run in chronological order; a later month is already posted.')
    if a.status != 'active' or a.put_to_use > month.replace(day=calendar.monthrange(month.year, month.month)[1]) or a.depreciation.filter(month=month).exists():
        return ZERO
    if a.method == 'slm':
        amt = (a.cost - a.salvage) / Decimal(a.life_years) / 12
    else:
        amt = a.book_value * Decimal(a.wdv_rate) / 100 / 12
    return min(amt, a.book_value - a.salvage).quantize(CENT, rounding=ROUND_HALF_UP) if a.book_value > a.salvage else ZERO


@transaction.atomic
def run_depreciation(user, month):
    _need(user, 'depreciate_fixedasset')
    month = month.replace(day=1)
    import calendar
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    total, n = ZERO, 0
    for a in FixedAsset.objects.select_for_update().filter(status='active'):
        amt = monthly_depreciation(a, month)
        if amt <= 0:
            continue
        je = gl.post(user, f'dep:{a.pk}:{month:%Y%m}', end, a.site, f'Depreciation {month:%b %Y} · {a.code}',
                     [(gl.acct('depreciation'), amt, 0, None, a.code), (gl.acct('acc_depreciation'), 0, amt, None, a.code)])
        DepreciationEntry.objects.create(asset=a, month=month, amount=amt, journal=je)
        total += amt
        n += 1
    _log(user, 'Depreciation run', f'{month:%b %Y}', f'{n} asset(s) · ₹{inr(total, 2)}')
    return n, total


@transaction.atomic
def dispose(user, pk, on, proceeds, bank):
    _need(user, 'depreciate_fixedasset')
    a = FixedAsset.objects.select_for_update().get(pk=pk)
    if a.status != 'active':
        raise ValidationError('Asset already disposed.')
    proceeds = Decimal(proceeds or 0)
    if not proceeds.is_finite():
        raise ValidationError('Sale value must be a finite number.')
    if on < a.put_to_use or a.depreciation.filter(month__gt=on.replace(day=1)).exists():
        raise ValidationError('Disposal cannot predate put-to-use or posted depreciation.')
    if proceeds < 0:
        raise ValidationError('Sale value cannot be negative.')
    if proceeds and (not bank or not bank.is_bank):
        raise ValidationError('Choose the bank that received the sale value.')
    acc, bv = a.accumulated, a.book_value
    gain = proceeds - bv
    lines = [(gl.acct('acc_depreciation'), acc, 0, None, ''), (gl.acct(a.category), 0, a.cost, None, a.code)]
    if proceeds: lines.append((bank, proceeds, 0, None, 'Sale value'))
    if gain > 0: lines.append((gl.acct('asset_gain'), 0, gain, None, ''))
    elif gain < 0: lines.append((gl.acct('asset_loss'), -gain, 0, None, ''))
    gl.post(user, f'fad:{a.pk}', on, a.site, f'Asset disposed {a.code} · book value ₹{inr(bv, 2)} · sold ₹{inr(proceeds, 2)}', lines)
    a.status, a.disposed_on, a.proceeds = 'disposed', on, proceeds
    a.save()
    _log(user, 'Asset disposed', a.code, f'{"gain" if gain >= 0 else "loss"} ₹{inr(abs(gain), 2)}')
    return a


# ---------- Cash flow (direct method from bank / cash vouchers) ----------

CASH_HEADS = [('Receipts from customers', 'operating', {'ar'}), ('Payments to suppliers', 'operating', {'ap'}), ('Freight paid', 'operating', {'ap_freight'}),
              ('Purchase / sale of fixed assets', 'investing', {'fa_plant', 'fa_building', 'fa_vehicle', 'fa_office', 'asset_gain', 'asset_loss'}),
              ('Capital introduced', 'financing', {'capital'})]


def cash_flow(start, end):
    banks = set(Account.objects.filter(is_bank=True).values_list('pk', flat=True))
    heads = defaultdict(lambda: ZERO)
    for je in gl.JournalEntry.objects.filter(status='posted', date__range=(start, end), lines__account__in=banks).distinct().prefetch_related('lines__account'):
        cash = sum((l.debit - l.credit for l in je.lines.all() if l.account_id in banks), ZERO)
        if not cash:
            continue
        keys = {l.account.system_key for l in je.lines.all() if l.account_id not in banks}
        head = next((h for h, _, k in CASH_HEADS if keys & k), 'Other operating items (expenses, adjustments)')
        heads[head] += cash
    rows = [(h, sec, heads.get(h, ZERO)) for h, sec, _ in CASH_HEADS] + [('Other operating items (expenses, adjustments)', 'operating', heads.get('Other operating items (expenses, adjustments)', ZERO))]
    opening = JournalLine.objects.filter(account__in=banks, entry__status='posted', entry__date__lt=start).aggregate(d=Sum('debit'), c=Sum('credit'))
    ob = (opening['d'] or ZERO) - (opening['c'] or ZERO)
    net = sum((r[2] for r in rows), ZERO)
    by_sec = {s: sum((r[2] for r in rows if r[1] == s), ZERO) for s in ('operating', 'investing', 'financing')}
    return {'rows': rows, 'sections': by_sec, 'opening': ob, 'net': net, 'closing': ob + net}
