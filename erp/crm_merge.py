"""Reviewed duplicate prospect consolidation: canonical link, original sources retained."""
import hashlib,json,uuid
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from accounts.security import access_allowed
from accounts.delegation import authorize,can_approve
from . import crm
from .models import CRMLead,CRMLeadMerge,AuditEvent


def _read(user):
    if not access_allowed(user) or not user.has_perm('erp.view_crmlead') or not user.has_perm('erp.view_team_crm'):raise PermissionDenied


def pair(user,source_id,target_id):
    _read(user)
    rows=list(crm.lead_scope(CRMLead.objects.select_for_update(of=('self',)).select_related('site','owner'),user).filter(pk__in=[source_id,target_id]).order_by('pk'))
    by_id={row.pk:row for row in rows}
    if source_id==target_id:raise ValidationError('Choose a distinct canonical lead.')
    if source_id not in by_id or target_id not in by_id:raise PermissionDenied
    source,target=by_id[source_id],by_id[target_id]
    if source.site_id!=target.site_id:raise ValidationError('Lead consolidation must stay within one originating site.')
    if not source.site.active:raise ValidationError('The originating site must be active.')
    return source,target


def _snapshot(lead):
    fields=['pk','number','site_id','owner_id','created_by_id','business_name','contact_name','mobile','email','kind','source','district','territory','stage','next_follow_up','estimated_value','notes','partner_id','closed_reason','merged_into_id','updated_at']
    result={key:str(getattr(lead,key)) if getattr(lead,key) is not None else None for key in fields}
    result['estimated_value']=format(lead.estimated_value,'.2f')
    result.update(activities=list(lead.activities.order_by('pk').values('pk','created_by_id','happened_on','kind','outcome','note','next_follow_up')),
        applications=list(lead.applications.order_by('pk').values('pk','status','revision')),
        quotes=list(lead.quotations.order_by('pk').values('pk','status','revision')),
        projects=list(lead.sales_projects.order_by('pk').values('pk','stage','updated_at')),
        stops=list(lead.beat_stops.order_by('pk').values('pk','status','next_action_on','plan__status')),
        aliases=list(lead.merged_duplicates.order_by('pk').values_list('pk',flat=True)))
    return json.loads(json.dumps(result,default=str))


def _contributors(source,target):
    ids={source.created_by_id,source.owner_id,target.created_by_id,target.owner_id}
    for lead in [source,target]:
        ids.update(lead.activities.values_list('created_by_id',flat=True))
        ids.update(AuditEvent.objects.filter(site=lead.site,reference=lead.number).exclude(action__in=['CRM duplicate merge requested','CRM duplicate merge rejected','CRM duplicate merge cancelled','CRM duplicate merge approved']).values_list('actor_id',flat=True))
    return sorted(ids-{None})


def preview(source,target):
    if source.merged_into_id or target.merged_into_id:raise ValidationError('Choose current canonical records; a merged duplicate is read-only.')
    if source.partner_id or source.stage=='won':raise ValidationError('Converted/customer-linked sources require a separate master consolidation, not prospect merging.')
    if target.stage=='lost':raise ValidationError('Reopen the canonical target before consolidating into it.')
    if source.applications.filter(status__in=crm.OPEN_APPLICATIONS).exists():raise ValidationError('Resolve the source onboarding application before merging.')
    if source.quotations.filter(status__in=['draft','approval','ready','sent','accepted']).exists():raise ValidationError('Resolve active source quotations before merging; original documents remain retained.')
    if source.sales_projects.exclude(stage__in=['won','lost']).exists():raise ValidationError('Resolve active source sales projects before merging.')
    if source.beat_stops.filter(status='planned').exclude(plan__status__in=['cancelled','finished']).exists():raise ValidationError('Resolve planned source beat work before merging.')
    dates=[value for value in [source.next_follow_up,target.next_follow_up] if value]
    state=dict(source=_snapshot(source),target=_snapshot(target),makers=_contributors(source,target),
        follow_up=min(dates).isoformat() if dates and target.stage not in ['won','lost'] else None)
    return state,hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()


