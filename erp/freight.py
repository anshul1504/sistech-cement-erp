"""Freight (FR-LOG-013/014): lane rate lookup, contracted freight frozen at gate-out, transporter bill check with maker-checker.

Payable per trip = min(claimed, contracted) + allowed extras (detention days × lane detention + unloading) − shortage/damage recovery.
Anything claimed above contract is shown as variance and not paid. Ex-works trips (customer arranges the truck) are never billed to Sistech.
"""
from accounts.delegation import authorize, can_approve
import hashlib
import json
from .gst import inr
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .services import require_site
from .models import Site, FreightRate, FreightBill, FreightBillLine, Dispatch, AuditEvent, Partner, clean_place

ZERO = Decimal('0.00')

def visible_bills(queryset, user):
    """A consolidated bill is visible only if every trip is in scope.

    Finance-wide access is explicit. Rejected bills have no trip lines, and
    remain visible to their creator and central finance only.
    """
    if user.is_superuser or user.has_perm('erp.access_company_finance'):
        return queryset
    forbidden = Site.objects.exclude(siteaccess__user=user)
    return queryset.exclude(lines__dispatch__site__in=forbidden).filter(Q(lines__isnull=False) | Q(created_by=user)).distinct()



def can_decide(user,bill):
    return (user.has_perm('erp.view_freightbill') and bill.status=='submitted' and bill.created_by_id!=user.pk
        and visible_bills(FreightBill.objects.filter(pk=bill.pk),user).exists()
        and can_approve(user,'approve_freightbill',makers=[bill.created_by_id]))



def review_state(bill,ignore_recovery=False):
    """Current financial/POD evidence only; no file bytes or bank data in the fingerprint."""
    rows=[];issues=[]
    for line in bill.lines.select_related('dispatch__freight_rate','dispatch__invoice').order_by('pk'):
        trip=line.dispatch
        invoice=getattr(trip,'invoice',None)
        invoice_quantity=sum((row.quantity for row in invoice.lines.all()),Decimal(0)) if invoice else Decimal(0)
        recovery=shortage_value(trip)
        extra=allowed_extra(trip,line.detention_days,line.extra)
        if trip.transporter_id!=bill.transporter_id or trip.freight_payable_by!='company' or trip.status not in ['gate_out','delivered']:
            issues.append(f'{trip.number}: this trip is no longer billable on this claim.')
        if trip.freight_amount!=line.contracted:issues.append(f'{trip.number}: contracted freight changed after entry.')
        if recovery!=line.shortage_recovery and not ignore_recovery:
            issues.append(f'{trip.number}: current POD recovery is ₹{inr(recovery,2)}; this claim records ₹{inr(line.shortage_recovery,2)}.')
        if (trip.shortage_qty or trip.damaged_qty) and (not invoice or invoice_quantity<=0):
            issues.append(f'{trip.number}: shortage recovery requires linked invoice quantities.')
        if extra!=line.allowed_extra:issues.append(f'{trip.number}: allowed extra-charge evidence changed after entry.')
        rows.append(dict(line=line.pk,trip=trip.pk,status=trip.status,transporter=trip.transporter_id,payer=trip.freight_payable_by,
            invoice_id=invoice.pk if invoice else None,invoice_quantity=str(invoice_quantity),invoice_taxable=str(invoice.taxable) if invoice else None,
            freight_rate_id=trip.freight_rate_id,contract=str(trip.freight_amount),claimed=str(line.claimed),extra=str(line.extra),days=line.detention_days,reason=line.extra_reason,
            recorded_contract=str(line.contracted),recorded_extra=str(line.allowed_extra),recorded_recovery=str(line.shortage_recovery),
            current_extra=str(extra),current_recovery=str(recovery),shortage=str(trip.shortage_qty),damaged=str(trip.damaged_qty),
            delivered_at=str(trip.delivered_at),receiver=trip.received_by,pod_note=trip.pod_notes))
    lines=list(bill.lines.all())
    expected=dict(contracted=sum((l.contracted for l in lines),ZERO),claimed=sum((l.claimed for l in lines),ZERO),
        extras=sum((l.extra for l in lines),ZERO),deductions=sum((l.shortage_recovery for l in lines),ZERO),
        payable=max(sum((l.payable for l in lines),ZERO)-bill.advance,ZERO))
    if any(getattr(bill,key)!=amount for key,amount in expected.items()):issues.append('The bill totals no longer match its retained trip calculations.')
    state=dict(bill=bill.pk,status=bill.status,advance=str(bill.advance),totals={key:str(getattr(bill,key)) for key in expected},trips=rows)
    return state,issues


