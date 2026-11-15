"""Star Dealer, Star Engineer and Star Mason & Contractor programmes (sistech.co.in): verified volume → district / state / national winners.

Rules:
- dealer volume = net MT invoiced in the programme year (invoice lines − returned quantity on credit notes), using bag weight;
- influencer volume = SISTECH bags recorded against a dealer bill and verified by a user other than the one who recorded it;
- dealers with an invoice overdue beyond the programme's limit at ranking time are not eligible (compliance);
- ranking awards national first, then state, then district; a participant receives only the highest award reached; each level has
  a number of winners per area and a minimum volume;
- the person who ran the ranking cannot publish it; publishing freezes the winners; rewards are then marked fulfilled one by one.
"""
from accounts.delegation import authorize, can_approve
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import (InfluencerUsage, RecognitionProgram, RecognitionLevel, RecognitionAward, InvoiceLine, CreditNoteLine, Partner, Influencer, AuditEvent,
                     INDIAN_STATES, CUSTOMER_KINDS, clean_place)

ZERO = Decimal('0')
AUDIENCE_KINDS = {'engineer': ['Engineer', 'Architect'], 'mason': ['Mason', 'Contractor', 'Builder']}
STATE_NAMES = dict(INDIAN_STATES)


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied



def makers(program):
    return {program.created_by_id,program.ranked_by_id,*program.contributors.values_list('pk',flat=True)} - {None}


def can_publish(user,program):
    return (program.status=='ranked' and user.has_perm('erp.view_recognitionprogram') and user.pk not in makers(program)
        and can_approve(user,'publish_recognitionprogram',makers=makers(program)))


def can_verify_usage(user,usage):
    return (usage.status=='pending' and user.has_perm('erp.view_influencerusage') and user.pk!=usage.created_by_id
        and can_approve(user,'verify_influencerusage',makers=[usage.created_by_id]))


def _log(user, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=reference, detail=detail[:250])


# ---------- Influencer usage ----------

@transaction.atomic
def record_usage(user, influencer, dealer, product, bags, on, bill, site_location=''):
    _need(user, 'add_influencerusage')
    problems = []
    if dealer.kind not in CUSTOMER_KINDS:
        problems.append('Choose the dealer who sold the cement.')
    if product.category != 'Cement' or product.unit != 'BAG':
        problems.append('Usage is recorded in SISTECH cement bags.')
    if not bags or int(bags) <= 0:
        problems.append('Enter the number of bags.')
    if on > timezone.localdate():
        problems.append('Date cannot be in the future.')
    bill = (bill or '').strip().upper()
    if not bill:
        problems.append("Enter the dealer's bill / slip number as evidence.")
    elif InfluencerUsage.objects.filter(dealer=dealer, bill_reference=bill, influencer=influencer).exclude(status='rejected').exists():
        problems.append(f'Bill {bill} is already recorded for {influencer.name}.')
    if not influencer.active:
        problems.append(f'{influencer.name} is inactive.')
    if problems:
        raise ValidationError(problems)
    u = InfluencerUsage.objects.create(influencer=influencer, dealer=dealer, product=product, bags=int(bags), date=on, bill_reference=bill[:40],
                                       site_location=(site_location or '')[:150], created_by=user)
    _log(user, 'Influencer usage recorded', influencer.code, f'{bags} bags · {dealer.name} · bill {bill}')
    return u


@transaction.atomic
def verify_usage(user, pk, approve, note=''):
    _need(user, 'view_influencerusage')
    u = InfluencerUsage.objects.select_for_update().select_related('influencer').get(pk=pk)
    if u.status != 'pending':
        raise ValidationError('Already decided.')
    if u.created_by_id == user.pk:
        raise ValidationError('The person who recorded the usage cannot verify it.')
    authorize(user,'verify_influencerusage',makers=[u.created_by_id],reference=f'usage:{u.pk}')
    if not approve and not (note or '').strip():
        raise ValidationError('Give the reason for rejecting (e.g. bill not found with dealer).')
    u.status, u.verified_by, u.verified_at, u.note = ('verified' if approve else 'rejected'), user, timezone.now(), (note or '').strip()[:200]
    u.save()
    _log(user, 'Influencer usage verified' if approve else 'Influencer usage rejected', u.influencer.code, f'{u.bags} bags · {u.bill_reference}' + (f' · {note}' if note else ''))
    return u


# ---------- Volumes ----------

def _mt(product, qty):
    from .stock import mt_factor
    f = mt_factor(product)
    return Decimal(qty) * f if f is not None else ZERO


