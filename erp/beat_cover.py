"""Finite, independently reviewed execution cover; commercial ownership stays fixed."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from accounts.security import access_allowed
from accounts.delegation import authorize, can_approve
from .models import BeatExecutionCover, BeatPlan
from .services import permitted


def active(user):
    if not access_allowed(user) or not user.has_perm('erp.view_beatplan') or not user.has_perm('erp.record_beatstop'):
        return BeatExecutionCover.objects.none()
    now = timezone.now()
    qs = BeatExecutionCover.objects.filter(recipient=user, status='approved', starts_at__lte=now, ends_at__gt=now,
        revision=F('plan__revision'), plan__status__in=['approved','finished'])
    if not user.is_superuser:
        from .crm import sites_for
        qs = qs.filter(plan__site__in=sites_for(user))
    return qs


def executes(user, plan):
    return active(user).filter(plan=plan).exists()


def mine(qs, user, prefix=''):
    root = prefix + '__' if prefix else ''
    return qs.filter(Q(**{root+'owner':user}) | Q(**{root+'pk__in':active(user).values('plan_id')}))


def makers(cover):
    p = cover.plan
    return [cover.created_by_id, cover.recipient_id, p.owner_id, p.created_by_id, p.submitted_by_id,
            *p.contributors.values_list('pk',flat=True)]


def evidence(plan):
    # Only route/outstanding evidence: no duplicate customer/bank/document bodies.
    return dict(revision=plan.revision, approved_by=plan.approved_by_id,
        stops=list(plan.stops.order_by('pk').values('pk','status','recorded_by_id','recorded_at','next_action_on','followed_at')))


def snapshot(plan):
    import json
    return json.loads(json.dumps(evidence(plan), default=str))


def _manager(user):
    permitted(user,'view_beatplan');permitted(user,'manage_salesterritory');permitted(user,'view_team_beats')


def _outstanding(plan):
    return plan.stops.filter(Q(status='planned') | Q(status='completed',next_action_on__isnull=False,followed_at__isnull=True)).exists()


@transaction.atomic
def propose(user, pk, recipient, ends_at, reason):
    from . import beats
    _manager(user)
    plan = beats._plan(user,pk)
    beats._owner(recipient,plan.site)
    now = timezone.now()
    if plan.status not in ['approved','finished'] or not _outstanding(plan):
        raise ValidationError('Cover requires an approved route with outstanding visits or follow-ups.')
    if recipient.pk == plan.owner_id or not ends_at or ends_at <= now or not (reason or '').strip():
        raise ValidationError('Choose a different qualified executor, a future expiry and a reason.')
    if plan.on > timezone.localdate(ends_at):
        raise ValidationError('Cover must extend through the planned route date.')
    if plan.execution_covers.filter(status__in=['submitted','approved'],ends_at__gt=now).exists():
        raise ValidationError('An unexpired cover already exists. Review or revoke it before proposing another.')
    obj = BeatExecutionCover(plan=plan,recipient=recipient,created_by=user,starts_at=now,ends_at=ends_at,
        revision=plan.revision,evidence=snapshot(plan),reason=reason.strip())
    obj.full_clean();obj.save()
    beats._log(user,plan.site,'Beat cover proposed',plan.number,f'Cover {obj.pk} · recipient {recipient.pk} · through {ends_at} · {reason}')
    return obj


def can_review(user, obj):
    return (access_allowed(user) and user.has_perm('erp.view_team_beats') and user.has_perm('erp.view_beatplan')
        and user.pk not in makers(obj) and can_approve(user,'approve_beatplan',obj.plan.site,makers=makers(obj)))


@transaction.atomic
def decide(user, pk, approve, note):
    from . import beats
    header = BeatExecutionCover.objects.get(pk=pk)
    plan = beats._plan(user,header.plan_id)
    obj = BeatExecutionCover.objects.select_for_update().select_related('plan').get(pk=pk)
    if not can_review(user,obj):raise PermissionDenied
    authorize(user,'approve_beatplan',plan.site,makers=makers(obj),reference=plan.number)
    if obj.status!='submitted' or not (note or '').strip():raise ValidationError('Review a pending cover with a finding.')
    if approve:
        beats._owner(obj.recipient,plan.site)
        if obj.ends_at<=timezone.now() or plan.status not in ['approved','finished'] or not _outstanding(plan) or obj.evidence!=snapshot(plan):
            raise ValidationError('The route work changed or cover expired. Reject and propose a fresh cover.')
        if plan.execution_covers.exclude(pk=pk).filter(status='approved',ends_at__gt=timezone.now()).exists():
            raise ValidationError('Another cover already handles this route.')
    obj.status='approved' if approve else 'rejected'
    obj.reviewed_by=user;obj.reviewed_at=timezone.now();obj.review_note=note.strip();obj.full_clean();obj.save()
    beats._log(user,plan.site,'Beat cover '+obj.status,plan.number,f'Cover {obj.pk} · {note}')
    return obj


@transaction.atomic
def revoke(user, pk, note):
    from . import beats
    _manager(user)
    header=BeatExecutionCover.objects.get(pk=pk)
    plan=beats._plan(user,header.plan_id)
    obj=BeatExecutionCover.objects.select_for_update().get(pk=pk)
    if obj.status not in ['submitted','approved'] or not (note or '').strip():raise ValidationError('Revoke a pending/approved cover with a reason.')
    obj.status='revoked';obj.revoked_by=user;obj.revoked_at=timezone.now();obj.revoke_note=note.strip();obj.full_clean();obj.save()
    beats._log(user,plan.site,'Beat cover revoked',plan.number,f'Cover {obj.pk} · {note}')
    return obj


def reviewable(user):
    from accounts.delegation import approvable
    from .crm import sites_for
    qs=BeatExecutionCover.objects.filter(status='submitted',plan__site__in=sites_for(user))
    if not user.has_perm('erp.view_beatplan') or not user.has_perm('erp.view_team_beats'):
        return qs.none()
    return approvable(qs,user,'approve_beatplan',site_field='plan__site',
        maker_fields=('created_by_id','recipient_id','plan__owner_id','plan__created_by_id','plan__submitted_by_id','plan__contributors__pk'))
