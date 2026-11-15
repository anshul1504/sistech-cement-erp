"""Retained ownership/approval routing, without role grants or posted-document changes."""
import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

from erp import models as M
from . import approval_work as AW
from .models import ApprovalRouting, WorkHandoffRun, WorkHandoffItem, UserProfile, WorkHandoffGuard
from .security import access_allowed


OWNER_WORK = {
    'support': (M.SupportTicket,'assigned_to',{'status__in':['open','progress','waiting']},'',('handle_supportticket',)),
    'complaint': (M.Complaint,'owner',{'status__in':['open','investigating','decided']},'invoice.site',('view_complaint','change_complaint')),
    'capa': (M.CapaAction,'owner',{'status__in':['open','reopened']},'complaint.invoice.site',('view_complaint','change_capaaction')),
    'project': (M.SalesProject,'owner',{},'site',('view_salesproject','change_salesproject')),
    'collection': (M.CollectionFollowUp,'owner',{'resolved':False},'',('view_receipt','add_receipt','access_company_finance')),
    'crm': (M.CRMLead,'owner',{},'site',('view_crmlead','change_crmlead')),
    'beat': (M.BeatPlan,'owner',{'status__in':['draft','submitted','approved','finished']},'site',('view_beatplan','record_beatstop')),
}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,cls=DjangoJSONEncoder,separators=(',',':')).encode()).hexdigest()


def object_digest(obj):
    # Retain only the digest, never duplicate bank details, descriptions or uploaded files.
    return digest({f.attname:getattr(obj,f.attname) for f in obj._meta.concrete_fields
                   if f.get_internal_type() != 'BinaryField'})


def _admin(actor):
    if not actor or not actor.is_superuser or not access_allowed(actor):
        raise PermissionDenied


def _key(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError,ValueError,AttributeError):
        raise ValidationError('Open a fresh handoff form; its request key is invalid.') from None


def _note(value):
    value=(value or '').strip()
    if not value or len(value)>250:
        raise ValidationError('Give a reason of at most 250 characters.')
    return value


def _owner_pending(kind,obj):
    if kind == 'beat':
        from erp.beat_cover import _outstanding, executes
        if obj.status == 'finished' and not _outstanding(obj):return False
        for cover in obj.execution_covers.filter(status='approved').select_related('recipient'):
            if executes(cover.recipient,obj):
                try:
                    from erp.beats import _owner
                    _owner(cover.recipient,obj.site)
                except (ValidationError,PermissionDenied):
                    continue
                return False
    if kind == 'project':
        return obj.stage not in {'won','lost'}
    if kind == 'crm':
        return obj.stage not in {'won','lost'}
    if kind == 'collection':
        from erp.collect import promise_status
        return (obj.kind=='promise' and promise_status(obj)!='kept' or obj.kind=='dispute'
                or obj.next_action_on is not None)
    return True


def _owner_eligible(kind,obj,recipient,site):
    if recipient is None:
        return False,'No work backup is configured.'
    if not access_allowed(recipient):
        return False,'The recipient is disabled or their temporary access expired.'
    if any(not recipient.has_perm('erp.'+p) for p in OWNER_WORK[kind][4]):
        return False,'The recipient needs the existing module/action permissions.'
    if site is not None and not (recipient.is_superuser or M.SiteAccess.objects.filter(user=recipient,site=site).exists()):
        return False,'The recipient needs existing access to this site.'
    if kind == 'crm':
        return False,'CRM ownership is part of submitted KYC evidence; use a reviewed CRM ownership change.'
    if kind == 'beat':
        return False,'Beat ownership is tied to dated dealer assignments and approved route evidence; review the route in its module.'
    return True,''


def inventory(source_user,recipient):
    """Current assigned work only; no inferred historical approval owner is invented."""
    rows=[]
    recipient=get_user_model().objects.get(pk=recipient.pk) if recipient else None
    for kind,(model,field,filters,site_path,_) in OWNER_WORK.items():
        qs=model.objects.filter(**{field:source_user},**filters).order_by('pk')
        for obj in qs:
            if not _owner_pending(kind,obj):
                continue
            site=AW.path(obj,site_path) if site_path else None
            allowed,reason=_owner_eligible(kind,obj,recipient,site)
            rows.append(dict(kind=kind,object_id=obj.pk,reference=(getattr(obj,'number',None) or str(obj))[:160],
                site_id=site.pk if site else None,eligible=allowed,reason=reason,
                status=getattr(obj,'status',getattr(obj,'stage','Open')),fingerprint=object_digest(obj),
                _object=obj,_owner_field=field))
    for routing in ApprovalRouting.objects.filter(assignee=source_user).order_by('kind','object_id'):
        task=AW.source(routing.kind,routing.object_id)
        if task is None:
            continue
        allowed,reason=AW.eligible(recipient,task) if recipient else (False,'No work backup is configured.')
        rows.append(dict(kind='approval',object_id=routing.pk,reference=task['reference'],
            site_id=task['site'].pk if task['site'] else None,eligible=allowed,reason=reason,status=task['status'],
            fingerprint=digest(dict(route=object_digest(routing),source=object_digest(task['object']),task=AW.public(task))),
            _object=task['object'],_routing=routing))
    return sorted(rows,key=lambda r:(r['kind'],r['object_id']))


