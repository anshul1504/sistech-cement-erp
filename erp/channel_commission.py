"""Channel commission: C&F, sales promoters, handling agents and distributors earn on their network's sales.

- Rate (agreement): ₹ per MT or % of net sales, on the whole network below the partner or only the partners directly under
  it (erp.network). Drafted by sales, approved by a different user. Only one approved rate per partner may cover a date.
- Base: posted tax invoices minus credit notes (before GST) of the partners counted, across all plants, from the rate's
  start to the run's end. The network is read as it stands when the run is calculated.
- Earn-once: each run calculates the cumulative commission and subtracts what earlier approved runs already gave on the
  same rate, so returns after a payout reduce the next run (a negative line is a recovery).
- Run approval (company finance, maker-checker) posts Dr Channel commission / Cr Trade payable (partner) / Cr TDS payable
  (194H, when the partner's TDS section is set) and records the TDS deduction for the challan. The payable is then paid
  through Payments like any supplier bill.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .finance_access import require_company_finance
from .gst import next_number
from .models import ChannelRate, ChannelRun, ChannelRunLine, Partner, Site, TdsDeduction, AuditEvent, CHANNEL_KINDS

ZERO = Decimal('0')
CENT = Decimal('0.01')


def _log(user, action, ref, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=ref, detail=(detail or '')[:250])


def _need(user, perm, finance=False):
    if finance:
        require_company_finance(user)
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


# ---------- Rates ----------

def _overlaps(rate):
    qs = ChannelRate.objects.filter(partner=rate.partner, status__in=['approved', 'submitted']).exclude(pk=rate.pk)
    end = rate.valid_to
    qs = qs.filter(Q(valid_to__isnull=True) | Q(valid_to__gte=rate.valid_from))
    if end:
        qs = qs.filter(valid_from__lte=end)
    return qs.exists()


@transaction.atomic
def save_rate(user, data, pk=None):
    _need(user, 'change_channelrate' if pk else 'add_channelrate')
    rate = ChannelRate.objects.select_for_update().get(pk=pk) if pk else ChannelRate(created_by=user)
    if pk and rate.status not in ('draft', 'rejected'):
        raise ValidationError('An approved rate cannot be edited — end it and add a new one.')
    problems = []
    partner = data.get('partner')
    if not partner or partner.kind not in CHANNEL_KINDS:
        problems.append('Choose a C&F, sales promoter, handling agent or distributor.')
    if data.get('basis') not in ('mt', 'pct'):
        problems.append('Choose ₹ per MT or % of sales.')
    r = data.get('rate')
    if r is None or r <= 0 or (data.get('basis') == 'pct' and r > 10) or (data.get('basis') == 'mt' and r > 2000):
        problems.append('Rate must be above 0 (at most 10 % of sales or ₹2,000 per MT).')
    if data.get('scope') not in ('network', 'direct'):
        problems.append('Choose whole network or direct partners only.')
    if not data.get('valid_from') or (data.get('valid_to') and data['valid_to'] < data['valid_from']):
        problems.append('Give a start date (and an end date after it).')
    if problems:
        raise ValidationError(problems)
    for k in ('partner', 'basis', 'rate', 'scope', 'valid_from', 'valid_to'):
        setattr(rate, k, data.get(k))
    rate.note, rate.status = (data.get('note') or '').strip()[:200], 'draft'
    rate.save()
    _log(user, 'Channel rate saved', f'channel-rate:{rate.pk}', str(rate))
    return rate


@transaction.atomic
def submit_rate(user, pk):
    _need(user, 'change_channelrate')
    rate = ChannelRate.objects.select_for_update().select_related('partner').get(pk=pk)
    if rate.status not in ('draft', 'rejected'):
        raise ValidationError('Only a draft rate can be submitted.')
    if _overlaps(rate):
        raise ValidationError(f'{rate.partner.name} already has a rate covering these dates — end it first.')
    rate.status = 'submitted'
    rate.save(update_fields=['status'])
    _log(user, 'Channel rate submitted', f'channel-rate:{rate.pk}', str(rate))
    return rate


@transaction.atomic
def decide_rate(user, pk, approve, note=''):
    _need(user, 'approve_channelrate')
    rate = ChannelRate.objects.select_for_update().select_related('partner').get(pk=pk)
    if rate.status != 'submitted':
        raise ValidationError('Only a submitted rate can be decided.')
    if rate.created_by_id == user.pk:
        raise ValidationError('A different user must approve the rate (maker-checker).')
    if not approve and not (note or '').strip():
        raise ValidationError('Say what must change.')
    if approve and _overlaps(rate):
        raise ValidationError(f'{rate.partner.name} already has an approved rate covering these dates.')
    rate.status, rate.decided_by, rate.decision_note = ('approved' if approve else 'rejected'), user, (note or '').strip()[:200]
    rate.save()
    _log(user, 'Channel rate approved' if approve else 'Channel rate sent back', f'channel-rate:{rate.pk}', note or str(rate))
    return rate


@transaction.atomic
def end_rate(user, pk, on):
    _need(user, 'approve_channelrate')
    rate = ChannelRate.objects.select_for_update().get(pk=pk)
    if rate.status != 'approved':
        raise ValidationError('Only an approved rate can be ended.')
    last = ChannelRunLine.objects.filter(rate=rate, run__status='approved').order_by('-run__end').first()
    if on < rate.valid_from or (last and on < last.run.end):
        raise ValidationError('End date must be after the start and after the last approved run.')
    rate.valid_to, rate.status = on, 'ended'
    rate.save()
    _log(user, 'Channel rate ended', f'channel-rate:{rate.pk}', f'{on}')
    return rate


# ---------- Calculation ----------

def counted_partners(rate):
    from .network import descendant_ids
    if rate.scope == 'direct':
        return set(Partner.objects.filter(parent=rate.partner).values_list('pk', flat=True))
    return descendant_ids(rate.partner)


def base(rate, end):
    """(net MT, net value, partners counted) of the counted partners from the rate's start to `end`."""
    from . import insights as I
    ids = counted_partners(rate)
    if not ids:
        return ZERO, ZERO, 0
    stop = min(end, rate.valid_to) if rate.valid_to else end
    rows = I.net_sales(list(Site.objects.all()), rate.valid_from, stop, 'partner')
    mt = sum((rows[k]['mt'] for k in ids if k in rows), ZERO)
    value = sum((rows[k]['value'] for k in ids if k in rows), ZERO)
    return mt.quantize(Decimal('0.001')), value.quantize(CENT), len(ids)


