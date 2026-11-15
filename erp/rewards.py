"""Scheme entitlements, accrual and payout (SRS §5.3, FR-INC-005…010) on top of the retained calculations in `schemes`.

Controls:
- only a calculation covering the whole approved scheme period can become entitlements, and only if its sources are unchanged
  since it was taken (otherwise calculate again);
- a dealer earns once per scheme version: each batch records only the difference from amounts already approved, so a later
  recalculation after returns produces a recovery (negative) instead of a second earning (FR-INC-009);
- a different user approves the batch; approval accrues Dr Scheme discounts / Cr Scheme incentives payable per dealer;
- payouts take the dealer's open entitlements (net of recoveries); a different user approves; a ledger credit reduces the
  dealer's outstanding and settles invoices like a credit note, a bank payout needs the UTR;
- the dealer statement shows earned, pending approval, paid and recovered amounts (FR-INC-010).
"""
from accounts.delegation import authorize, can_approve
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .finance_access import require_company_finance
from .services import require_site
from . import gl
from .gst import next_number, inr
from .models import (SchemeEvaluation, SchemeEntitlementBatch, SchemeEntitlement, SchemeSettlement, SchemeVersion, Partner, Account, AuditEvent)
from .schemes import _payload

ZERO = Decimal('0.00')


def _need(user, perm):
    require_company_finance(user)
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=reference, detail=detail[:250])


def approved_so_far(version, partner):
    return SchemeEntitlement.objects.filter(version=version, partner=partner, batch__status='approved').aggregate(t=Sum('amount'))['t'] or ZERO


@transaction.atomic
def create_batch(user, evaluation_pk, note=''):
    _need(user, 'add_schemeentitlementbatch')
    ev = SchemeEvaluation.objects.select_for_update().select_related('version__scheme').get(pk=evaluation_pk)
    v = ev.version
    require_site(user, v.scheme.site)
    if hasattr(ev, 'batch'):
        raise ValidationError(f'This calculation already became {ev.batch}.')
    if (ev.starts_on, ev.ends_on) != (v.starts_on, v.ends_on):
        raise ValidationError('Entitlements need a calculation over the whole scheme period. Calculate the full period first.')
    if v.ends_on > timezone.localdate():
        raise ValidationError('The scheme period has not ended yet.')
    if SchemeEntitlementBatch.objects.filter(version=v, status='submitted').exists():
        raise ValidationError('A batch for this scheme version is already waiting for approval.')
    _, digest = _payload(v, ev.starts_on, ev.ends_on)
    if digest != ev.source_digest:
        raise ValidationError('Invoices, returns or receipts changed after this calculation. Calculate again and use the new one.')
    batch = SchemeEntitlementBatch.objects.create(evaluation=ev, version=v, total=ZERO, note=(note or '')[:200], created_by=user)
    total = ZERO
    for row in ev.snapshot.get('dealers', []):
        partner = Partner.objects.get(pk=row['partner_id'])
        calc = Decimal(row['incentive'])
        prior = approved_so_far(v, partner)
        amount = calc - prior
        if not amount:
            continue
        SchemeEntitlement.objects.create(batch=batch, version=v, partner=partner, eligible=Decimal(row['eligible']), rate=Decimal(row['percentage']),
                                         calculated=calc, prior=prior, amount=amount)
        total += amount
    if not batch.entitlements.exists():
        raise ValidationError('Nothing new to approve — every dealer already has exactly this amount approved.')
    batch.total = total
    batch.save(update_fields=['total'])
    _log(user, 'Scheme entitlements prepared', str(batch), f'{v} · {batch.entitlements.count()} dealer(s) · ₹{inr(total, 2)}')
    return batch


@transaction.atomic
def decide_batch(user, pk, approve, note=''):
    require_company_finance(user)
    b = SchemeEntitlementBatch.objects.select_for_update().select_related('version__scheme__site').get(pk=pk)
    require_site(user, b.version.scheme.site)
    authorize(user, 'approve_schemeentitlementbatch', b.version.scheme.site, makers=(b.created_by_id, b.evaluation.created_by_id), reference=str(b))
    if b.status != 'submitted':
        raise ValidationError('Only batches waiting for approval can be decided.')
    if user.pk in (b.created_by_id, b.evaluation.created_by_id):
        raise ValidationError('The person who calculated or prepared the batch cannot approve it (maker-checker).')
    note = (note or '').strip()
    if not approve and not note:
        raise ValidationError('Give the reason for rejecting.')
    if approve:
        _, digest = _payload(b.version, b.evaluation.starts_on, b.evaluation.ends_on)
        if digest != b.evaluation.source_digest:
            raise ValidationError('Sources changed after the calculation. Reject this batch and prepare it again from a fresh calculation.')
        lines = []
        for e in b.entitlements.select_related('partner'):
            if e.amount > 0:
                lines += [(gl.acct('sales_schemes'), e.amount, 0, None, e.partner.name), (gl.acct('scheme_payable'), 0, e.amount, e.partner, str(b))]
            else:
                lines += [(gl.acct('scheme_payable'), -e.amount, 0, e.partner, str(b)), (gl.acct('sales_schemes'), 0, -e.amount, None, f'Recovery {e.partner.name}')]
        gl.post(user, f'schacc:{b.pk}', timezone.localdate(), b.version.scheme.site,
                f'Scheme accrual {b} · {b.version}', lines)
    b.status, b.decided_by, b.decided_at, b.decision_note = ('approved' if approve else 'rejected'), user, timezone.now(), note[:200]
    b.save()
    _log(user, 'Scheme entitlements approved' if approve else 'Scheme entitlements rejected', str(b), f'₹{inr(b.total, 2)}' + (f' · {note}' if note else ''))
    return b