def public(row):
    return {k:v for k,v in row.items() if not k.startswith('_')}


def preview(source_user,recipient):
    rows=inventory(source_user,recipient)
    return rows,digest(dict(source=source_user.pk,recipient=recipient.pk if recipient else None,
                           rows=[public(row) for row in rows]))


def _run(key,payload,**fields):
    run,created=WorkHandoffRun.objects.get_or_create(request_key=_key(key),
        defaults=dict(payload_digest=digest(payload),**fields))
    if not created and run.payload_digest!=digest(payload):
        raise ValidationError('This request key was already used for different handoff details.')
    return run,created


def _save_item(run,row,source_user,recipient,moved,reason):
    return WorkHandoffItem.objects.create(run=run,kind=row['kind'],object_id=row['object_id'],
        reference=row['reference'],site_id=row['site_id'],source_user=source_user,
        recipient=recipient if moved else None,outcome='moved' if moved else 'blocked',note=reason[:250],snapshot=public(row))


def _finish(run):
    run.status='partial' if run.transferred and run.blocked else 'complete' if run.transferred else 'attention' if run.blocked else 'empty'
    run.save(update_fields=['status','transferred','blocked'])
    M.AuditEvent.objects.create(actor=run.actor,action='Work handoff recorded',reference=str(run),
        detail=f'From {run.source_user_id} to {run.recipient_id}; transferred {run.transferred}; attention {run.blocked}')
    return run


@transaction.atomic
def assign_approval(actor,kind,object_id,recipient,reason,request_key):
    _admin(actor)
    reason=_note(reason)
    payload=dict(kind=kind,object_id=object_id,recipient=recipient.pk,reason=reason)
    previous=WorkHandoffRun.objects.filter(request_key=_key(request_key)).first()
    if previous:
        if previous.payload_digest!=digest(payload):
            raise ValidationError('This request key was already used for different handoff details.')
        return previous
    task=AW.source(kind,object_id,lock=True)
    if task is None:
        raise ValidationError('This source is no longer awaiting this approval.')
    recipient=get_user_model().objects.select_for_update(no_key=True).get(pk=recipient.pk)
    allowed,problem=AW.eligible(recipient,task)
    if not allowed:
        raise ValidationError(problem)
    old=ApprovalRouting.objects.select_for_update().filter(kind=kind,object_id=object_id).first()
    run,created=_run(request_key,payload,source_user=old.assignee if old else None,recipient=recipient,actor=actor,reason=reason)
    if not created:
        return run
    row=dict(kind='approval',object_id=old.pk if old else 0,reference=task['reference'],
             site_id=task['site'].pk if task['site'] else None,status=task['status'],
             fingerprint=object_digest(task['object']),eligible=True,reason='',task=AW.public(task))
    # The source lock serializes creation of the unique routing row as well as updates.
    routing,_=ApprovalRouting.objects.update_or_create(kind=kind,object_id=object_id,defaults=dict(
        assignee=recipient,assigned_by=actor,assigned_at=timezone.now(),reason=reason,
        reference=task['reference'],site=task['site']))
    row['object_id']=routing.pk
    _save_item(run,row,old.assignee if old else None,recipient,True,'Approval reviewer assigned; no authority granted.')
    run.transferred=1
    return _finish(run)


