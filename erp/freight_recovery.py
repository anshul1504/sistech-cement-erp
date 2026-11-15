"""Reviewed post-approval recovery; original freight claims and paid allocations stay retained."""
import hashlib,json,uuid
from decimal import Decimal
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from accounts.delegation import authorize,can_approve
from accounts.security import access_allowed
from .finance_access import permitted,require_company_finance
from .models import FreightBill,FreightRecoveryAdjustment,Partner,Dispatch,PaymentAllocation,AuditEvent,PODCorrection
from . import freight as F,gl
ZERO=Decimal('0.00')


def _read(user):
    if not access_allowed(user):raise PermissionDenied
    require_company_finance(user);permitted(user,'view_freightbill')


def effective_payable(bill):
    reduction=bill.recovery_adjustments.filter(status='approved').aggregate(total=Sum('amount'))['total'] or ZERO
    return bill.payable-reduction


def _lock(bill_id):
    header=FreightBill.objects.only('transporter_id').get(pk=bill_id)
    Partner.objects.select_for_update(no_key=True).get(pk=header.transporter_id)
    bill=FreightBill.objects.select_for_update().get(pk=bill_id)
    list(Dispatch.objects.select_for_update().filter(freight_line__bill=bill).order_by('pk'))
    return bill


def basis(bill):
    if bill.status!='approved':raise ValidationError('Recover only against an approved freight bill.')
    state,issues=F.review_state(bill,ignore_recovery=True)
    if issues:raise ValidationError(issues)
    if not state['trips']:raise ValidationError('Recovery requires retained bill trip lines.')
    approved=list(bill.recovery_adjustments.filter(status='approved').values('pk','amount','recovery_total','payable_after'))
    baseline=approved[0]['recovery_total'] if approved else bill.deductions
    current=sum((Decimal(row['current_recovery']) for row in state['trips']),ZERO)
    fixed=sum((min(line.claimed,line.contracted)+line.allowed_extra for line in bill.lines.all()),ZERO)-bill.advance
    target=max(fixed-current,ZERO)
    before=effective_payable(bill)
    evidence=dict(source=state,approved=[{key:str(value) for key,value in row.items()} for row in approved],baseline=str(baseline),current=str(current),before=str(before),after=str(target))
    digest=hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()
    return dict(evidence=evidence,digest=digest,recovery_total=current,payable_after=target,amount=before-target,changed=current!=baseline)


def clear_for_payment(bill):
    try:
        current=basis(bill)
        return not current['changed'] and not bill.recovery_adjustments.filter(status='requested').exists()
    except ValidationError:return False


def makers(request):return {request.created_by_id,request.bill.created_by_id}


def can_decide(user,request):
    return (access_allowed(user) and request.status=='requested' and user.has_perm('erp.access_company_finance') and user.has_perm('erp.view_freightbill')
        and user.pk not in makers(request) and can_approve(user,'approve_freightbill',makers=makers(request)))


def _log(user,request,action,note):
    AuditEvent.objects.create(actor=user,action=action,reference=f'freight:{request.bill_id}',detail=f'Recovery {request.pk} · {note}'[:250])


def check_date(bill,on):
    prior_dates=list(bill.recovery_adjustments.filter(status='approved').values_list('on',flat=True))
    corrections=PODCorrection.objects.filter(dispatch__freight_line__bill=bill,status='approved')
    prior_dates.extend(timezone.localdate(row.reviewed_at) for row in corrections)
    latest_pod=max([bill.bill_date,*prior_dates,*[timezone.localdate(trip.delivered_at) for trip in Dispatch.objects.filter(freight_line__bill=bill,delivered_at__isnull=False)]])
    if on<latest_pod or on>timezone.localdate():raise ValidationError('Use a recovery date from the original bill/latest POD or approved correction through today.')



@transaction.atomic
def propose(user,bill_id,on,reason,key,expected_digest=None):
    _read(user);permitted(user,'add_freightbill')
    bill=_lock(bill_id);reason=(reason or '').strip();on=on or timezone.localdate()
    if not reason or len(reason)>300:raise ValidationError('Record a recovery reason of up to 300 characters.')
    try:key=uuid.UUID(str(key))
    except (ValueError,TypeError,AttributeError):raise ValidationError('Open a fresh recovery request form.') from None
    old=FreightRecoveryAdjustment.objects.filter(request_key=key).first()
    if old:
        if (old.bill_id,old.created_by_id,old.on,old.reason)!=(bill.pk,user.pk,on,reason):raise ValidationError('This request key belongs to different recovery details.')
        return old
    current=basis(bill)
    if expected_digest is not None and expected_digest!=current['digest']:raise ValidationError('Recovery evidence changed. Reopen the bill and review the current POD.')
    if not current['changed']:raise ValidationError('No new recovery remains to reconcile.')
    if bill.recovery_adjustments.filter(status='requested').exists():raise ValidationError('A recovery request is already pending for this bill.')
    check_date(bill,on)
    gl.check_open(on)
    request=FreightRecoveryAdjustment(bill=bill,on=on,reason=reason,created_by=user,request_key=key,
        amount=current['amount'],recovery_total=current['recovery_total'],payable_after=current['payable_after'],evidence=current['evidence'],source_digest=current['digest'])
    request.full_clean();request.save();_log(user,request,'Freight recovery requested',reason)
    return request


