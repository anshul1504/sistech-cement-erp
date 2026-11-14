"""Help & support tickets.

Any signed-in user raises a ticket (with the screen it came from); the support team (handle_supportticket) assigns,
answers (public reply or internal note), resolves with a resolution note. The raiser can reply, accept the resolution
(closing it with a 1–5 rating) or reopen within 7 days. SLA = response-and-fix target by priority. Every step is audited.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .gst import next_number
from .models import SupportTicket, TicketMessage, AuditEvent, TICKET_SLA_HOURS, TICKET_CATEGORIES, TICKET_PRIORITIES

REOPEN_DAYS = 7


def is_agent(user):
    return user.has_perm('erp.handle_supportticket')


def visible(user):
    qs = SupportTicket.objects.select_related('raised_by', 'assigned_to')
    return qs if is_agent(user) else qs.filter(raised_by=user)


def _log(user, t, action, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=t.number, detail=detail[:250])


@transaction.atomic
def raise_ticket(user, subject, description, category='problem', priority='normal', module='', page_url=''):
    subject, description = (subject or '').strip(), (description or '').strip()
    problems = []
    if not subject:
        problems.append('Write a short subject.')
    if len(description) < 10:
        problems.append('Describe what happened, what you expected and the document number if any.')
    if category not in dict(TICKET_CATEGORIES):
        problems.append('Choose what kind of help you need.')
    if priority not in dict(TICKET_PRIORITIES):
        problems.append('Choose the priority.')
    if problems:
        raise ValidationError(problems)
    page_url = page_url if (page_url or '').startswith('/') else ''
    t = SupportTicket.objects.create(number=next_number('HLP'), subject=subject[:150], category=category, priority=priority, module=(module or '')[:80],
                                     page_url=page_url[:300], raised_by=user, due_at=timezone.now() + timedelta(hours=TICKET_SLA_HOURS[priority]))
    TicketMessage.objects.create(ticket=t, author=user, body=description)
    _log(user, t, 'Support ticket raised', f'{t.get_priority_display()} · {subject}')
    return t


def _lock(user, pk):
    t = SupportTicket.objects.select_for_update().get(pk=pk)
    if not (is_agent(user) or t.raised_by_id == user.pk):
        raise PermissionDenied
    return t


@transaction.atomic
def reply(user, pk, body, internal=False):
    t = _lock(user, pk)
    body = (body or '').strip()
    if not body:
        raise ValidationError('Write the message.')
    if t.status == 'closed':
        raise ValidationError('The ticket is closed — raise a new one.')
    agent = is_agent(user) and user.pk != t.raised_by_id
    if internal and not agent:
        raise PermissionDenied
    TicketMessage.objects.create(ticket=t, author=user, body=body, internal=internal)
    if agent and not internal:
        t.first_response_at = t.first_response_at or timezone.now()
        t.status = 'waiting' if t.status in ('open', 'progress') else t.status
        if not t.assigned_to_id:
            t.assigned_to = user
    elif not agent and t.status in ('waiting', 'resolved'):
        t.status = 'progress' if t.assigned_to_id else 'open'
        t.resolved_at = None
    t.save()
    _log(user, t, 'Support note added' if internal else 'Support reply', body[:200])
    return t


@transaction.atomic
def assign(user, pk, agent):
    if not is_agent(user):
        raise PermissionDenied
    t = _lock(user, pk)
    if t.status in ('resolved', 'closed'):
        raise ValidationError('Reopen the ticket first.')
    if agent is None or not is_agent(agent) or not agent.is_active:
        raise ValidationError('Choose an active member of the support team.')
    t.assigned_to, t.status = agent, 'progress'
    t.save()
    _log(user, t, 'Support ticket assigned', agent.get_username())
    return t


@transaction.atomic
def resolve(user, pk, resolution):
    if not is_agent(user):
        raise PermissionDenied
    t = _lock(user, pk)
    resolution = (resolution or '').strip()
    if t.status in ('resolved', 'closed'):
        raise ValidationError('Already resolved.')
    if not resolution:
        raise ValidationError('Write what was done, so the user and others can follow it.')
    t.status, t.resolution, t.resolved_at = 'resolved', resolution[:300], timezone.now()
    t.first_response_at = t.first_response_at or t.resolved_at
    t.assigned_to = t.assigned_to or user
    t.save()
    TicketMessage.objects.create(ticket=t, author=user, body='Resolved: ' + resolution)
    _log(user, t, 'Support ticket resolved', resolution[:200])
    return t


@transaction.atomic
def close(user, pk, rating=None):
    t = _lock(user, pk)
    if t.raised_by_id != user.pk and not is_agent(user):
        raise PermissionDenied
    if t.status != 'resolved':
        raise ValidationError('Only a resolved ticket can be closed.')
    if rating is not None and rating not in range(1, 6):
        raise ValidationError('Rate from 1 to 5.')
    t.status, t.rating = 'closed', rating if t.raised_by_id == user.pk else t.rating
    t.save()
    _log(user, t, 'Support ticket closed', f'rating {rating}' if rating else '')
    return t


@transaction.atomic
def reopen(user, pk, reason):
    t = _lock(user, pk)
    if t.status not in ('resolved', 'closed'):
        raise ValidationError('The ticket is still open.')
    if t.resolved_at and timezone.now() - t.resolved_at > timedelta(days=REOPEN_DAYS):
        raise ValidationError(f'Resolved more than {REOPEN_DAYS} days ago — raise a new ticket.')
    if not (reason or '').strip():
        raise ValidationError('Say what is still wrong.')
    TicketMessage.objects.create(ticket=t, author=user, body='Reopened: ' + reason.strip())
    t.status, t.resolved_at, t.rating = ('progress' if t.assigned_to_id else 'open'), None, None
    t.due_at = timezone.now() + timedelta(hours=TICKET_SLA_HOURS[t.priority])
    t.save()
    _log(user, t, 'Support ticket reopened', reason[:200])
    return t


def stats(user):
    qs = visible(user).select_related(None)
    open_ = qs.filter(status__in=['open', 'progress', 'waiting'])
    closed = qs.filter(resolved_at__isnull=False, resolved_at__gte=timezone.now() - timedelta(days=30))
    within = sum(1 for r, d in closed.values_list('resolved_at', 'due_at') if r <= d)
    rated = list(qs.filter(rating__isnull=False).values_list('rating', flat=True))
    return {'open': open_.count(), 'overdue': open_.filter(status__in=['open', 'progress'], due_at__lt=timezone.now()).count(),
            'unassigned': open_.filter(assigned_to__isnull=True).count(), 'mine': open_.filter(Q(assigned_to=user)).count(),
            'sla_pct': round(within * 100 / closed.count()) if closed.count() else None, 'rating': round(sum(rated) / len(rated), 1) if rated else None}
