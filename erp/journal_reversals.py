"""Independent approval workflow for original posted manual-journal corrections."""
import uuid
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from accounts.delegation import authorize,can_approve
from accounts.security import access_allowed
from .finance_access import permitted,require_company_finance
from .models import JournalEntry,JournalReversalRequest,AuditEvent
from . import gl


def _read(user):
    if not access_allowed(user):raise PermissionDenied
    require_company_finance(user);permitted(user,'view_journalentry')


def _source(entry):
    if entry.kind!='manual' or entry.source or entry.reversal_of_id or entry.status!='posted':
        raise ValidationError('Request corrections only for original posted manual vouchers; system postings use their source document.')
    if hasattr(entry,'reversed_by'):raise ValidationError('This voucher already has a linked reversal.')


def makers(request):return {request.created_by_id,request.entry.created_by_id}


def can_request(user,entry):
    return (access_allowed(user) and user.has_perm('erp.access_company_finance') and user.has_perm('erp.view_journalentry') and user.has_perm('erp.add_journalentry')
        and entry.kind=='manual' and not entry.source and not entry.reversal_of_id and entry.status=='posted'
        and not hasattr(entry,'reversed_by') and not entry.reversal_requests.filter(status__in=['requested','approved']).exists())


def can_decide(user,request):
    return (access_allowed(user) and request.status=='requested' and user.has_perm('erp.access_company_finance') and user.has_perm('erp.view_journalentry')
        and user.pk not in makers(request) and can_approve(user,'approve_journalentry',makers=makers(request)))


def _audit(user,request,action,detail):
    AuditEvent.objects.create(actor=user,site=request.entry.site,action=action,reference=request.entry.number,detail=f'Request {request.pk} · {detail}'[:250])


@transaction.atomic
def propose(user,entry_pk,on,reason,key):
    _read(user);permitted(user,'add_journalentry')
    entry=JournalEntry.objects.select_for_update().get(pk=entry_pk)
    reason=(reason or '').strip()
    if not reason or len(reason)>300:raise ValidationError('Record a reversal reason of up to 300 characters.')
    try:key=uuid.UUID(str(key))
    except (TypeError,ValueError,AttributeError):raise ValidationError('Open a fresh reversal request form.') from None
    on=on or timezone.localdate()
    old=JournalReversalRequest.objects.filter(request_key=key).first()
    if old:
        if (old.entry_id,old.created_by_id,old.on,old.reason)!=(entry.pk,user.pk,on,reason):
            raise ValidationError('This request key belongs to different reversal details.')
        return old
    _source(entry)
    if on<entry.date:raise ValidationError('A reversal cannot precede the original voucher date.')
    gl.check_open(on)
    if entry.reversal_requests.filter(status__in=['requested','approved']).exists():raise ValidationError('This voucher already has a pending/approved reversal request.')
    request=JournalReversalRequest(entry=entry,on=on,reason=reason,created_by=user,request_key=key)
    request.full_clean();request.save();_audit(user,request,'Journal reversal requested',f'{on} · {reason}')
    return request


@transaction.atomic
def decide(user,pk,approve,note):
    _read(user)
    header=JournalReversalRequest.objects.only('entry_id').get(pk=pk)
    entry=JournalEntry.objects.select_for_update().get(pk=header.entry_id)
    request=JournalReversalRequest.objects.select_for_update().select_related('entry').get(pk=pk)
    if request.status!='requested':raise ValidationError('This reversal request has already been decided.')
    if user.pk in makers(request):raise ValidationError('The requester and original voucher maker cannot approve/reject this reversal (maker-checker).')
    authorize(user,'approve_journalentry',makers=makers(request),reference=f'REVREQ-{request.pk:05d}')
    note=(note or '').strip()
    if not note or len(note)>300:raise ValidationError('Record an independent review finding of up to 300 characters.')
    if approve:
        _source(entry)
        reversal=gl._reverse_entry(user,entry,request.on,request.reason)
        reversal.created_by=request.created_by;reversal.approved_by=user;reversal.save(update_fields=['created_by','approved_by'])
        request.reversal=reversal
    request.status='approved' if approve else 'rejected';request.reviewed_by=user;request.reviewed_at=timezone.now();request.review_note=note
    request.full_clean();request.save();_audit(user,request,'Journal reversal '+request.status,note)
    return request


@transaction.atomic
def cancel(user,pk,note):
    _read(user)
    header=JournalReversalRequest.objects.only('entry_id').get(pk=pk)
    JournalEntry.objects.select_for_update().get(pk=header.entry_id)
    request=JournalReversalRequest.objects.select_for_update().select_related('entry').get(pk=pk)
    if request.created_by_id!=user.pk and not user.is_superuser:raise PermissionDenied
    if request.status!='requested' or not (note or '').strip():raise ValidationError('Cancel a pending request with a reason.')
    request.status='cancelled';request.reviewed_by=user;request.reviewed_at=timezone.now();request.review_note=note.strip()[:300];request.save()
    _audit(user,request,'Journal reversal cancelled',note)
    return request