def review_token(bill):
    state,_=review_state(bill)
    return hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()


def decision_evidence(bill):
    return dict(bill_number=bill.bill_number,transporter=bill.transporter_id,bill_date=str(bill.bill_date),
        contracted=str(bill.contracted),claimed=str(bill.claimed),extras=str(bill.extras),deductions=str(bill.deductions),
        advance=str(bill.advance),payable=str(bill.payable),
        trips=[dict(dispatch_id=line.dispatch_id,number=line.dispatch.number,site_id=line.dispatch.site_id,
            vehicle=line.dispatch.vehicle.registration,customer=line.dispatch.order.partner.name,net_kg=str(line.dispatch.net_kg),
            contracted=str(line.contracted),claimed=str(line.claimed),extra=str(line.extra),allowed_extra=str(line.allowed_extra),
            shortage_recovery=str(line.shortage_recovery),payable=str(line.payable),detention_days=line.detention_days,reason=line.extra_reason)
            for line in bill.lines.select_related('dispatch__vehicle','dispatch__order__partner').order_by('pk')])


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


def _amount(value,label,model=FreightBillLine,field='claimed'):
    definition=model._meta.get_field(field)
    try:
        return forms.DecimalField(max_digits=definition.max_digits,decimal_places=definition.decimal_places,min_value=ZERO).clean(value).quantize(Decimal('0.01'))
    except ValidationError as error:
        raise ValidationError([f'{label}: {message}' for message in error.messages]) from None


def _days(value):
    try:
        if isinstance(value,bool):raise ValidationError('Enter a whole number.')
        return forms.IntegerField(min_value=0,max_value=32767).clean(0 if value in (None,'') else value)
    except ValidationError as error:
        raise ValidationError([f'Detention days: {message}' for message in error.messages]) from None


@transaction.atomic
def create_bill(user, transporter, bill_number, bill_date, rows, advance=0, notes=''):
    """Validate amounts and lock authoritative trips; passed model instances supply IDs only."""
    if not user.has_perm('erp.add_freightbill'):raise PermissionDenied
    transporter=Partner.objects.select_for_update(no_key=True).get(pk=transporter.pk)
    if transporter.kind!='Transporter':raise ValidationError('Choose a transporter.')
    bill_number=forms.CharField(max_length=40).clean(bill_number)
    bill_date=forms.DateField().clean(bill_date)
    if bill_date>timezone.localdate():raise ValidationError('Bill date cannot be in the future.')
    advance=_amount(0 if advance in (None,'') else advance,'Advance',FreightBill,'advance')
    if FreightBill.objects.filter(transporter=transporter,bill_number__iexact=bill_number).exists():
        raise ValidationError(f'Bill {bill_number} from {transporter.name} is already entered. Use a distinct corrected reference for a rejected claim.')
    if not rows:raise ValidationError('Tick at least one trip.')
    selected=list(rows.items())
    identifiers=[getattr(dispatch,'pk',None) for dispatch,row in selected]
    if None in identifiers:raise ValidationError('Select existing dispatched trips.')
    locked={dispatch.pk:dispatch for dispatch in Dispatch.objects.select_for_update(of=('self',)).filter(pk__in=identifiers).select_related('site','transporter','freight_rate').order_by('pk')}
    billed=set(FreightBillLine.objects.filter(dispatch_id__in=identifiers).values_list('dispatch_id',flat=True))
    clean=[]
    for supplied,row in selected:
        dispatch=locked.get(supplied.pk)
        if dispatch is None:raise ValidationError('A selected trip no longer exists.')
        require_site(user,dispatch.site)
        if dispatch.transporter_id!=transporter.pk or dispatch.status not in ['gate_out','delivered'] or dispatch.freight_payable_by!='company' or dispatch.pk in billed:
            raise ValidationError(f'{dispatch.number} is not billable for {transporter.name}.')
        if dispatch.freight_amount is None:
            raise ValidationError(f'{dispatch.number}: no freight rate for {dispatch.site.code} → {destination_of(dispatch) or "district not set"}. Add the lane rate first.')
        contracted=_amount(dispatch.freight_amount,'Contracted freight',field='contracted')
        claimed=_amount(row.get('claimed',0),'Claimed freight')
        extra=_amount(row.get('extra',0),'Extra charge',field='extra')
        days=_days(row.get('days',0));reason=(row.get('reason') or '').strip()
        if extra and not reason:raise ValidationError(f'{dispatch.number}: give the reason for the extra charge.')
        clean.append((dispatch,contracted,claimed,extra,days,reason))
    bill=FreightBill.objects.create(transporter=transporter,bill_number=bill_number,bill_date=bill_date,advance=advance,notes=(notes or '')[:200],created_by=user)
    for dispatch,contracted,claimed,extra,days,reason in clean:
        FreightBillLine.objects.create(bill=bill,dispatch=dispatch,contracted=contracted,claimed=claimed,detention_days=days,extra=extra,
            extra_reason=reason[:120],shortage_recovery=shortage_value(dispatch),allowed_extra=allowed_extra(dispatch,days,extra))
    recompute(bill)
    AuditEvent.objects.create(actor=user,action='Freight bill entered',reference=f'freight:{bill.pk}',detail=f'{transporter.name} · {bill_number} · ₹{bill.claimed+bill.extras}')
    return bill


