"""Collections work (SRS FR-AR-007): follow-up log, promise-to-pay tracking, disputes and the daily collection list.

Promise status is derived, never typed: kept when posted receipts dated from the promise date up to the promised date reach
the promised amount; broken when the promised date has passed without that; otherwise due / upcoming.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import CollectionFollowUp, Receipt, AuditEvent, CUSTOMER_KINDS, FOLLOWUP_KINDS, DISPUTE_REASONS

ZERO = Decimal('0')


def _need(user, perm):
    if not (user.has_perm('erp.' + perm) and user.has_perm('erp.access_company_finance')):
        raise PermissionDenied


def promise_status(f, today=None):
    if f.kind != 'promise':
        return None
    today = today or timezone.localdate()
    paid = Receipt.objects.filter(partner=f.partner, status='posted', date__gte=f.on, date__lte=max(f.promised_date, f.on)).aggregate(t=Sum('amount'))['t'] or ZERO
    if paid >= f.promised_amount:
        return 'kept'
    if f.promised_date < today:
        return 'broken'
    return 'due' if f.promised_date == today else 'upcoming'


@transaction.atomic
def record(user, partner, kind, note, promised_amount=None, promised_date=None, dispute_reason='', next_action_on=None, owner=None):
    _need(user, 'add_receipt')
    today = timezone.localdate()
    problems = []
    if partner.kind not in CUSTOMER_KINDS:
        problems.append('Follow-ups are for customers.')
    if kind not in dict(FOLLOWUP_KINDS):
        problems.append('Choose what happened.')
    if not (note or '').strip():
        problems.append('Write what the customer said.')
    if kind == 'promise':
        if not promised_amount or promised_amount <= 0:
            problems.append('Enter the promised amount.')
        if not promised_date or promised_date < today:
            problems.append('Enter the promised payment date (today or later).')
    if kind == 'dispute' and dispute_reason not in dict(DISPUTE_REASONS) or kind == 'dispute' and not dispute_reason:
        problems.append('Choose the dispute reason.')
    if next_action_on and next_action_on < today:
        problems.append('Next follow-up cannot be in the past.')
    if problems:
        raise ValidationError(problems)
    f = CollectionFollowUp.objects.create(partner=partner, kind=kind, note=note.strip()[:300], promised_amount=promised_amount if kind == 'promise' else None,
                                          promised_date=promised_date if kind == 'promise' else None, dispute_reason=dispute_reason if kind == 'dispute' else '',
                                          next_action_on=next_action_on, owner=owner or user, created_by=user)
    detail = f'{f.get_kind_display()}: {f.note}' + (f' · ₹{promised_amount} by {promised_date}' if kind == 'promise' else '')
    AuditEvent.objects.create(actor=user, action='Collection follow-up', reference=partner.code, detail=detail[:250])
    return f


@transaction.atomic
def resolve_dispute(user, pk, resolution):
    _need(user, 'add_receipt')
    f = CollectionFollowUp.objects.select_for_update().get(pk=pk)
    if f.kind != 'dispute' or f.resolved:
        raise ValidationError('Only an open dispute can be resolved.')
    if not (resolution or '').strip():
        raise ValidationError('Write how it was settled (credit note, explained, …).')
    f.resolved, f.resolution = True, resolution.strip()[:200]
    f.save()
    AuditEvent.objects.create(actor=user, action='Dispute resolved', reference=f.partner.code, detail=f.resolution)
    return f


def worklist(user=None, today=None):
    """Customers to chase today: promises due/broken, follow-ups due, open disputes, and overdue customers nobody has called in 7 days."""
    from . import insights
    today = today or timezone.localdate()
    fu = CollectionFollowUp.objects.select_related('partner', 'owner')
    if user is not None:
        fu = fu.filter(owner=user)
    promises = [dict(f=f, state=promise_status(f, today)) for f in fu.filter(kind='promise', promised_date__gte=today - timedelta(days=30))]
    due = [p for p in promises if p['state'] in ('due', 'broken')]
    followups = list(fu.filter(next_action_on__lte=today).exclude(kind='promise')[:50])
    disputes = list(fu.filter(kind='dispute', resolved=False))
    recent = set(CollectionFollowUp.objects.filter(on__gte=today - timedelta(days=7)).values_list('partner', flat=True))
    r = insights.receivables(insights.parse_period({'p': 'mtd'}), today)
    untouched = [x for x in r['overdue_dealers'] if x['partner'].pk not in recent] if user is None else []
    kept = [p for p in promises if p['state'] == 'kept']
    decided = [p for p in promises if p['state'] in ('kept', 'broken')]
    return {'due': sorted(due, key=lambda p: p['f'].promised_date), 'followups': followups, 'disputes': disputes, 'untouched': untouched,
            'kept_pct': round(len(kept) * 100 / len(decided)) if decided else None, 'promised_open': sum((p['f'].promised_amount for p in promises if p['state'] in ('due', 'upcoming')), ZERO)}