def volumes(program):
    """[(participant, district, state, volume)] for everyone with volume > 0."""
    out = defaultdict(lambda: ZERO)
    if program.audience == 'dealer':
        for l in InvoiceLine.objects.filter(invoice__date__range=(program.starts_on, program.ends_on), invoice__partner__kind__in=CUSTOMER_KINDS).select_related('invoice__partner', 'product'):
            out[l.invoice.partner] += _mt(l.product, l.quantity)
        for l in CreditNoteLine.objects.filter(credit_note__invoice__date__range=(program.starts_on, program.ends_on), quantity__gt=0).select_related('credit_note__partner', 'invoice_line__product'):
            out[l.credit_note.partner] -= _mt(l.invoice_line.product, l.quantity)
        return [(p, clean_place(p.district), p.state, v.quantize(Decimal('0.001'))) for p, v in out.items() if v > 0]
    for u in InfluencerUsage.objects.filter(status='verified', date__range=(program.starts_on, program.ends_on),
                                            influencer__kind__in=AUDIENCE_KINDS[program.audience]).select_related('influencer'):
        out[u.influencer] += u.bags
    return [(i, clean_place(i.district), i.state, Decimal(v)) for i, v in out.items() if v > 0]


def eligible(program, participant):
    if program.audience != 'dealer' or not program.exclude_overdue_days:
        return True
    from .ledger import overdue
    return not overdue(participant, grace_days=program.exclude_overdue_days)


# ---------- Ranking & publishing ----------

@transaction.atomic
def rank(user, pk):
    _need(user, 'change_recognitionprogram')
    p = RecognitionProgram.objects.select_for_update().get(pk=pk)
    if p.status == 'published':
        raise ValidationError('Winners are already published.')
    if p.ends_on > timezone.localdate():
        raise ValidationError('Rank after the programme year ends.')
    rules = {r.level: r for r in p.levels.all()}
    if not rules:
        raise ValidationError('Set the national / state / district awards first.')
    p.contributors.add(user,p.created_by)
    if p.ranked_by_id:p.contributors.add(p.ranked_by_id)
    p.awards.all().delete()
    rows = [r for r in volumes(p) if eligible(p, r[0])]
    taken = set()
    made = 0
    for level in ('national', 'state', 'district'):
        rule = rules.get(level)
        if not rule:
            continue
        groups = defaultdict(list)
        for who, district, state, vol in rows:
            if who in taken or vol < rule.minimum:
                continue
            area = 'India' if level == 'national' else STATE_NAMES.get(state, state or 'Unknown state') if level == 'state' else (district or 'Unknown district')
            groups[area].append((who, vol))
        for area, items in groups.items():
            items.sort(key=lambda x: (-x[1], str(x[0])))
            for i, (who, vol) in enumerate(items[:rule.winners], 1):
                RecognitionAward.objects.create(program=p, level=level, area=area, rank=i, volume=vol, reward=rule.reward,
                                                **({'partner': who} if isinstance(who, Partner) else {'influencer': who}))
                taken.add(who)
                made += 1
    p.status, p.ranked_by, p.ranked_at = 'ranked', user, timezone.now()
    p.save()
    _log(user, 'Recognition ranked', f'STAR-{p.pk:05d}', f'{made} winner(s) from {len(rows)} eligible participant(s)')
    return p


@transaction.atomic
def publish(user, pk, approve, note=''):
    _need(user, 'view_recognitionprogram')
    p = RecognitionProgram.objects.select_for_update().get(pk=pk)
    if p.status != 'ranked':
        raise ValidationError('Run the ranking first.')
    if user.pk in makers(p):
        raise ValidationError('The creator or a ranking contributor cannot publish this programme (maker-checker).')
    authorize(user,'publish_recognitionprogram',makers=makers(p),reference=f'STAR-{p.pk:05d}')
    if approve:
        p.status, p.published_by, p.published_at = 'published', user, timezone.now()
    else:
        if not (note or '').strip():
            raise ValidationError('Give the reason for sending the ranking back.')
        p.awards.all().delete()
        p.status = 'open'
    p.save()
    _log(user, 'Recognition published' if approve else 'Recognition sent back', f'STAR-{p.pk:05d}', note or f'{p.awards.count()} winner(s)')
    return p


@transaction.atomic
def fulfil(user, award_pk, note):
    _need(user, 'publish_recognitionprogram')
    _need(user, 'view_recognitionprogram')
    header=RecognitionAward.objects.only('program_id').get(pk=award_pk)
    RecognitionProgram.objects.select_for_update().get(pk=header.program_id)
    a = RecognitionAward.objects.select_for_update(of=('self',)).select_related('program').get(pk=award_pk)
    if a.program.status != 'published':
        raise ValidationError('Publish the winners first.')
    if not (note or '').strip():
        raise ValidationError('Record how the reward was given (e.g. trip dates, ticket numbers).')
    note=note.strip()[:200]
    if a.fulfilled:
        if a.fulfilment_note!=note:raise ValidationError('This reward already has retained delivery evidence; it cannot be overwritten.')
        return a
    a.fulfilled, a.fulfilment_note, a.fulfilled_by, a.fulfilled_at = True, note, user, timezone.now()
    a.save()
    _log(user, 'Recognition reward given', f'STAR-{a.program_id:05d}', f'{a.participant} · {a.reward} · {note}')
    return a
