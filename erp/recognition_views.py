"""Star programmes: programme setup, leaderboard, ranking, publishing and reward fulfilment; influencer usage entry and verification."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import recognition as RC
from .models import (RecognitionProgram, RecognitionLevel, InfluencerUsage, Influencer, Partner, Product, AuditEvent, RECOGNITION_AUDIENCES, CUSTOMER_KINDS)
from .services import permitted

DEFAULT_REWARDS = {'district': 'Domestic couple trip', 'state': 'International couple trip', 'national': 'Grand Champion Award — trophy & certificate'}


@login_required
def programs(request):
    permitted(request.user, 'view_recognitionprogram')
    P, errors = request.POST, []
    if request.method == 'POST':
        permitted(request.user, 'add_recognitionprogram')
        try:
            start, end = date.fromisoformat(P.get('starts_on')), date.fromisoformat(P.get('ends_on'))
            if end < start: raise ValidationError('End date cannot be before the start.')
            if P.get('audience') not in dict(RECOGNITION_AUDIENCES): raise ValidationError('Choose the programme.')
            if not (P.get('name') or '').strip(): raise ValidationError('Enter the programme name.')
            with transaction.atomic():
                p = RecognitionProgram.objects.create(name=P['name'].strip()[:120], audience=P['audience'], starts_on=start, ends_on=end,
                                                      exclude_overdue_days=int(P.get('overdue') or 0), notes=P.get('notes', '')[:500], created_by=request.user)
                for level in ('national', 'state', 'district'):
                    if P.get(f'{level}_on') == 'on':
                        RecognitionLevel.objects.create(program=p, level=level, winners=max(int(P.get(f'{level}_winners') or 1), 1),
                                                        minimum=Decimal(P.get(f'{level}_min') or 0), reward=(P.get(f'{level}_reward') or DEFAULT_REWARDS[level])[:120])
                AuditEvent.objects.create(actor=request.user, action='Recognition programme created', reference=p.name, detail=f'{p.get_audience_display()} · {start} – {end}')
            messages.success(request, f'{p.name} created.')
            return redirect('recognition_detail', pk=p.pk)
        except (ValueError, InvalidOperation):
            errors.append('Enter dates and numbers correctly.')
        except ValidationError as e:
            errors += e.messages
    today = timezone.localdate()
    return render(request, 'erp/recognition.html', {'title': 'Star programmes', 'programs': RecognitionProgram.objects.prefetch_related('levels', 'awards'),
                                                    'audiences': RECOGNITION_AUDIENCES, 'defaults': DEFAULT_REWARDS, 'errors': errors, 'post': P,
                                                    'year_start': date(today.year if today.month >= 4 else today.year - 1, 4, 1).isoformat(),
                                                    'year_end': date(today.year + 1 if today.month >= 4 else today.year, 3, 31).isoformat(),
                                                    'pending': InfluencerUsage.objects.filter(status='pending').count(),
                                                    'can_add': request.user.has_perm('erp.add_recognitionprogram')})


@login_required
def program_detail(request, pk):
    permitted(request.user, 'view_recognitionprogram')
    p = get_object_or_404(RecognitionProgram.objects.prefetch_related('levels'), pk=pk)
    board = sorted(RC.volumes(p), key=lambda r: -r[3])[:25] if p.status != 'published' else []
    board = [{'who': w, 'district': d, 'state': s, 'volume': v, 'ok': RC.eligible(p, w)} for w, d, s, v in board]
    u = request.user
    return render(request, 'erp/recognition_detail.html', {'title': p.name, 'p': p, 'board': board,
                                                           'awards': p.awards.select_related('partner', 'influencer'),
                                                           'can': {'rank': p.status != 'published' and u.has_perm('erp.change_recognitionprogram'),
                                                                   'publish': p.status == 'ranked' and u.has_perm('erp.publish_recognitionprogram') and p.ranked_by_id != u.pk,
                                                                   'fulfil': p.status == 'published' and u.has_perm('erp.publish_recognitionprogram')}})


@login_required
@require_POST
def program_action(request, pk, action):
    P = request.POST
    try:
        if action == 'rank':
            p = RC.rank(request.user, pk); msg = f'{p.awards.count()} winner(s) ranked. A different user publishes them.'
        elif action in ('publish', 'sendback'):
            RC.publish(request.user, pk, action == 'publish', P.get('note', '')); msg = 'Winners published.' if action == 'publish' else 'Ranking sent back.'
        elif action == 'fulfil':
            RC.fulfil(request.user, int(P.get('award') or 0), P.get('note', '')); msg = 'Reward marked as given.'
        else:
            raise ValidationError('Unknown action.')
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('recognition_detail', pk=pk)


@login_required
def usage(request):
    permitted(request.user, 'view_influencerusage')
    P, errors = request.POST, []
    influencers = Influencer.objects.filter(active=True).order_by('name')
    dealers = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).order_by('name')
    bags = Product.objects.filter(active=True, category='Cement', unit='BAG').order_by('name')
    if request.method == 'POST':
        try:
            if P.get('action') == 'verify':
                RC.verify_usage(request.user, int(P.get('usage') or 0), P.get('decision') == 'approve', P.get('note', ''))
                messages.success(request, 'Decision recorded.')
                return redirect('influencer_usage')
            inf, dealer, product = influencers.filter(pk=P.get('influencer') or 0).first(), dealers.filter(pk=P.get('dealer') or 0).first(), bags.filter(pk=P.get('product') or 0).first()
            if not (inf and dealer and product):
                raise ValidationError('Choose the mason / engineer, the dealer and the cement.')
            RC.record_usage(request.user, inf, dealer, product, int(P.get('bags') or 0), date.fromisoformat(P.get('date')), P.get('bill', ''), P.get('site', ''))
            messages.success(request, 'Usage recorded. A different user verifies it with the dealer.')
            return redirect('influencer_usage')
        except (ValueError, InvalidOperation):
            errors.append('Enter the bags and date correctly.')
        except ValidationError as e:
            errors += e.messages
    status = request.GET.get('status', 'pending')
    qs = InfluencerUsage.objects.select_related('influencer', 'dealer', 'product', 'created_by')
    if status: qs = qs.filter(status=status)
    return render(request, 'erp/influencer_usage.html', {'title': 'Influencer usage', 'rows': qs[:200], 'status': status, 'influencers': influencers, 'dealers': dealers,
                                                         'bags': bags, 'errors': errors, 'post': P, 'today': timezone.localdate().isoformat(),
                                                         'can_add': request.user.has_perm('erp.add_influencerusage'), 'can_verify': request.user.has_perm('erp.verify_influencerusage')})