def makers(item):
    return set(item.snapshot.get('makers',[]))|{item.created_by_id,item.source.owner_id,item.target.owner_id,item.source.created_by_id,item.target.created_by_id}


def can_decide(user,item):
    try:_read(user)
    except PermissionDenied:return False
    allowed=crm.lead_scope(CRMLead.objects.filter(pk__in=[item.source_id,item.target_id]),user).count()==2
    return allowed and item.status=='requested' and user.pk not in makers(item) and can_approve(user,'approve_crmmerge',item.source.site,makers=makers(item))


@transaction.atomic
def propose(user,source_id,target_id,reason,key,expected_digest):
    crm._lock();source,target=pair(user,source_id,target_id);crm.permitted(user,'change_crmlead')
    reason=(reason or '').strip()
    if not reason or len(reason)>500:raise ValidationError('Record matching evidence and a merge reason of up to 500 characters.')
    try:key=uuid.UUID(str(key))
    except (ValueError,TypeError,AttributeError):raise ValidationError('Open a fresh merge preview.') from None
    old=CRMLeadMerge.objects.filter(request_key=key).first()
    if old:
        if (old.source_id,old.target_id,old.created_by_id,old.reason)!=(source.pk,target.pk,user.pk,reason):raise ValidationError('This request key belongs to another merge.')
        return old
    state,digest=preview(source,target)
    if expected_digest!=digest:raise ValidationError('The merge preview changed. Review the current records again.')
    if source.merge_requests.filter(status='requested').exists():raise ValidationError('A merge request is already pending for this source.')
    item=CRMLeadMerge(source=source,target=target,snapshot=state,source_digest=digest,reason=reason,request_key=key,created_by=user)
    item.full_clean();item.save();crm._audit(user,source,'CRM duplicate merge requested',f'{item.pk} → {target.number} · {reason}')
    return item


@transaction.atomic
def decide(user,pk,action,note):
    crm._lock();header=CRMLeadMerge.objects.get(pk=pk);source,target=pair(user,header.source_id,header.target_id)
    item=CRMLeadMerge.objects.select_for_update().select_related('source__site','target').get(pk=pk)
    if item.status!='requested':raise ValidationError('This merge request is already decided.')
    note=(note or '').strip()
    if not note or len(note)>500:raise ValidationError('Record a review finding of up to 500 characters.')
    if action=='cancel':
        if user.pk!=item.created_by_id and not user.is_superuser:raise PermissionDenied
    elif action in ['approve','reject']:
        if user.pk in makers(item):raise ValidationError('A contributor cannot review their own consolidation (maker-checker).')
        authorize(user,'approve_crmmerge',source.site,makers=makers(item),reference=f'CRMMERGE-{item.pk}')
    else:raise ValidationError('Choose approve, reject or cancel.')
    if action=='approve':
        state,digest=preview(source,target)
        if digest!=item.source_digest:raise ValidationError('Lead details or linked work changed. Reject and request a fresh merge.')
        source.merged_into=target;source.stage='merged';source.next_follow_up=None;source.full_clean();source.save()
        if state['follow_up']:
            from datetime import date
            target.next_follow_up=date.fromisoformat(state['follow_up']);target.save(update_fields=['next_follow_up','updated_at'])
    item.status={'approve':'approved','reject':'rejected','cancel':'cancelled'}[action];item.reviewed_by=user;item.reviewed_at=timezone.now();item.review_note=note;item.full_clean();item.save()
    if action=='approve':crm._audit(user,target,'CRM duplicate merge approved',f'{item.pk} ← {source.number} · {note}')
    crm._audit(user,source,'CRM duplicate merge '+item.status,f'{item.pk} → {target.number} · {note}')
    return item


def canonical(lead):
    seen=set()
    while lead.merged_into_id:
        if lead.pk in seen:raise ValidationError('The retained merge chain requires reconciliation.')
        seen.add(lead.pk);lead=lead.merged_into
    return lead
