"""Read-only pipeline health derived from retained records; no forecast or stage writes."""
from datetime import timedelta
from decimal import Decimal
from django.db.models import OuterRef, Subquery, Q
from django.utils import timezone
from .gst import money
from .models import PROJECT_WEIGHTS, ProjectActivity, ProjectEvent

STALE_DAYS = 14


def project_health_queryset(qs):
    return qs.annotate(
        last_contact_on=Subquery(ProjectActivity.objects.filter(project_id=OuterRef('pk')).order_by('-on', '-pk').values('on')[:1]),
        last_stage_review=Subquery(ProjectEvent.objects.filter(project_id=OuterRef('pk'), action__in=['Created', 'Stage changed', 'Reopened']).order_by('-created_at', '-pk').values('created_at')[:1]),
    ).order_by('-updated_at', '-pk')


def stale_projects(today):
    cutoff = today - timedelta(days=STALE_DAYS)
    return Q(last_contact_on__lt=cutoff) | Q(last_contact_on__isnull=True, created_at__date__lt=cutoff)


def project_health(project, today=None):
    today = today or timezone.localdate()
    created = timezone.localtime(project.created_at).date()
    reviewed = getattr(project, 'last_stage_review', None) or project.created_at
    last_contact = getattr(project, 'last_contact_on', None) or created
    risks = []
    if project.stage not in ['won', 'lost']:
        if not project.next_action_on:
            risks.append('No follow-up scheduled')
        elif project.next_action_on < today:
            risks.append('Follow-up overdue')
        if project.expected_close and project.expected_close < today:
            risks.append('Expected close missed')
        if not project.expected_close:
            risks.append('Expected close missing')
        if last_contact < today - timedelta(days=STALE_DAYS):
            risks.append(f'No activity in {STALE_DAYS} days')
    return {'risks': risks, 'days_open': max(0, (today - created).days),
            'stage_days': max(0, (today - timezone.localtime(reviewed).date()).days),
            'last_contact': last_contact, 'next_action_label': 'Closed' if project.stage in ['won', 'lost'] else 'Not scheduled'}


def forecast(qs):
    """Filtered open opportunities bucketed by expected close, keeping held weight zero."""
    buckets = {}
    for p in qs.exclude(stage__in=['won', 'lost']):
        key = p.expected_close.strftime('%Y-%m') if p.expected_close else 'Not scheduled'
        row = buckets.setdefault(key, {'month': key, 'count': 0, 'value': Decimal('0'), 'weighted': Decimal('0')})
        row['count'] += 1
        row['value'] += p.estimated_value
        row['weighted'] += money(p.estimated_value * PROJECT_WEIGHTS[p.stage] / 100)
    return [buckets[key] for key in sorted(buckets)]


def query_link(params, **changes):
    params = params.copy()
    for key in ['page', 'export']:
        params.pop(key, None)
    for key, value in changes.items():
        if value:
            params[key] = value
        else:
            params.pop(key, None)
    return '?' + params.urlencode()