@transaction.atomic
def decide(user,pk,approve,note):
    _read(user)
    header=FreightRecoveryAdjustment.objects.only('bill_id').get(pk=pk);bill=_lock(header.bill_id)
    request=FreightRecoveryAdjustment.objects.select_for_update().select_related('bill').get(pk=pk)
    if request.status!='requested':raise ValidationError('This recovery request has already been decided.')
    if user.pk in makers(request):raise ValidationError('The request and original bill makers cannot decide this recovery (maker-checker).')
    authorize(user,'approve_freightbill',makers=makers(request),reference=f'FRREC-{request.pk:05d}')
    note=(note or '').strip()
    if not note or len(note)>300:raise ValidationError('Record an independent review finding of up to 300 characters.')
    if approve:
        current=basis(bill)
        if current['digest']!=request.source_digest:raise ValidationError('POD or recovery evidence changed. Reject and prepare a fresh request.')
        if PaymentAllocation.objects.filter(freight_bill=bill,payment__status__in=['proposed','approved']).exists():
            raise ValidationError('Cancel/review the existing pending payment allocations before approving recovery; allocations are never silently changed.')
        check_date(bill,request.on)
        gl.check_open(request.on)
        if request.amount:
            request.journal=gl.post(user,f'freight-recovery:{request.pk}',request.on,None,f'Freight recovery · {bill.bill_number}',
                [(gl.acct('ap_freight'),max(request.amount,ZERO),max(-request.amount,ZERO),bill.transporter,request.reason),(gl.acct('freight_out'),max(-request.amount,ZERO),max(request.amount,ZERO),None,request.reason)])
    request.status='approved' if approve else 'rejected';request.reviewed_by=user;request.reviewed_at=timezone.now();request.review_note=note
    request.full_clean();request.save();_log(user,request,'Freight recovery '+request.status,note)
    return request


@transaction.atomic
def cancel(user,pk,note):
    _read(user)
    header=FreightRecoveryAdjustment.objects.only('bill_id').get(pk=pk);_lock(header.bill_id)
    request=FreightRecoveryAdjustment.objects.select_for_update().get(pk=pk)
    if user.pk!=request.created_by_id and not user.is_superuser:raise PermissionDenied
    if request.status!='requested' or not (note or '').strip():raise ValidationError('Cancel a pending recovery with a reason.')
    request.status='cancelled';request.reviewed_by=user;request.reviewed_at=timezone.now();request.review_note=note.strip()[:300];request.save()
    _log(user,request,'Freight recovery cancelled',note);return request


def check_payment(payment):
    """Caller holds the payee mutex; ordered bill/trip locks freeze POD through release."""
    ids=list(payment.allocations.exclude(freight_bill=None).values_list('freight_bill_id',flat=True))
    for bill in FreightBill.objects.select_for_update().filter(pk__in=ids).order_by('pk'):
        list(Dispatch.objects.select_for_update().filter(freight_line__bill=bill).order_by('pk'))
        if bill.transporter_id!=payment.payee_id:raise ValidationError('A freight allocation belongs to another payee.')
        if not clear_for_payment(bill):raise ValidationError(f'{bill.bill_number}: reconcile the changed POD/recovery before payment approval or release.')
        paid=PaymentAllocation.objects.filter(freight_bill=bill,payment__status='paid').aggregate(total=Sum('amount'))['total'] or ZERO
        reserved_credit=-(PaymentAllocation.objects.filter(freight_bill=bill,payment__status__in=['proposed','approved'],amount__lt=0).aggregate(total=Sum('amount'))['total'] or ZERO)
        if reserved_credit>max(paid-effective_payable(bill),ZERO):raise ValidationError(f'{bill.bill_number}: the reserved freight credit is no longer backed by paid allocations. Cancel and reallocate.')
        live=PaymentAllocation.objects.filter(freight_bill=bill,payment__status__in=['proposed','approved','paid']).aggregate(total=Sum('amount'))['total'] or ZERO
        if live>effective_payable(bill):raise ValidationError(f'{bill.bill_number}: payment reservations exceed the adjusted payable. Cancel and propose corrected allocations.')
