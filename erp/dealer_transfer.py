"""Preview-bound future commercial ownership transfer with retained stop lineage."""
import hashlib
import json
import uuid
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Max
from django.utils import timezone
from . import beats
from .models import DealerTerritoryAssignment, DealerOwnershipTransfer, BeatPlan, BeatStop, Site, CUSTOMER_KINDS
from .services import require_site


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def _assignment(user,pk,lock=False):
    beats.permitted(user,'manage_salesterritory');beats.permitted(user,'view_team_beats');beats.permitted(user,'view_beatplan')
    obj=DealerTerritoryAssignment.objects.select_related('site','territory','partner','owner').get(pk=pk)
    require_site(user,obj.site)
    if lock:
        Site.objects.select_for_update().get(pk=obj.site_id)
        obj=DealerTerritoryAssignment.objects.select_for_update().select_related('site','territory','partner','owner').get(pk=pk)
    return obj


def _row(obj):
    values={field.attname:str(getattr(obj,field.attname)) for field in obj._meta.concrete_fields}
    safe={'id','starts_on','ends_on','on','status','revision','superseded_at','updated_at','created_at','submitted_at','approved_at','recorded_at','followed_at'}
    return dict(fingerprint=digest(values), **{k:v for k,v in values.items() if k in safe or k.endswith('_id')})


def preview(user,pk,recipient,effective_on):
    source=_assignment(user,pk)
    recipient=get_user_model().objects.get(pk=recipient.pk)
    beats._owner(recipient,source.site);beats._active(source.territory)
    if not source.partner.active or source.partner.kind not in CUSTOMER_KINDS:raise ValidationError('Use an active customer assignment.')
    if source.superseded_at or DealerOwnershipTransfer.objects.filter(source=source).exists():raise ValidationError('This assignment already has a retained transfer.')
    if recipient.pk==source.owner_id:raise ValidationError('Choose a different qualified owner.')
    if not effective_on or effective_on<max(source.starts_on,timezone.localdate()+timedelta(days=1)):
        raise ValidationError('Transfer from tomorrow or the future assignment start onward. Use execution cover for today’s visits.')
    if source.ends_on and effective_on>source.ends_on:raise ValidationError('The transfer must start within the retained assignment period.')
    overlaps=DealerTerritoryAssignment.objects.filter(site=source.site,partner=source.partner,superseded_at__isnull=True).exclude(pk=pk)
    overlaps=overlaps.filter(Q(ends_on__isnull=True)|Q(ends_on__gte=effective_on))
    if source.ends_on:overlaps=overlaps.filter(starts_on__lte=source.ends_on)
    if overlaps.exists():raise ValidationError('Another dated ownership overlaps the replacement period.')
    stops=list(BeatStop.objects.filter(assignment=source,plan__on__gte=effective_on).exclude(plan__status='cancelled').exclude(status='reassigned').select_related('plan').order_by('plan_id','pk'))
    if stops:
        beats.permitted(user,'add_beatplan');beats.permitted(user,'change_beatplan')
    if any(s.status!='planned' or s.recorded_at for s in stops):raise ValidationError('A future route has recorded evidence; reconcile it before transferring. No recorded visit is moved.')
    if any(s.plan.owner_id!=source.owner_id or (source.ends_on and s.plan.on>source.ends_on) for s in stops):
        raise ValidationError('A retained future stop lies outside this ownership. Reconcile the route before transferring.')
    sources={s.plan_id:s.plan for s in stops}
    targets=list(BeatPlan.objects.filter(site=source.site,owner=recipient,on__in=[p.on for p in sources.values()]).exclude(status='cancelled').order_by('pk'))
    if any(p.status=='finished' or p.stops.exclude(status__in=['planned','reassigned']).exists() for p in targets):
        raise ValidationError('A destination route has recorded outcomes. Retain and reconcile it before transferring.')
    if any(p.stops.exclude(status='reassigned').count()>=50 for p in targets):raise ValidationError('A destination route already has 50 active stops.')
    evidence=dict(source=_row(source),recipient=recipient.pk,effective_on=str(effective_on),
        source_plans=[dict(plan=_row(p),contributors=list(p.contributors.order_by('pk').values_list('pk',flat=True)),stops=[_row(s) for s in p.stops.order_by('pk')]) for p in sources.values()],
        target_plans=[dict(plan=_row(p),contributors=list(p.contributors.order_by('pk').values_list('pk',flat=True)),stops=[_row(s) for s in p.stops.order_by('pk')]) for p in targets])
    return dict(source=source,recipient=recipient,effective_on=effective_on,stops=stops,source_plans=sources,targets=targets,
        fingerprint=digest(evidence),evidence=evidence)