@transaction.atomic
def _perform(source_user,recipient,reason,request_key,actor=None,expected_digest=None,automatic=False,source_event=None):
    if recipient and source_user.pk==recipient.pk:
        raise ValidationError('Choose a different recipient.')
    reason=_note(reason)
    # Serialize runs for this source without holding a User lock during business-document updates.
    WorkHandoffGuard.objects.get_or_create(user=source_user)
    WorkHandoffGuard.objects.select_for_update(of=('self',)).get(user=source_user)
    payload=dict(source_user=source_user.pk,recipient=recipient.pk if recipient else None,reason=reason,automatic=automatic)
    existing=WorkHandoffRun.objects.filter(request_key=_key(request_key)).first()
    if existing:
        if existing.payload_digest!=digest(payload):
            raise ValidationError('This request key was already used for different handoff details.')
        return existing
    if automatic:
        current=get_user_model().objects.get(pk=source_user.pk)
        if access_allowed(current):
            return None
        policy=UserProfile.objects.filter(user=current).first()
        if (policy.work_backup_id if policy else None)!=(recipient.pk if recipient else None):
            raise ValidationError('The backup policy changed. Retry with the current configuration.')
    rows,before=preview(source_user,recipient)
    if expected_digest is not None and before!=expected_digest:
        raise ValidationError('Pending work or recipient eligibility changed. Review a fresh preview before transferring.')
    # Match service locking: source document first, then its optional routing row.
    resources={(row['_object']._meta.label_lower,row['_object'].pk):row['_object'] for row in rows}
    for _,obj in sorted(resources.items()):
        obj.__class__.objects.select_for_update(of=('self',)).get(pk=obj.pk)
    for row in rows:
        if '_routing' in row:
            ApprovalRouting.objects.select_for_update().get(pk=row['_routing'].pk)
    if recipient:
        recipient=get_user_model().objects.select_for_update(no_key=True).get(pk=recipient.pk)
    locked_rows,after=preview(source_user,recipient)
    if before!=after:
        raise ValidationError('Pending work changed while being reviewed. Open a fresh preview.')
    run,created=_run(request_key,payload,source_user=source_user,recipient=recipient,actor=actor,
                     reason=reason,automatic=automatic,source_event=source_event)
    if not created:
        return run
    for row in locked_rows:
        if not row['eligible']:
            _save_item(run,row,source_user,recipient,False,row['reason'])
            run.blocked+=1
            continue
        obj=row['_object']
        if row['kind']=='approval':
            routing=row['_routing']
            routing.assignee,routing.assigned_by,routing.assigned_at,routing.reason=recipient,actor,timezone.now(),reason
            routing.save(update_fields=['assignee','assigned_by','assigned_at','reason'])
        else:
            setattr(obj,row['_owner_field'],recipient)
            fields=[row['_owner_field']]
            if hasattr(obj,'updated_at'):
                fields.append('updated_at')
            obj.save(update_fields=fields)
        _save_item(run,row,source_user,recipient,True,reason)
        M.AuditEvent.objects.create(actor=actor,site_id=row['site_id'],action='Pending work reassigned',
            reference=row['reference'][:100],detail=f'{row["kind"]}:{row["object_id"]}; {source_user.pk} → {recipient.pk}; {run}')
        run.transferred+=1
    return _finish(run)


def handoff(actor,source_user,recipient,reason,request_key,expected_digest):
    _admin(actor)
    if not recipient or not access_allowed(get_user_model().objects.get(pk=recipient.pk)):
        raise ValidationError('Choose an active recipient with unexpired access.')
    return _perform(source_user,recipient,reason,request_key,actor,expected_digest)


def automatic_handoff(user_id,source_event_id=None):
    user=get_user_model().objects.get(pk=user_id)
    if access_allowed(user):
        return None
    profile=UserProfile.objects.filter(user=user).select_related('work_backup').first()
    recipient=profile.work_backup if profile else None
    rows,stamp=preview(user,recipient)
    if not rows:
        return None
    # Stable on unchanged work/eligibility; eligibility changes yield a fresh retry attempt.
    key=uuid.uuid5(uuid.NAMESPACE_URL,f'sistech-unavailable:{user_id}:{recipient.pk if recipient else None}:{stamp}')
    event=M.AuditEvent.objects.filter(pk=source_event_id).first() if source_event_id else M.AuditEvent.objects.filter(
        reference=f'user:{user_id}',action__in=['User disabled','Temporary access expired','User access updated']).order_by('-pk').first()
    return _perform(user,recipient,'Automatic unavailable-staff handoff',key,automatic=True,source_event=event)


def schedule_unavailable(user_id,source_event_id=None):
    transaction.on_commit(lambda:automatic_handoff(user_id,source_event_id),robust=True)


def former_owners(kind,object_id):
    return set(WorkHandoffItem.objects.filter(kind=kind,object_id=object_id,outcome='moved',
        source_user__isnull=False).values_list('source_user_id',flat=True))


def assigned_pending(user):
    rows=[]
    for routing in ApprovalRouting.objects.filter(assignee=user).order_by('assigned_at','pk'):
        task=AW.source(routing.kind,routing.object_id)
        if task and AW.eligible(user,task)[0]:
            rows.append(dict(routing=routing,task=task,url=AW.source_url(task)))
    return rows

def unavailable_backlog():
    """Read-only current exceptions, rather than presenting old blocked receipts as unresolved."""
    from django.db.models import Q
    ids=set(ApprovalRouting.objects.values_list('assignee_id',flat=True))
    for _,(model,field,filters,_,_) in OWNER_WORK.items():
        ids.update(model.objects.filter(**filters).values_list(field+'_id',flat=True).distinct())
    ids.discard(None)
    users=get_user_model().objects.filter(pk__in=ids).filter(
        Q(is_active=False)|Q(profile__access_expires_at__lte=timezone.now())).order_by('username')
    result=[]
    for user in users:
        profile=getattr(user,'profile',None)
        backup=profile.work_backup if profile else None
        rows=inventory(user,backup)
        if rows:
            result.append(dict(user=user,backup=backup,total=len(rows),
                eligible=sum(row['eligible'] for row in rows),
                attention=sum(not row['eligible'] for row in rows)))
    return result