def recompute(bill):
    lines = list(bill.lines.all())
    bill.contracted = sum((l.contracted for l in lines), ZERO)
    bill.claimed = sum((l.claimed for l in lines), ZERO)
    bill.extras = sum((l.extra for l in lines), ZERO)
    bill.deductions = sum((l.shortage_recovery for l in lines), ZERO)
    payable = sum((l.payable for l in lines), ZERO) - bill.advance
    bill.payable = max(payable, ZERO)
    for field in ['contracted','claimed','extras','deductions','advance','payable']:
        setattr(bill,field,_amount(getattr(bill,field),field.replace('_',' ').capitalize(),FreightBill,field))
    bill.save()


@transaction.atomic
def decide(user, pk, approve, note, expected_evidence=None):
    if not user.has_perm('erp.view_freightbill'):
        raise PermissionDenied
    bill = FreightBill.objects.select_for_update().get(pk=pk)
    if not user.has_perm('erp.access_company_finance'):
        for line in bill.lines.select_related('dispatch__site'):
            require_site(user, line.dispatch.site)
    if bill.status != 'submitted':
        raise ValidationError('This bill has already been decided.')
    if bill.created_by_id == user.pk:
        raise ValidationError('The person who entered the bill cannot approve or reject it (maker-checker).')
    authorize(user,'approve_freightbill',makers=[bill.created_by_id],reference=f'freight:{bill.pk}')
    if not bill.lines.exists():raise ValidationError('A submitted freight bill needs retained trip lines before review.')
    trip_ids=list(bill.lines.values_list('dispatch_id',flat=True))
    list(Dispatch.objects.select_for_update().filter(pk__in=trip_ids).order_by('pk'))
    state,issues=review_state(bill)
    current=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
    if expected_evidence is not None and expected_evidence!=current:
        raise ValidationError('Trip or POD evidence changed or is missing. Reopen the bill and review its current evidence.')
    if approve and issues:
        raise ValidationError([*issues,'Reject this pending claim and enter a corrected bill using the current delivery evidence.'])
    note = (note or '').strip()
    if not approve and not note:
        raise ValidationError('Give the reason for rejecting.')
    if approve and bill.variance > 0 and not note:
        raise ValidationError(f'Claim is ₹{inr(bill.variance, 2)} above contract — note why the payable is acceptable.')
    bill.review_snapshot = {**decision_evidence(bill),'source_evidence':state,'evidence_digest':current}
    bill.status, bill.checked_by, bill.checked_at, bill.check_note = ('approved' if approve else 'rejected'), user, timezone.now(), note[:200]
    bill.save()
    if not approve:
        bill.lines.all().delete()  # trips become billable again on a corrected bill
    else:
        from .gl import post_freight_bill
        post_freight_bill(user, bill)
    AuditEvent.objects.create(actor=user, action='Freight bill approved' if approve else 'Freight bill rejected', reference=f'freight:{bill.pk}',
                              detail=f'{bill.transporter.name} · {bill.bill_number} · payable ₹{bill.payable}' + (f' · {note}' if note else ''))
    return bill
