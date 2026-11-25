"""Sales commission (SRS FR-INC-004, FR-INC-005/006/007/008).

Attribution: a person earns on the sales orders they created (same as CRM performance), optionally limited to one plant.
Bases: net sales value excl. GST (invoices − credit notes), money collected against those invoices, or net MT × ₹/MT.
Earn-once: a run calculates the cumulative commission from the plan's start to the run end and subtracts what earlier
approved runs already gave the same person, so returns/credit notes after a payout reduce the next run (recovery).
Plans and runs need approval by a different user. Approving a run posts Dr Sales commission / Cr Commission payable
(and TDS payable when the plan has a TDS rate confirmed by the tax advisor). Runs of one plan are sequential and cannot overlap.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .finance_access import require_company_finance
from .gst import next_number
from .models import CommissionPlan, CommissionRun, CommissionLine, Invoice, CreditNote, Settlement, AuditEvent

ZERO = Decimal('0')
CENT = Decimal('0.01')


def _need(user, perm):
    """Plans are commercial rules (sales manager drafts); runs move money and need company-finance scope."""
    if 'run' in perm:
        require_company_finance(user)
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, action, ref, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=ref, detail=detail[:250])


# ---------- Plans ----------

@transaction.atomic
def save_plan(user, data, people, pk=None):
    _need(user, 'change_commissionplan' if pk else 'add_commissionplan')
    plan = CommissionPlan.objects.select_for_update().get(pk=pk) if pk else CommissionPlan(created_by=user)
    if pk and plan.status not in ('draft', 'rejected'):
        raise ValidationError('An approved plan cannot be edited — end it and create a new one.')
    problems = []
    if not (data.get('name') or '').strip():
        problems.append('Name the plan.')
    if data.get('basis') not in ('sales', 'collected', 'mt'):
        problems.append('Choose the basis.')
    rate = data.get('rate')
    if rate is None or rate <= 0 or (data.get('basis') != 'mt' and rate > 20):
        problems.append('Rate must be above 0 (and at most 20 % for value-based plans).')
    tds = data.get('tds_rate') or ZERO
    if tds < 0 or tds > 30:
        problems.append('TDS rate must be between 0 and 30 %.')
    if not data.get('valid_from') or (data.get('valid_to') and data['valid_to'] < data['valid_from']):
        problems.append('Give a valid start date (and an end date after it).')
    if not people:
        problems.append('Choose at least one sales person.')
    if problems:
        raise ValidationError(problems)
    for k in ('name', 'basis', 'rate', 'site', 'valid_from', 'valid_to'):
        setattr(plan, k, data.get(k))
    plan.name, plan.tds_rate, plan.status = plan.name.strip()[:100], tds, 'draft'
    plan.save()
    plan.beneficiaries.set(people)
    _log(user, 'Commission plan saved', f'commission-plan:{plan.pk}', f'{plan.name} · {plan.get_basis_display()} {plan.rate}')
    return plan


@transaction.atomic
def submit_plan(user, pk):
    _need(user, 'change_commissionplan')
    plan = CommissionPlan.objects.select_for_update().get(pk=pk)
    if plan.status not in ('draft', 'rejected'):
        raise ValidationError('Only a draft plan can be submitted.')
    plan.status = 'submitted'
    plan.save(update_fields=['status'])
    _log(user, 'Commission plan submitted', f'commission-plan:{plan.pk}', plan.name)
    return plan


@transaction.atomic
def decide_plan(user, pk, approve, note=''):
    _need(user, 'approve_commissionplan')
    plan = CommissionPlan.objects.select_for_update().get(pk=pk)
    if plan.status != 'submitted':
        raise ValidationError('Only a submitted plan can be decided.')
    if plan.created_by_id == user.pk:
        raise ValidationError('A different user must approve the plan (maker-checker).')
    if not approve and not (note or '').strip():
        raise ValidationError('Say what must change.')
    plan.status, plan.decided_by, plan.decision_note = ('approved' if approve else 'rejected'), user, (note or '').strip()[:200]
    plan.save()
    _log(user, 'Commission plan approved' if approve else 'Commission plan sent back', f'commission-plan:{plan.pk}', note)
    return plan


@transaction.atomic
def end_plan(user, pk, on):
    _need(user, 'approve_commissionplan')
    plan = CommissionPlan.objects.select_for_update().get(pk=pk)
    if plan.status != 'approved':
        raise ValidationError('Only an approved plan can be ended.')
    last = plan.runs.filter(status='approved').order_by('-end').first()
    if last and on < last.end:
        raise ValidationError(f'The plan has an approved run up to {last.end:%d %b %Y}.')
    plan.valid_to, plan.status = on, 'ended'
    plan.save()
    _log(user, 'Commission plan ended', f'commission-plan:{plan.pk}', f'{on}')
    return plan


# ---------- Calculation ----------

def basis(plan, person, start, end):
    from .stock import mt_factor
    inv = Invoice.objects.filter(order__created_by=person)
    if plan.site_id:
        inv = inv.filter(site_id=plan.site_id)
    if plan.basis == 'collected':
        return Settlement.objects.filter(invoice__in=inv, receipt__status='posted', receipt__date__range=(start, end)).aggregate(t=Sum('amount'))['t'] or ZERO
    period = inv.filter(date__range=(start, end))
    cns = CreditNote.objects.filter(invoice__in=inv, date__range=(start, end))
    if plan.basis == 'sales':
        return (period.aggregate(t=Sum('taxable'))['t'] or ZERO) - (cns.aggregate(t=Sum('taxable'))['t'] or ZERO)
    mt = ZERO
    from .models import InvoiceLine, CreditNoteLine
    for l in InvoiceLine.objects.filter(invoice__in=period).select_related('product'):
        mt += l.quantity * (mt_factor(l.product) or ZERO)
    for l in CreditNoteLine.objects.filter(credit_note__in=cns).select_related('invoice_line__product'):
        mt -= l.quantity * (mt_factor(l.invoice_line.product) or ZERO)
    return mt.quantize(Decimal('0.001'))


def _commission(plan, amount):
    return (amount * plan.rate / (1 if plan.basis == 'mt' else 100)).quantize(CENT)


@transaction.atomic
def calculate(user, plan_pk, end):
    _need(user, 'add_commissionrun')
    plan = CommissionPlan.objects.select_for_update().get(pk=plan_pk)
    if plan.status not in ('approved', 'ended'):
        raise ValidationError('Approve the plan before calculating commission.')
    if plan.runs.filter(status='draft').exists():
        raise ValidationError('A calculated run is already waiting for approval — approve or reject it first.')
    last = plan.runs.filter(status='approved').order_by('-end').first()
    start = last.end + timedelta(days=1) if last else plan.valid_from
    if plan.valid_to:
        end = min(end, plan.valid_to)
    if end < start:
        raise ValidationError(f'Nothing to calculate: the next period starts {start:%d %b %Y}.')
    if end > timezone.localdate():
        raise ValidationError('A run cannot end in the future.')
    run = CommissionRun.objects.create(number=next_number('COM', plan.site), plan=plan, start=start, end=end, created_by=user)
    total = tds = ZERO
    for person in plan.beneficiaries.all().order_by('username'):
        b = basis(plan, person, plan.valid_from, end)
        cumulative = _commission(plan, b)
        prior = CommissionLine.objects.filter(run__plan=plan, run__status='approved', person=person).aggregate(t=Sum('amount'))['t'] or ZERO
        amount = cumulative - prior
        t = (max(amount, ZERO) * plan.tds_rate / 100).quantize(CENT)
        if b or prior:
            CommissionLine.objects.create(run=run, person=person, basis_amount=b, cumulative=cumulative, prior=prior, amount=amount, tds=t)
            total += amount
            tds += t
    run.total, run.tds = total, tds
    run.save(update_fields=['total', 'tds'])
    _log(user, 'Commission calculated', run.number, f'{plan.name} · {start} – {end} · ₹{total}')
    return run


@transaction.atomic
def decide_run(user, pk, approve, note=''):
    _need(user, 'approve_commissionrun')
    run = CommissionRun.objects.select_for_update().select_related('plan').get(pk=pk)
    if run.status != 'draft':
        raise ValidationError('Only a calculated run can be decided.')
    if user.pk == run.created_by_id:
        raise ValidationError('A different user must approve the run (maker-checker).')
    if run.lines.filter(person=user).exists():
        raise ValidationError('You cannot approve your own commission.')
    if not approve and not (note or '').strip():
        raise ValidationError('Give the reason for rejecting.')
    if approve and run.total:
        from . import gl
        lines = []
        for l in run.lines.select_related('person'):
            if l.amount > 0:
                lines.append((gl.acct('commission'), l.amount, ZERO, None, f'{l.person.get_username()} {run.start}–{run.end}'))
                lines.append((gl.acct('commission_payable'), ZERO, l.amount - l.tds, None, l.person.get_username()))
                if l.tds:
                    lines.append((gl.acct('tds_payable'), ZERO, l.tds, None, f'TDS {l.person.get_username()}'))
            elif l.amount < 0:   # recovery of earlier commission after returns
                lines.append((gl.acct('commission_payable'), -l.amount, ZERO, None, f'Recovery {l.person.get_username()}'))
                lines.append((gl.acct('commission'), ZERO, -l.amount, None, l.person.get_username()))
        gl.post(user, f'com:{run.pk}', run.end, run.plan.site, f'Sales commission {run.number} · {run.plan.name}', lines)
    run.status, run.decided_by, run.decided_at, run.decision_note = ('approved' if approve else 'rejected'), user, timezone.now(), (note or '').strip()[:200]
    run.save()
    _log(user, 'Commission approved & posted' if approve else 'Commission rejected', run.number, note or f'₹{run.total}')
    return run


def statement(person):
    """Per plan: approved runs for this person (earned, recovered, TDS)."""
    rows = CommissionLine.objects.filter(person=person).select_related('run__plan').order_by('run__end')
    return [{'line': l, 'run': l.run} for l in rows]
