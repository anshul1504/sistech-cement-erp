"""Plant maintenance: equipment, breakdown / preventive jobs and preventive schedules.

Job flow: reported → started (assigned technician) → done (work, spares, downtime) → verified and closed by a different
user (not the person who did the work). Preventive plans open a job when due (button or `open_due_maintenance` command)
and move to the next due date when the job is closed. MTTR = average start→finish of closed breakdowns; MTBF = average days
between breakdowns of the same equipment.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .gst import next_number
from .models import Equipment, MaintenancePlan, MaintenanceJob, AuditEvent
from .services import require_site


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, job, action, detail=''):
    AuditEvent.objects.create(actor=user, site=job.equipment.site, action=action, reference=job.number, detail=detail[:250])


@transaction.atomic
def report(user, equipment, kind, description, priority='normal', due_on=None, plan=None):
    _need(user, 'add_maintenancejob')
    require_site(user, equipment.site)
    if kind not in ('breakdown', 'preventive', 'inspection'):
        raise ValidationError('Choose the job type.')
    if not (description or '').strip():
        raise ValidationError('Describe the problem or the task.')
    if not equipment.active:
        raise ValidationError(f'{equipment} is inactive.')
    job = MaintenanceJob.objects.create(number=next_number('MNT', equipment.site), equipment=equipment, plan=plan, kind=kind, priority=priority if kind != 'breakdown' or priority != 'low' else 'normal',
                                        description=description.strip()[:300], due_on=due_on or (timezone.localdate() if kind == 'breakdown' else None), reported_by=user)
    _log(user, job, 'Maintenance job opened', f'{job.get_kind_display()} · {equipment} · {job.description}')
    return job


def _lock(user, pk):
    job = MaintenanceJob.objects.select_for_update().select_related('equipment__site').get(pk=pk)
    require_site(user, job.equipment.site)
    return job


@transaction.atomic
def start(user, pk, technician=None):
    _need(user, 'change_maintenancejob')
    job = _lock(user, pk)
    if job.status != 'open':
        raise ValidationError('Only an open job can be started.')
    job.status, job.started_at, job.assigned_to = 'progress', timezone.now(), technician or user
    job.save()
    _log(user, job, 'Maintenance started', job.assigned_to.get_username())
    return job


@transaction.atomic
def finish(user, pk, work_done, spares='', downtime_minutes=0):
    _need(user, 'change_maintenancejob')
    job = _lock(user, pk)
    if job.status != 'progress':
        raise ValidationError('Start the job before marking it done.')
    if not (work_done or '').strip():
        raise ValidationError('Describe the work done.')
    if downtime_minutes < 0 or downtime_minutes > 60 * 24 * 60:
        raise ValidationError('Enter the production downtime in minutes.')
    job.status, job.finished_at, job.work_done, job.spares, job.downtime_minutes = 'done', timezone.now(), work_done.strip()[:300], (spares or '').strip()[:200], downtime_minutes
    job.save()
    _log(user, job, 'Maintenance done', f'{job.work_done} · downtime {downtime_minutes} min')
    return job


@transaction.atomic
def verify(user, pk, accept, note=''):
    _need(user, 'verify_maintenancejob')
    job = _lock(user, pk)
    if job.status != 'done':
        raise ValidationError('Only a completed job can be verified.')
    if user.pk == (job.assigned_to_id or 0):
        raise ValidationError('The technician cannot verify their own work.')
    if not accept and not (note or '').strip():
        raise ValidationError('Say what is still wrong.')
    if accept:
        job.status, job.verified_by, job.verified_at, job.note = 'closed', user, timezone.now(), (note or '')[:200]
        if job.plan_id:
            plan = MaintenancePlan.objects.select_for_update().get(pk=job.plan_id)
            plan.next_due = max(plan.next_due, timezone.localdate()) + timedelta(days=plan.every_days)
            plan.save(update_fields=['next_due'])
    else:
        job.status, job.note = 'progress', note.strip()[:200]
    job.save()
    _log(user, job, 'Maintenance verified' if accept else 'Maintenance sent back', note)
    return job


@transaction.atomic
def cancel(user, pk, reason):
    _need(user, 'change_maintenancejob')
    job = _lock(user, pk)
    if job.status in ('closed', 'cancelled'):
        raise ValidationError('The job is already finished.')
    if not (reason or '').strip():
        raise ValidationError('Give the reason.')
    job.status, job.note = 'cancelled', reason.strip()[:200]
    job.save()
    _log(user, job, 'Maintenance cancelled', reason)
    return job


def open_due(user, sites=None, today=None):
    """Open one preventive job for every active plan that is due and has no open job."""
    today = today or timezone.localdate()
    plans = MaintenancePlan.objects.filter(active=True, next_due__lte=today, equipment__active=True).select_related('equipment__site')
    if sites is not None:
        plans = plans.filter(equipment__site__in=sites)
    made = []
    for p in plans:
        if p.jobs.filter(status__in=['open', 'progress', 'done']).exists():
            continue
        made.append(report(user, p.equipment, 'preventive', p.task, 'normal', p.next_due, p))
    return made


def kpis(sites, days=90):
    since = timezone.now() - timedelta(days=days)
    jobs = MaintenanceJob.objects.filter(equipment__site__in=sites)
    closed = jobs.filter(kind='breakdown', status='closed', finished_at__gte=since).exclude(started_at=None)
    hours = [(j.finished_at - j.started_at).total_seconds() / 3600 for j in closed]
    gaps = []
    for e in Equipment.objects.filter(site__in=sites):
        times = sorted(j.reported_at for j in jobs.filter(equipment=e, kind='breakdown', reported_at__gte=since))
        gaps += [(b - a).total_seconds() / 86400 for a, b in zip(times, times[1:])]
    open_ = jobs.filter(status__in=['open', 'progress'])
    today = timezone.localdate()
    return {'open': open_.count(), 'breakdowns': open_.filter(kind='breakdown').count(), 'overdue': open_.filter(due_on__lt=today).count(),
            'verify': jobs.filter(status='done').count(), 'mttr': round(sum(hours) / len(hours), 1) if hours else None,
            'mtbf': round(sum(gaps) / len(gaps), 1) if gaps else None,
            'pm_due': MaintenancePlan.objects.filter(active=True, equipment__site__in=sites, next_due__lte=today + timedelta(days=7)).count(),
            'downtime_h': round(sum(j.downtime_minutes for j in jobs.filter(finished_at__gte=since)) / 60, 1)}
