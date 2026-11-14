"""Dated territory ownership, independently approved visit plans and immutable outcomes."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (SalesTerritory, DealerTerritoryAssignment, BeatPlan, BeatStop, BeatReview, Site, Partner,
                     AuditEvent, CollectionFollowUp, CUSTOMER_KINDS)
from .services import permitted, require_site
from .crm import sites_for, lead_scope
from .gst import next_number
from .beat_forms import TerritoryForm, AssignmentForm, PlanForm, StopForm, OutcomeForm


def scope(qs,user,prefix=''):
    root=prefix+'__' if prefix else ''
    if user.is_superuser:return qs
    qs=qs.filter(**{root+'site__in':sites_for(user)})
    return qs if user.has_perm('erp.view_team_beats') else qs.filter(**{root+'owner':user})


def eligible_owners():
    ids=[u.pk for u in get_user_model().objects.filter(is_active=True) if u.has_perm('erp.view_beatplan') and u.has_perm('erp.record_beatstop')]
    return get_user_model().objects.filter(pk__in=ids).order_by('username')


def _owner(owner,site):
    if not owner.is_active or not owner.has_perm('erp.view_beatplan') or not owner.has_perm('erp.record_beatstop'):
        raise ValidationError('The owner needs active field-plan view and visit-recording access.')
    require_site(owner,site)


def _errors(form):
    return ValidationError([f'{k}: {m}' for k,values in form.errors.items() for m in values])


def _log(user,site,action,reference,note):
    AuditEvent.objects.create(actor=user,site=site,action=action,reference=reference,detail=note)


def path(territory):
    nodes=[]
    while territory:
        nodes.append(territory)
        territory=territory.parent
    return list(reversed(nodes))


def _active(territory):
    territory=SalesTerritory.objects.select_related('site','parent__parent').get(pk=territory.pk)
    if not territory.site.active or any(not node.active for node in path(territory)):
        raise ValidationError('This site or territory branch is inactive. Use an active assignment.')


@transaction.atomic
def create_territory(user,data):
    permitted(user,'manage_salesterritory')
    form=TerritoryForm(data,user=user)
    if not form.is_valid():raise _errors(form)
    obj=form.save(commit=False);obj.code=obj.code.strip().upper();obj.name=' '.join(obj.name.split())
    require_site(user,obj.site)
    Site.objects.select_for_update().get(pk=obj.site_id)
    if obj.kind=='state':
        if obj.parent_id or not obj.state:raise ValidationError('A state root needs its state code and no parent.')
        if SalesTerritory.objects.filter(site=obj.site,kind='state',state=obj.state).exists():raise ValidationError('This site already has a territory root for this state.')
    else:
        if not obj.parent_id or obj.parent.site_id!=obj.site_id or obj.parent.kind!={'district':'state','area':'district'}[obj.kind]:
            raise ValidationError('Use a state parent for districts and a district parent for areas, within the same site.')
        _active(obj.parent)
        obj.state=obj.parent.state
        if SalesTerritory.objects.filter(parent=obj.parent,name__iexact=obj.name).exists():raise ValidationError('This parent already has a territory with that name.')
    obj.created_by=user;obj.full_clean();obj.save()
    _log(user,obj.site,'Territory created',f'territory:{obj.pk}',str(obj))
    return obj


@transaction.atomic
def toggle_territory(user,pk,note):
    permitted(user,'manage_salesterritory')
    obj=SalesTerritory.objects.get(pk=pk);require_site(user,obj.site)
    Site.objects.select_for_update().get(pk=obj.site_id)
    obj=SalesTerritory.objects.select_for_update().get(pk=pk)
    if not (note or '').strip():raise ValidationError('Record the reason for changing this branch status.')
    if not obj.active and obj.parent:_active(obj.parent)
    obj.active=not obj.active;obj.save(update_fields=['active'])
    _log(user,obj.site,'Territory activated' if obj.active else 'Territory archived',f'territory:{obj.pk}',note)
    return obj


@transaction.atomic
def assign(user,data):
    permitted(user,'manage_salesterritory')
    form=AssignmentForm(data,user=user)
    if not form.is_valid():raise _errors(form)
    obj=form.save(commit=False);obj.site=obj.territory.site
    require_site(user,obj.site);Site.objects.select_for_update().get(pk=obj.site_id)
    _active(obj.territory);_owner(obj.owner,obj.site)
    if obj.starts_on<timezone.localdate():raise ValidationError('New ownership cannot be backdated; retained assignments preserve past coverage.')
    if obj.ends_on and obj.ends_on<obj.starts_on:raise ValidationError('End date cannot precede the start date.')
    overlaps=DealerTerritoryAssignment.objects.filter(site=obj.site,partner=obj.partner).filter(Q(ends_on__isnull=True)|Q(ends_on__gte=obj.starts_on))
    if obj.ends_on:overlaps=overlaps.filter(starts_on__lte=obj.ends_on)
    if overlaps.exists():raise ValidationError('This customer already has an assignment during those dates at this site. End it before the next assignment starts.')
    obj.created_by=user;obj.full_clean();obj.save()
    _log(user,obj.site,'Dealer territory assigned',f'assignment:{obj.pk}',f'{obj} · {obj.starts_on} to {obj.ends_on or "ongoing"} · {obj.note}')
    return obj


@transaction.atomic
def end_assignment(user,pk,on,note):
    permitted(user,'manage_salesterritory')
    header=DealerTerritoryAssignment.objects.get(pk=pk);require_site(user,header.site)
    Site.objects.select_for_update().get(pk=header.site_id)
    obj=DealerTerritoryAssignment.objects.select_for_update().get(pk=pk)
    if obj.ends_on:raise ValidationError('The retained assignment already has an end date. Prepare a new assignment for a later period.')
    if not on or on<max(obj.starts_on,timezone.localdate()) or not (note or '').strip():
        raise ValidationError('Choose an inclusive end date from today/start onward and record the handover reason.')
    obj.ends_on,obj.end_note,obj.ended_by=on,note,user;obj.full_clean();obj.save()
    _log(user,obj.site,'Dealer territory ended',f'assignment:{obj.pk}',f'Through {on} · {note}')
    return obj


def _plan(user,pk):
    header=BeatPlan.objects.only('site_id').get(pk=pk)
    require_site(user,header.site)
    Site.objects.select_for_update().get(pk=header.site_id)
    plan=BeatPlan.objects.select_for_update().select_related('site','owner').get(pk=pk)
    if not user.is_superuser and not user.has_perm('erp.view_team_beats') and plan.owner_id!=user.pk:raise PermissionDenied
    return plan


def _editor(user,plan):
    if plan.owner_id!=user.pk and not user.has_perm('erp.manage_salesterritory'):raise PermissionDenied


@transaction.atomic
def save_plan(user,data,pk=None):
    permitted(user,'change_beatplan' if pk else 'add_beatplan')
    plan=_plan(user,pk) if pk else None
    if plan:
        _editor(user,plan)
        if plan.status!='draft':raise ValidationError('Only draft routes can be edited.')
        for key,value in [('site',plan.site),('owner',plan.owner),('on',plan.on)]:
            if data.get(key)!=value:raise ValidationError('The site, owner and route date are fixed; cancel and prepare another route to change them.')
    form=PlanForm(data,instance=plan,user=user)
    if not form.is_valid():raise _errors(form)
    obj=form.save(commit=False);require_site(user,obj.site)
    Site.objects.select_for_update().get(pk=obj.site_id)
    _owner(obj.owner,obj.site)
    if obj.owner_id!=user.pk and not user.has_perm('erp.manage_salesterritory'):raise PermissionDenied
    if obj.on<timezone.localdate() or not obj.site.active:raise ValidationError('Plan today or a future date at an active site.')
    if not pk:obj.created_by=user;obj.number=next_number('BEAT',obj.site)
    obj.full_clean();obj.save();obj.contributors.add(user)
    _log(user,obj.site,'Beat draft saved',obj.number,f'{obj.on} · owner {obj.owner.get_username()} · {obj.title}')
    return obj


def _check_assignment(plan,assignment,active=True):
    if assignment.site_id!=plan.site_id or assignment.owner_id!=plan.owner_id or assignment.territory.kind!='area':raise ValidationError('The stop must use this owner’s area assignment at the route site.')
    if assignment.starts_on>plan.on or (assignment.ends_on and assignment.ends_on<plan.on):raise ValidationError('An assignment no longer covers the route date. Return the plan for changes.')
    if active:
        _active(assignment.territory)
        if not assignment.partner.active or assignment.partner.kind not in CUSTOMER_KINDS:raise ValidationError('The customer is inactive or no longer a customer.')


@transaction.atomic
def add_stop(user,pk,data):
    permitted(user,'change_beatplan')
    plan=_plan(user,pk);_editor(user,plan)
    if plan.status!='draft':raise ValidationError('Stops are locked after submission.')
    if plan.stops.count()>=50:raise ValidationError('A daily route supports up to 50 stops.')
    form=StopForm(data,plan=plan)
    if not form.is_valid():raise _errors(form)
    stop=form.save(commit=False);stop.plan=plan
    _check_assignment(plan,stop.assignment)
    if stop.sequence<1:raise ValidationError('Stop sequence starts at one.')
    stop.full_clean();stop.save();plan.contributors.add(user)
    _log(user,plan.site,'Beat stop added',plan.number,f'{stop.sequence} · {stop.assignment.partner.code} · {stop.get_purpose_display()}')
    return stop


@transaction.atomic
def remove_stop(user,pk,stop_pk):
    permitted(user,'change_beatplan')
    plan=_plan(user,pk);_editor(user,plan)
    if plan.status!='draft':raise ValidationError('Only draft stops can be removed.')
    stop=plan.stops.get(pk=stop_pk);code=stop.assignment.partner.code
    stop.delete();plan.contributors.add(user)
    _log(user,plan.site,'Beat draft stop removed',plan.number,code)


def _stop_snapshot(stop):
    a=stop.assignment;p=a.partner
    return dict(assignment=a.pk,owner=a.owner_id,partner=p.pk,code=p.code,name=p.name,address=p.address,city=p.city,phone=p.phone,
                starts_on=str(a.starts_on),ends_on=str(a.ends_on) if a.ends_on else None,
                territory=a.territory_id,area=a.territory.name,path=[dict(id=n.pk,code=n.code,name=n.name,kind=n.kind) for n in path(a.territory)])


def _review(plan,user,action,note):
    snapshot=dict(title=plan.title,owner=plan.owner_id,owner_name=plan.owner.get_username(),on=str(plan.on),site=plan.site.code,
                  stops=[dict(sequence=s.sequence,purpose=s.get_purpose_display(),instructions=s.instructions,customer=s.snapshot) for s in plan.stops.all()])
    review=BeatReview(plan=plan,revision=plan.revision,action=action,actor=user,note=note,snapshot=snapshot)
    review.full_clean();review.save()
    _log(user,plan.site,'Beat '+action.lower(),plan.number,f'Revision {plan.revision} · {note}')


@transaction.atomic
def submit_plan(user,pk,note):
    permitted(user,'submit_beatplan')
    plan=_plan(user,pk);_editor(user,plan)
    if plan.status!='draft' or plan.on<timezone.localdate() or not (note or '').strip():raise ValidationError('Submit a current/future draft with a submission note.')
    _owner(plan.owner,plan.site)
    stops=list(plan.stops.select_related('assignment__partner','assignment__territory'))
    if not stops:raise ValidationError('Add at least one assigned customer stop.')
    for stop in stops:
        _check_assignment(plan,stop.assignment);stop.snapshot=_stop_snapshot(stop);stop.save(update_fields=['snapshot'])
    plan.status='submitted';plan.revision+=1;plan.submitted_by=user;plan.approved_by=None;plan.approved_at=None;plan.decision_note=''
    plan.save();plan.contributors.add(user);_review(plan,user,'Submitted',note)
    return plan


@transaction.atomic
def decide_plan(user,pk,approve,note):
    permitted(user,'approve_beatplan')
    plan=_plan(user,pk)
    if plan.status!='submitted':raise ValidationError('Only a submitted plan can be reviewed.')
    if user.pk in [plan.created_by_id,plan.owner_id,plan.submitted_by_id] or plan.contributors.filter(pk=user.pk).exists():raise ValidationError('A contributor or route owner cannot approve their own plan.')
    if not (note or '').strip():raise ValidationError('Record the review finding.')
    if approve:
        if plan.on<timezone.localdate():raise ValidationError('The planned day has passed; return for changes or cancel this plan.')
        _owner(plan.owner,plan.site)
        if not plan.stops.exists():raise ValidationError('A route needs at least one stop.')
        for stop in plan.stops.select_related('assignment__partner','assignment__territory'):
            _check_assignment(plan,stop.assignment)
            if _stop_snapshot(stop)!=stop.snapshot:raise ValidationError('Assignment/customer evidence changed after submission. Return for changes and resubmit.')
        plan.status,plan.approved_by,plan.approved_at='approved',user,timezone.now()
    else:plan.status='draft'
    plan.decision_note=note;plan.save();_review(plan,user,'Approved' if approve else 'Changes requested',note)
    return plan


@transaction.atomic
def cancel_plan(user,pk,note):
    permitted(user,'change_beatplan')
    plan=_plan(user,pk);_editor(user,plan)
    if plan.status in ['finished','cancelled'] or plan.stops.exclude(status='planned').exists():raise ValidationError('A route with recorded outcomes cannot be cancelled; retain and finish its remaining stops.')
    if not (note or '').strip():raise ValidationError('Record the cancellation reason.')
    plan.status='cancelled';plan.decision_note=note;plan.save();_review(plan,user,'Cancelled',note)
    return plan


@transaction.atomic
def record_stop(user,pk,data,missed=False):
    permitted(user,'record_beatstop')
    header=BeatStop.objects.only('plan_id').get(pk=pk)
    plan=_plan(user,header.plan_id);_editor(user,plan)
    stop=plan.stops.select_for_update().select_related('assignment__partner','assignment__territory').get(pk=pk)
    if plan.status!='approved' or stop.status!='planned':raise ValidationError('Record an outstanding stop on an approved route once.')
    if plan.on>timezone.localdate():raise ValidationError('A future visit cannot be recorded as completed or missed.')
    _check_assignment(plan,stop.assignment,active=False)
    if missed:
        note=(data.get('note') or '').strip()
        if not note:raise ValidationError('Record why the planned visit was missed.')
        stop.status,stop.note='missed',note
    else:
        form=OutcomeForm(data,user=user,stop=stop)
        if not form.is_valid():raise _errors(form)
        values=form.cleaned_data;actual=values['visited_on']
        if actual<plan.on or actual>timezone.localdate():raise ValidationError('Visit date must be from the planned date through today.')
        if values['next_action_on'] and values['next_action_on']<actual:raise ValidationError('Next action cannot precede the visit.')
        if values['outcome']=='promise':
            if not values['promised_amount'] or values['promised_amount']<=0 or not values['promised_on'] or values['promised_on']<actual:raise ValidationError('A payment promise needs a positive amount and payment date on/after the visit.')
        elif values['promised_amount'] is not None or values['promised_on']:raise ValidationError('Payment fields apply only to a reported payment promise.')
        if values['outcome']=='complaint' and not values['complaint']:raise ValidationError('Link the matching complaint before recording a complaint outcome.')
        for key,value in values.items():setattr(stop,key,value)
        stop.status='completed'
    stop.recorded_by,stop.recorded_at=user,timezone.now();stop.full_clean();stop.save()
    if not plan.stops.filter(status='planned').exists():plan.status='finished';plan.save(update_fields=['status','updated_at'])
    _log(user,plan.site,'Beat stop '+stop.status,plan.number,f'{stop.assignment.partner.code} · {stop.note}')
    return stop


@transaction.atomic
def close_followup(user,pk,note):
    permitted(user,'record_beatstop')
    header=BeatStop.objects.only('plan_id').get(pk=pk);plan=_plan(user,header.plan_id);_editor(user,plan)
    stop=plan.stops.select_for_update().get(pk=pk)
    if stop.status!='completed' or not stop.next_action_on or stop.followed_at or not (note or '').strip():raise ValidationError('Close an open follow-up with an outcome note.')
    stop.followup_note,stop.followed_by,stop.followed_at=note,user,timezone.now();stop.full_clean();stop.save()
    _log(user,plan.site,'Beat follow-up closed',plan.number,note)
    return stop


@transaction.atomic
def publish_promise(user,pk,note):
    permitted(user,'publish_beatpromise')
    from .collect import _need
    _need(user,'add_receipt')
    header=BeatStop.objects.only('plan_id').get(pk=pk);plan=_plan(user,header.plan_id)
    stop=plan.stops.select_for_update().select_related('assignment__partner').get(pk=pk)
    if stop.collection_followup_id:return stop.collection_followup
    if stop.status!='completed' or stop.outcome!='promise' or not (note or '').strip():raise ValidationError('Review a completed field promise and record the finance finding.')
    if user.pk in [plan.owner_id,stop.recorded_by_id]:raise ValidationError('Finance review must be independent of the reported visit.')
    row=CollectionFollowUp(partner=stop.assignment.partner,kind='promise',on=stop.visited_on,note=f'{plan.number}: {stop.note}'[:300],
        promised_amount=stop.promised_amount,promised_date=stop.promised_on,next_action_on=stop.next_action_on,owner=plan.owner,created_by=user)
    row.full_clean();row.save();stop.collection_followup=row;stop.save(update_fields=['collection_followup'])
    _log(user,plan.site,'Beat promise accepted',plan.number,f'Collection follow-up {row.pk} · {note}')
    return row


def coverage(user,start,end):
    permitted(user,'view_beatplan')
    if start>end or (end-start).days>366:raise ValidationError('Choose a report period of up to 367 inclusive days.')
    assignments=scope(DealerTerritoryAssignment.objects.select_related('territory','partner','owner','site'),user).filter(starts_on__lte=end).filter(Q(ends_on__isnull=True)|Q(ends_on__gte=start))
    stops=scope(BeatStop.objects.select_related('plan__owner','plan__site','assignment__territory','assignment__partner'),user,'plan').filter(plan__on__range=(start,end),plan__status__in=['approved','finished'])
    rows={}
    def row(site,owner,area_id,area_name):
        key=(site.pk,owner.pk,area_id)
        return rows.setdefault(key,dict(site=site,owner=owner,area=area_name,assigned={},visited=set(),planned=0,completed=0,missed=0,overdue=0))
    for a in assignments:
        item=row(a.site,a.owner,a.territory_id,a.territory.name);item['assigned'][a.partner_id]=a.partner
    for s in stops:
        item=row(s.plan.site,s.plan.owner,s.snapshot.get('territory',s.assignment.territory_id),s.snapshot.get('area',s.assignment.territory.name))
        item['planned']+=1
        if s.status=='completed':item['completed']+=1;item['visited'].add(s.snapshot.get('partner',s.assignment.partner_id))
        elif s.status=='missed':item['missed']+=1
        elif s.plan.on<timezone.localdate():item['overdue']+=1
    for item in rows.values():
        item['gaps']=[partner for key,partner in item['assigned'].items() if key not in item['visited']]
        item['assigned_count']=len(item['assigned']);item['visited_count']=len(item['visited'])
    return sorted(rows.values(),key=lambda r:(r['site'].code,r['owner'].get_username(),r['area']))