def _commission(rate, mt, value):
    return ((mt * rate.rate) if rate.basis == 'mt' else (value * rate.rate / 100)).quantize(CENT)


def next_start():
    last = ChannelRun.objects.filter(status='approved').order_by('-end').first()
    return last.end + timedelta(days=1) if last else None


@transaction.atomic
def calculate(user, end):
    _need(user, 'add_channelrun', finance=True)
    if ChannelRun.objects.filter(status='draft').exists():
        raise ValidationError('A calculated run is waiting for approval — approve or reject it first.')
    if end > timezone.localdate():
        raise ValidationError('A run cannot end in the future.')
    rates = list(ChannelRate.objects.filter(status__in=['approved', 'ended'], valid_from__lte=end).select_related('partner'))
    if not rates:
        raise ValidationError('No approved channel rate starts on or before this date.')
    start = next_start() or min(r.valid_from for r in rates)
    if end < start:
        raise ValidationError(f'Nothing to calculate: the next run starts {start:%d %b %Y}.')
    run = ChannelRun.objects.create(number=next_number('CHC', None, end), start=start, end=end, created_by=user)
    total = tds_total = ZERO
    for rate in rates:
        mt, value, n = base(rate, end)
        cumulative = _commission(rate, mt, value)
        prior = ChannelRunLine.objects.filter(rate=rate, run__status='approved').aggregate(t=Sum('amount'))['t'] or ZERO
        amount = cumulative - prior
        if not amount:
            continue
        p = rate.partner
        section = '194H' if p.tds_section == '194H' and p.tds_rate else ''
        tds = (amount * p.tds_rate / 100).quantize(Decimal('1')) if section and amount > 0 else ZERO
        ChannelRunLine.objects.create(run=run, partner=p, rate=rate, base_mt=mt, base_value=value, partners=n, cumulative=cumulative,
                                      prior=prior, amount=amount, tds=tds, tds_section=section)
        total += amount
        tds_total += tds
    if not run.lines.exists():
        run.delete()
        raise ValidationError('No new commission since the last approved run.')
    run.total, run.tds = total, tds_total
    run.save(update_fields=['total', 'tds'])
    _log(user, 'Channel commission calculated', run.number, f'{start} – {end} · ₹{total}')
    return run


@transaction.atomic
def decide_run(user, pk, approve, note=''):
    _need(user, 'approve_channelrun', finance=True)
    run = ChannelRun.objects.select_for_update().get(pk=pk)
    if run.status != 'draft':
        raise ValidationError('Only a calculated run can be decided.')
    if run.created_by_id == user.pk:
        raise ValidationError('A different user must approve the run (maker-checker).')
    if not approve and not (note or '').strip():
        raise ValidationError('Give the reason for rejecting.')
    if approve:
        from . import gl
        lines = []
        for l in run.lines.select_related('partner'):
            memo = f'{run.number} · {l.partner.code}'
            if l.amount > 0:
                lines.append((gl.acct('channel_commission'), l.amount, ZERO, None, memo))
                lines.append((gl.acct('ap'), ZERO, l.amount - l.tds, l.partner, memo))
                if l.tds:
                    lines.append((gl.acct('tds_payable'), ZERO, l.tds, None, f'TDS 194H {l.partner.code}'))
            else:
                lines.append((gl.acct('ap'), -l.amount, ZERO, l.partner, f'Recovery {memo}'))
                lines.append((gl.acct('channel_commission'), ZERO, -l.amount, None, f'Recovery {memo}'))
        gl.post(user, f'chc:{run.pk}', run.end, None, f'Channel commission {run.number} · {run.start:%d %b} – {run.end:%d %b %Y}', lines)
        for l in run.lines.filter(tds__gt=0).select_related('partner'):
            TdsDeduction.objects.create(number=next_number('TDS', None, run.end), payee=l.partner, channel_line=l, section=l.tds_section,
                                        rate=l.partner.tds_rate, base=l.amount, amount=l.tds, date=run.end, created_by=user,
                                        note=f'Channel commission {run.number}')
    run.status, run.decided_by, run.decided_at, run.decision_note = ('approved' if approve else 'rejected'), user, timezone.now(), (note or '').strip()[:200]
    run.save()
    _log(user, 'Channel commission approved & posted' if approve else 'Channel commission rejected', run.number, note or f'₹{run.total}')
    return run


def statement(partner):
    """Approved lines of one partner with what was paid against each."""
    from .treasury import allocated
    out = []
    for l in ChannelRunLine.objects.filter(partner=partner, run__status='approved').select_related('run', 'rate').order_by('-run__end'):
        paid = allocated('channel_line', l)
        out.append({'line': l, 'paid': paid, 'due': l.net - paid})
    return out