@transaction.atomic
def transfer(user,pk,recipient,effective_on,reason,request_key,expected_digest):
    source=_assignment(user,pk,lock=True)
    try:key=uuid.UUID(str(request_key))
    except (TypeError,ValueError,AttributeError):raise ValidationError('Open a fresh transfer preview.') from None
    reason=(reason or '').strip()
    if not reason or len(reason)>300:raise ValidationError('Record a handover reason of up to 300 characters.')
    payload=digest(dict(source=pk,recipient=recipient.pk,effective_on=str(effective_on),reason=reason,preview=expected_digest))
    existing=DealerOwnershipTransfer.objects.filter(request_key=key).first()
    if existing:
        if existing.actor_id!=user.pk or existing.payload_digest!=payload:raise ValidationError('This request key belongs to a different transfer.')
        return existing
    # All territory/plan/stop writers acquire this site lock before their rows.
    current=preview(user,pk,recipient,effective_on)
    if not expected_digest or current['fingerprint']!=expected_digest:raise ValidationError('Ownership or route evidence changed. Review a fresh preview.')
    original_end=source.ends_on
    if effective_on==source.starts_on:source.superseded_at=timezone.now()
    else:source.ends_on=effective_on-timedelta(days=1)
    source.end_note=reason;source.ended_by=user;source.full_clean();source.save()
    replacement=DealerTerritoryAssignment(territory=source.territory,site=source.site,partner=source.partner,owner=recipient,
        starts_on=effective_on,ends_on=original_end,note=reason,created_by=user)
    replacement.full_clean();replacement.save()
    destination={p.on:p for p in current['targets']}
    moved=[]
    for old in current['stops']:
        plan=old.plan
        target=destination.get(plan.on)
        if target is None:
            target=BeatPlan.objects.create(site=source.site,owner=recipient,on=plan.on,title=f'Transferred visits · {plan.on}',
                created_by=user,number=beats.next_number('BEAT',source.site))
            destination[plan.on]=target
        target.contributors.add(user,recipient,source.owner,*plan.contributors.all())
        next_sequence=(target.stops.aggregate(value=Max('sequence'))['value'] or 0)+1
        new=BeatStop(plan=target,assignment=replacement,sequence=next_sequence,purpose=old.purpose,instructions=old.instructions)
        new.full_clean();new.save()
        old.status='reassigned';old.replacement_stop=new;old.save(update_fields=['status','replacement_stop'])
        moved.append(dict(source_stop=old.pk,replacement_stop=new.pk,source_plan=plan.pk,replacement_plan=target.pk))
    for plan in [*current['source_plans'].values(),*destination.values()]:
        plan.status='draft' if plan.stops.filter(status='planned').exists() else 'cancelled'
        # Earlier approvals remain in BeatReview; a fresh submission is required.
        plan.approved_by=None;plan.approved_at=None;plan.decision_note=reason;plan.save()
        plan.contributors.add(user,source.owner,recipient)
        beats._review(plan,user,'Ownership transfer',reason)
    obj=DealerOwnershipTransfer.objects.create(source=source,replacement=replacement,effective_on=effective_on,actor=user,
        reason=reason,request_key=key,payload_digest=payload,evidence=dict(before=current['evidence'],moved_stops=moved))
    beats._log(user,source.site,'Dealer ownership transferred',f'assignment:{source.pk}',f'Transfer {obj.pk} · replacement {replacement.pk} · {effective_on} · {reason}')
    return obj
