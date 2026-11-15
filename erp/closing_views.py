"""Reviewable month-end and year-end workflows for central finance."""
from accounts.delegation import can_approve
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import closing, gl
from .finance_access import permitted
from .models import Account, AuditEvent, FinancialPeriod, FinancialYearClose


@login_required
def period_detail(request, pk):
    permitted(request.user, 'view_financials')
    period = get_object_or_404(FinancialPeriod, pk=pk)
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action == 'bank':
                bank = get_object_or_404(Account, pk=request.POST.get('bank') or 0, is_bank=True)
                closing.confirm_bank(request.user, pk, bank, request.POST.get('balance'), request.POST.get('reference', ''))
                messages.success(request, 'Month-end statement balance recorded. Prepare a fresh review if evidence changed.')
            elif action == 'prepare':
                closing.prepare_period(request.user, period.start, request.POST.get('note', ''),
                    tax_reviewed=request.POST.get('tax_reviewed') == 'on', stock_reviewed=request.POST.get('stock_reviewed') == 'on', bank_reviewed=request.POST.get('bank_reviewed') == 'on')
                messages.success(request, 'Review prepared. A different finance approver can close the month.')
            else:
                raise Http404
        except (ValueError, TypeError, InvalidOperation):
            messages.error(request, 'Choose a bank and enter a valid balance.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        return redirect('period_detail', pk=pk)
    result = closing.checklist(period)
    review = period.reviews.select_related('prepared_by', 'approved_by').order_by('-prepared_at', '-pk').first()
    return render(request, 'erp/period_detail.html', dict(title=f'{period} close review', period=period, result=result, review=review,
        reviews=period.reviews.select_related('prepared_by', 'approved_by').order_by('-prepared_at', '-pk'),
        can_prepare=request.user.has_perm('erp.prepare_periodclose') and period.status == 'open',
        can_approve=can_approve(request.user, 'close_period', makers=(review.prepared_by_id,) if review else ()) and period.status == 'open' and review and not review.approved_at and review.prepared_by_id != request.user.pk and not result['blockers'],
        can_reopen=can_approve(request.user, 'close_period') and period.status == 'closed',
        banks=Account.objects.filter(is_bank=True).exclude(system_key='cash'),
        history=AuditEvent.objects.filter(reference=str(period), action__startswith='Period').select_related('actor').order_by('-created_at')[:20]))


@login_required
def years(request):
    permitted(request.user, 'view_financialyearclose')
    if request.method == 'POST':
        try:
            row = closing.prepare_year(request.user, date.fromisoformat(request.POST.get('on', '')), request.POST.get('note', ''))
        except (ValueError, TypeError):
            messages.error(request, 'Choose a valid financial-year date.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        else:
            messages.success(request, 'Year-end proposal prepared for independent approval.')
            return redirect('year_detail', pk=row.pk)
        return redirect('year_closes')
    current_start, _ = closing.year_bounds(timezone.localdate())
    previous_end = current_start - timedelta(days=1)
    start, end = closing.year_bounds(previous_end)
    return render(request, 'erp/year_closes.html', dict(title='Year-end carry-forward', years=FinancialYearClose.objects.select_related('prepared_by', 'decided_by'),
        previous_end=previous_end, start=start, end=end, can_prepare=request.user.has_perm('erp.prepare_yearclose')))


@login_required
def year_detail(request, pk):
    permitted(request.user, 'view_financialyearclose')
    row = get_object_or_404(FinancialYearClose.objects.select_related('prepared_by', 'decided_by', 'reopen_requested_by'), pk=pk)
    groups, profit = closing.year_preview(row.start, row.end)
    from .models import Site
    accounts = {a.pk: a for a in Account.objects.all()}
    sites = {s.pk: s for s in Site.objects.all()}
    preview = [dict(site=sites.get(site_id), lines=[dict(account=accounts[aid], balance=net) for aid, net in balances], profit=-sum((net for _, net in balances), Decimal(0))) for site_id, balances in groups.items()]
    return render(request, 'erp/year_detail.html', dict(title=f'Year close {row.start.year}', year=row, preview=preview, profit=profit,
        entries=row.entries.select_related('site', 'reversal_of'),
        submissions=row.history.select_related('actor'),
        months=FinancialPeriod.objects.filter(start__gte=row.start, end__lte=row.end),
        history=AuditEvent.objects.filter(reference=str(row)).select_related('actor').order_by('-created_at')[:30],
        can_decide=row.status == 'proposed' and can_approve(request.user, 'approve_yearclose', makers=(row.prepared_by_id,)) and row.prepared_by_id != request.user.pk,
        can_request_reopen=row.status == 'closed' and request.user.has_perm('erp.prepare_yearclose'),
        can_decide_reopen=row.status == 'reopen_requested' and can_approve(request.user, 'approve_yearclose', makers=(row.prepared_by_id, row.reopen_requested_by_id)) and row.reopen_requested_by_id != request.user.pk))


@login_required
@require_POST
def year_action(request, pk, action):
    permitted(request.user, 'view_financialyearclose')
    get_object_or_404(FinancialYearClose, pk=pk)
    reason = request.POST.get('reason', '')
    try:
        if action in ('approve', 'reject'):
            closing.decide_year(request.user, pk, action == 'approve', reason)
        elif action == 'request_reopen':
            closing.request_year_reopen(request.user, pk, reason)
        elif action in ('approve_reopen', 'reject_reopen'):
            closing.approve_year_reopen(request.user, pk, action == 'approve_reopen', reason)
        else:
            raise Http404
        messages.success(request, 'Year-end decision recorded in the audit trail.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('year_detail', pk=pk)