def open_entitlements(partner):
    return SchemeEntitlement.objects.filter(partner=partner, batch__status='approved', settlement__isnull=True).select_related('version__scheme', 'batch')


def payable_dealers():
    out = []
    for p in Partner.objects.filter(scheme_entitlements__batch__status='approved', scheme_entitlements__settlement__isnull=True).distinct().order_by('name'):
        due = open_entitlements(p).aggregate(t=Sum('amount'))['t'] or ZERO
        if due > 0:
            out.append({'partner': p, 'due': due, 'count': open_entitlements(p).count()})
    return out


@transaction.atomic
def propose_payout(user, partner, mode, bank=None, note=''):
    _need(user, 'add_schemesettlement')
    Partner.objects.select_for_update().get(pk=partner.pk)
    ents = list(open_entitlements(partner).select_for_update())
    amount = sum((e.amount for e in ents), ZERO)
    if amount <= 0:
        raise ValidationError(f'{partner.name} has nothing to pay (recoveries cover the earnings).')
    if mode not in ('ledger', 'bank'):
        raise ValidationError('Choose how to pay.')
    if mode == 'bank' and (not bank or not bank.is_bank):
        raise ValidationError('Choose the bank account to pay from.')
    if mode == 'bank' and not (partner.bank_account_number and partner.bank_ifsc):
        raise ValidationError(f'{partner.name} has no bank account / IFSC on file — pay as a credit to the account or update the master.')
    s = SchemeSettlement.objects.create(number=next_number('SCH', None), partner=partner, mode=mode, bank=bank if mode == 'bank' else None, amount=amount,
                                        note=(note or '')[:200], created_by=user)
    SchemeEntitlement.objects.filter(pk__in=[e.pk for e in ents]).update(settlement=s)
    _log(user, 'Scheme payout proposed', s.number, f'{partner.name} · {s.get_mode_display()} · ₹{inr(amount, 2)}')
    return s


@transaction.atomic
def decide_payout(user, pk, approve, reference='', note=''):
    require_company_finance(user)
    s = SchemeSettlement.objects.select_for_update().select_related('partner', 'bank').get(pk=pk)
    authorize(user, 'approve_schemesettlement', makers=(s.created_by_id,), reference=s.number)
    if s.status != 'proposed':
        raise ValidationError('Only proposed payouts can be decided.')
    if s.created_by_id == user.pk:
        raise ValidationError('The person who proposed the payout cannot approve it (maker-checker).')
    reference, note = (reference or '').strip(), (note or '').strip()
    if not approve:
        if not note:
            raise ValidationError('Give the reason for cancelling.')
        s.status, s.note = 'cancelled', note[:200]
        s.save()
        s.entitlements.update(settlement=None)  # entitlements become payable again
        _log(user, 'Scheme payout cancelled', s.number, note)
        return s
    if s.mode == 'bank' and not reference:
        raise ValidationError('Enter the UTR of the bank transfer.')
    credit = (gl.acct('ar'), 0, s.amount, s.partner, s.number) if s.mode == 'ledger' else (s.bank, 0, s.amount, None, reference)
    s.status, s.approved_by, s.approved_at, s.reference = 'posted', user, timezone.now(), reference[:40]
    s.save()
    gl.post(user, f'schpay:{s.pk}', s.date, None, f'Scheme payout {s.number} · {s.partner.name} · {s.get_mode_display()}',
            [(gl.acct('scheme_payable'), s.amount, 0, s.partner, s.number), credit])
    if s.mode == 'ledger':
        from .ledger import allocate
        allocate(s.partner)
    _log(user, 'Scheme payout posted', s.number, f'{s.partner.name} · ₹{inr(s.amount, 2)}' + (f' · UTR {reference}' if reference else ''))
    return s


def statement(partner):
    """Per scheme version: calculated (latest), approved earnings, recoveries, paid and balance; plus pending batches."""
    rows = {}
    for e in SchemeEntitlement.objects.filter(partner=partner).select_related('version__scheme', 'batch', 'settlement').order_by('batch__created_at'):
        r = rows.setdefault(e.version_id, {'version': e.version, 'earned': ZERO, 'recovered': ZERO, 'paid': ZERO, 'pending': ZERO, 'items': []})
        r['items'].append(e)
        if e.batch.status == 'approved':
            if e.amount > 0: r['earned'] += e.amount
            else: r['recovered'] += -e.amount
            if e.settlement and e.settlement.status == 'posted': r['paid'] += e.amount
        elif e.batch.status == 'submitted':
            r['pending'] += e.amount
    for r in rows.values():
        r['balance'] = r['earned'] - r['recovered'] - r['paid']
    return list(rows.values())
