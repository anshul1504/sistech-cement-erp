"""Dealer security deposits (SRS FR-AR-008): separate refundable ledger, never netted into trade receivables.

Receipt: recorded by finance when the money is in the bank → Dr Bank / Cr Dealer security deposits.
Refund: proposed (cannot exceed the deposit held, and the dealer must not owe overdue money unless the reason says it was
settled), approved by a different user with recent identity confirmation at the screen → Dr Dealer security deposits / Cr Bank.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum

from .finance_access import require_company_finance
from .gst import next_number
from .models import DepositEntry, Partner, Account, AuditEvent, CUSTOMER_KINDS

ZERO = Decimal('0')


def held(partner=None):
    qs = DepositEntry.objects.filter(status='posted')
    if partner is not None:
        qs = qs.filter(partner=partner)
    rec = qs.filter(kind='receipt').aggregate(t=Sum('amount'))['t'] or ZERO
    ref = qs.filter(kind='refund').aggregate(t=Sum('amount'))['t'] or ZERO
    return rec - ref


def _need(user, perm):
    require_company_finance(user)
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _post(user, e):
    from . import gl
    dep, bank = gl.acct('security_deposits'), e.bank
    lines = [(bank, e.amount, ZERO, None, e.reference), (dep, ZERO, e.amount, e.partner, e.note)] if e.kind == 'receipt' else \
            [(dep, e.amount, ZERO, e.partner, e.note), (bank, ZERO, e.amount, None, e.reference)]
    gl.post(user, f'dep:{e.pk}', e.date, None, f'Security deposit {e.get_kind_display().lower()} · {e.partner.name} · {e.number}', lines)


def _validate(partner, amount, bank, reference):
    problems = []
    if partner.kind not in CUSTOMER_KINDS:
        problems.append('Security deposits are taken from customers.')
    if not amount or amount <= 0:
        problems.append('Enter the amount.')
    if not bank or not bank.is_bank or not bank.active:
        problems.append('Choose an active bank account.')
    if not (reference or '').strip():
        problems.append('Enter the UTR / cheque number.')
    if problems:
        raise ValidationError(problems)


@transaction.atomic
def receive(user, partner, amount, bank, reference, on, note=''):
    _need(user, 'add_depositentry')
    _validate(partner, amount, bank, reference)
    e = DepositEntry.objects.create(number=next_number('DEP'), partner=partner, kind='receipt', date=on, amount=amount, bank=bank, reference=reference.strip()[:40],
                                    note=(note or '')[:200], created_by=user, status='posted')
    _post(user, e)
    AuditEvent.objects.create(actor=user, action='Security deposit received', reference=e.number, detail=f'{partner.name} ₹{amount} {reference}')
    return e


@transaction.atomic
def propose_refund(user, partner, amount, bank, reference, on, note):
    _need(user, 'add_depositentry')
    Partner.objects.select_for_update().get(pk=partner.pk)
    _validate(partner, amount, bank, reference)
    pending = DepositEntry.objects.filter(partner=partner, kind='refund', status='proposed').aggregate(t=Sum('amount'))['t'] or ZERO
    if amount > held(partner) - pending:
        raise ValidationError(f'Only ₹{held(partner) - pending} of deposit is available to refund.')
    if not (note or '').strip():
        raise ValidationError('Record why the deposit is refunded (dealership closed, limit reduced …).')
    from .ledger import overdue
    if overdue(partner) and 'settled' not in note.lower():
        raise ValidationError('The dealer has overdue invoices. Collect them first, or say in the reason how they were settled.')
    e = DepositEntry.objects.create(number=next_number('DEP'), partner=partner, kind='refund', date=on, amount=amount, bank=bank, reference=reference.strip()[:40],
                                    note=note.strip()[:200], created_by=user, status='proposed')
    AuditEvent.objects.create(actor=user, action='Deposit refund proposed', reference=e.number, detail=f'{partner.name} ₹{amount} · {note}')
    return e


@transaction.atomic
def decide_refund(user, pk, approve):
    _need(user, 'approve_depositentry')
    e = DepositEntry.objects.select_for_update().select_related('partner', 'bank').get(pk=pk)
    if e.kind != 'refund' or e.status != 'proposed':
        raise ValidationError('Only a proposed refund can be decided.')
    if e.created_by_id == user.pk:
        raise ValidationError('A different user must approve the refund (maker-checker).')
    Partner.objects.select_for_update().get(pk=e.partner_id)
    if approve:
        if e.amount > held(e.partner):
            raise ValidationError('The deposit held is now less than this refund.')
        e.status, e.approved_by = 'posted', user
        e.save()
        _post(user, e)
    else:
        e.status, e.approved_by = 'rejected', user
        e.save()
    AuditEvent.objects.create(actor=user, action='Deposit refund approved' if approve else 'Deposit refund rejected', reference=e.number, detail=f'{e.partner.name} ₹{e.amount}')
    return e
