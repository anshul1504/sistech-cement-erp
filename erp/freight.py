"""Freight (FR-LOG-013/014): lane rate lookup, contracted freight frozen at gate-out, transporter bill check with maker-checker.

Payable per trip = min(claimed, contracted) + allowed extras (detention days × lane detention + unloading) − shortage/damage recovery.
Anything claimed above contract is shown as variance and not paid. Ex-works trips (customer arranges the truck) are never billed to Sistech.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import FreightRate, FreightBill, FreightBillLine, Dispatch, AuditEvent, clean_place

ZERO = Decimal('0.00')


def destination_of(dispatch):
    return clean_place(dispatch.order.partner.district)


def rate_for(dispatch, on=None):
    on = on or timezone.localdate()
    district = destination_of(dispatch)
    if not district:
        return None
    qs = FreightRate.objects.filter(origin=dispatch.site, destination__iexact=district, active=True, valid_from__lte=on) \
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on)) \
        .filter(Q(transporter__isnull=True) | Q(transporter=dispatch.transporter_id)) \
        .filter(Q(vehicle_type='') | Q(vehicle_type=dispatch.vehicle.vehicle_type))
    rates = sorted(qs, key=lambda r: (r.specificity, r.valid_from, r.pk), reverse=True)
    return rates[0] if rates else None


def payer(dispatch):
    if dispatch.order.freight_terms == 'ex_works' or dispatch.vehicle.owner == 'customer':
        return 'customer'
    return 'company'


def snapshot(dispatch):
    """Called at gate-out: freeze who pays and the contracted amount for this trip's net weight."""
    dispatch.freight_payable_by = payer(dispatch)
    if dispatch.freight_payable_by == 'company':
        rate = rate_for(dispatch)
        dispatch.freight_rate = rate
        dispatch.freight_amount = rate.amount_for(dispatch.net_kg) if rate else None
    dispatch.save(update_fields=['freight_payable_by', 'freight_rate', 'freight_amount'])


def billable_trips(transporter):
    return Dispatch.objects.filter(transporter=transporter, status__in=['gate_out', 'delivered'], freight_payable_by='company', freight_line__isnull=True) \
        .select_related('order__partner', 'vehicle', 'freight_rate', 'invoice').order_by('gate_out_at')


def shortage_value(dispatch):
    units = (dispatch.shortage_qty or 0) + (dispatch.damaged_qty or 0)
    if not units:
        return ZERO
    inv = getattr(dispatch, 'invoice', None)
    qty = sum((l.quantity for l in inv.lines.all()), Decimal('0')) if inv else Decimal('0')
    if not inv or not qty:
        return ZERO
    return (inv.taxable / qty * units).quantize(Decimal('0.01'))


def allowed_extra(dispatch, days, claimed_extra):
    r = dispatch.freight_rate
    if not r or not claimed_extra:
        return ZERO
    cap = r.detention_per_day * days + r.unloading_charge
    return min(Decimal(claimed_extra), cap)


@transaction.atomic
def create_bill(user, transporter, bill_number, bill_date, rows, advance=0, notes=''):
    """rows: {Dispatch: {'claimed': x, 'days': n, 'extra': y, 'reason': s}}"""
    if not user.has_perm('erp.add_freightbill'):
        raise PermissionDenied
    bill_number = (bill_number or '').strip()
    problems = []
    if not bill_number: problems.append("Enter the transporter's bill number.")
    if FreightBill.objects.filter(transporter=transporter, bill_number__iexact=bill_number).exclude(status='rejected').exists():
        problems.append(f'Bill {bill_number} from {transporter.name} is already entered.')
    if bill_date and bill_date > timezone.localdate(): problems.append('Bill date cannot be in the future.')
    open_trips = {d.pk: d for d in billable_trips(transporter)}
    if not rows: problems.append('Tick at least one trip.')
    for d, row in rows.items():
        if d.pk not in open_trips: problems.append(f'{d.number} is not billable for {transporter.name}.')
        if row['claimed'] < 0 or row['extra'] < 0: problems.append(f'{d.number}: amounts cannot be negative.')
        if row['extra'] and not row['reason'].strip(): problems.append(f'{d.number}: give the reason for the extra charge.')
        if d.freight_amount is None: problems.append(f'{d.number}: no freight rate for {d.site.code} → {destination_of(d) or "district not set"}. Add the lane rate first.')
    if Decimal(advance or 0) < 0: problems.append('Advance cannot be negative.')
    if problems:
        raise ValidationError(problems)
    bill = FreightBill.objects.create(transporter=transporter, bill_number=bill_number, bill_date=bill_date, advance=Decimal(advance or 0), notes=notes[:200], created_by=user)
    for d, row in rows.items():
        FreightBillLine.objects.create(bill=bill, dispatch=d, contracted=d.freight_amount, claimed=row['claimed'], detention_days=row['days'], extra=row['extra'],
                                       extra_reason=row['reason'][:120], shortage_recovery=shortage_value(d), allowed_extra=allowed_extra(d, row['days'], row['extra']))
    recompute(bill)
    AuditEvent.objects.create(actor=user, action='Freight bill entered', reference=f'freight:{bill.pk}', detail=f'{transporter.name} · {bill_number} · ₹{bill.claimed + bill.extras}')
    return bill


def recompute(bill):
    lines = list(bill.lines.all())
    bill.contracted = sum((l.contracted for l in lines), ZERO)
    bill.claimed = sum((l.claimed for l in lines), ZERO)
    bill.extras = sum((l.extra for l in lines), ZERO)
    bill.deductions = sum((l.shortage_recovery for l in lines), ZERO)
    payable = sum((l.payable for l in lines), ZERO) - bill.advance
    bill.payable = max(payable, ZERO)
    bill.save()


@transaction.atomic
def decide(user, pk, approve, note):
    if not user.has_perm('erp.approve_freightbill'):
        raise PermissionDenied
    bill = FreightBill.objects.select_for_update().get(pk=pk)
    if bill.status != 'submitted':
        raise ValidationError('This bill has already been decided.')
    if bill.created_by_id == user.pk:
        raise ValidationError('The person who entered the bill cannot approve or reject it (maker-checker).')
    note = (note or '').strip()
    if not approve and not note:
        raise ValidationError('Give the reason for rejecting.')
    if approve and bill.variance > 0 and not note:
        raise ValidationError(f'Claim is ₹{bill.variance:,.2f} above contract — note why the payable is acceptable.')
    bill.status, bill.checked_by, bill.checked_at, bill.check_note = ('approved' if approve else 'rejected'), user, timezone.now(), note[:200]
    bill.save()
    if not approve:
        bill.lines.all().delete()  # trips become billable again on a corrected bill
    AuditEvent.objects.create(actor=user, action='Freight bill approved' if approve else 'Freight bill rejected', reference=f'freight:{bill.pk}',
                              detail=f'{bill.transporter.name} · {bill.bill_number} · payable ₹{bill.payable}' + (f' · {note}' if note else ''))
    return bill
