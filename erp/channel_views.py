"""Channel commission screens: rates (agreements) and runs for C&F, sales promoters, handling agents and distributors."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import channel_commission as C
from .models import ChannelRate, ChannelRun, Partner, CHANNEL_KINDS


def _date(v):
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def _dec(v):
    try:
        return Decimal(str(v).replace(',', '').strip()) if v not in (None, '') else None
    except InvalidOperation:
        return None


@login_required
def channel_commission(request):
    if not request.user.has_perm('erp.view_channelrate'):
        raise PermissionDenied
    if request.method == 'POST':
        data = {'partner': Partner.objects.filter(pk=request.POST.get('partner') or 0, kind__in=CHANNEL_KINDS).first(), 'basis': request.POST.get('basis'),
                'rate': _dec(request.POST.get('rate')), 'scope': request.POST.get('scope'), 'valid_from': _date(request.POST.get('valid_from')),
                'valid_to': _date(request.POST.get('valid_to')), 'note': request.POST.get('note', '')}
        try:
            rate = C.save_rate(request.user, data)
            if request.POST.get('submit'):
                C.submit_rate(request.user, rate.pk)
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        else:
            messages.success(request, f'Rate for {rate.partner.name} saved{" and sent for approval" if request.POST.get("submit") else " as draft"}.')
        return redirect('channel_commission')
    rates = ChannelRate.objects.select_related('partner', 'created_by', 'decided_by').order_by('status', 'partner__name')
    runs = ChannelRun.objects.select_related('created_by', 'decided_by')[:24]
    return render(request, 'erp/channel_commission.html', {
        'title': 'Channel commission', 'rates': rates, 'runs': runs,
        'partners': Partner.objects.filter(kind__in=CHANNEL_KINDS, active=True).order_by('kind', 'name'),
        'can_add': request.user.has_perm('erp.add_channelrate'), 'can_approve': request.user.has_perm('erp.approve_channelrate'),
        'can_run': request.user.has_perm('erp.add_channelrun') and request.user.has_perm('erp.access_company_finance'),
        'next_start': C.next_start(), 'today': timezone.localdate()})


@login_required
@require_POST
def channel_rate_action(request, pk, action):
    try:
        if action == 'submit':
            C.submit_rate(request.user, pk)
        elif action in ('approve', 'reject'):
            C.decide_rate(request.user, pk, action == 'approve', request.POST.get('note', ''))
        elif action == 'end':
            C.end_rate(request.user, pk, _date(request.POST.get('on')) or timezone.localdate())
        else:
            raise ValidationError('Unknown action.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, {'submit': 'Sent for approval.', 'approve': 'Rate approved.', 'reject': 'Rate sent back.', 'end': 'Rate ended.'}[action])
    return redirect('channel_commission')


@login_required
@require_POST
def channel_calculate(request):
    try:
        run = C.calculate(request.user, _date(request.POST.get('end')) or timezone.localdate())
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
        return redirect('channel_commission')
    messages.success(request, f'{run.number} calculated — a different user approves it to post the payable.')
    return redirect('channel_run', pk=run.pk)


@login_required
def channel_run(request, pk):
    if not request.user.has_perm('erp.view_channelrun'):
        raise PermissionDenied
    run = get_object_or_404(ChannelRun.objects.select_related('created_by', 'decided_by'), pk=pk)
    if request.method == 'POST':
        try:
            C.decide_run(request.user, pk, request.POST.get('decision') == 'approve', request.POST.get('note', ''))
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        else:
            messages.success(request, 'Run approved — the payable is posted and can be paid under Payments.' if request.POST.get('decision') == 'approve' else 'Run rejected.')
        return redirect('channel_run', pk=pk)
    return render(request, 'erp/channel_run.html', {'title': run.number, 'run': run, 'lines': run.lines.select_related('partner', 'rate'),
                                                     'can_decide': request.user.has_perm('erp.approve_channelrun') and request.user.has_perm('erp.access_company_finance') and run.status == 'draft'})
