"""Scheme entitlements, payouts and dealer scheme statement."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from . import rewards as RW
from .models import SchemeEntitlementBatch, SchemeSettlement, SchemeEvaluation, Partner, Account
from .finance_access import permitted, require_company_finance
from .schemes import scope


def _banks():
    from .gl import ensure_chart
    ensure_chart()
    return Account.objects.filter(is_bank=True, active=True).exclude(system_key='cash').order_by('code')


@login_required
def payouts(request):
    permitted(request.user, 'view_schemeentitlementbatch')
    permitted(request.user, 'view_schemesettlement')
    ready = [e for e in scope(SchemeEvaluation.objects.filter(batch__isnull=True, version__status='approved'), request.user, 'version__scheme').select_related('version__scheme').order_by('-created_at')[:40]
             if (e.starts_on, e.ends_on) == (e.version.starts_on, e.version.ends_on)]
    return render(request, 'erp/scheme_payouts.html', {
        'title': 'Scheme entitlements & payouts', 'ready': ready,
        'batches': scope(SchemeEntitlementBatch.objects.select_related('version__scheme', 'created_by', 'decided_by'), request.user, 'version__scheme')[:60],
        'dealers': RW.payable_dealers(), 'settlements': SchemeSettlement.objects.select_related('partner', 'created_by', 'approved_by')[:60], 'banks': _banks(),
        'can': {'batch': request.user.has_perm('erp.add_schemeentitlementbatch'), 'pay': request.user.has_perm('erp.add_schemesettlement'),
                'decide_pay': request.user.has_perm('erp.approve_schemesettlement')}})


@login_required
@require_POST
def batch_new(request, evaluation_pk):
    require_company_finance(request.user)
    try:
        b = RW.create_batch(request.user, evaluation_pk, request.POST.get('note', ''))
        messages.success(request, f'{b} prepared for ₹{RW.inr(b.total, 2)}. A different user approves it.')
        return redirect('scheme_batch', pk=b.pk)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('scheme_evaluation', pk=evaluation_pk)


@login_required
def batch_detail(request, pk):
    permitted(request.user, 'view_schemeentitlementbatch')
    b = get_object_or_404(scope(SchemeEntitlementBatch.objects.select_related('version__scheme', 'evaluation', 'created_by', 'decided_by'), request.user, 'version__scheme'), pk=pk)
    u = request.user
    return render(request, 'erp/scheme_batch.html', {'title': str(b), 'b': b, 'rows': b.entitlements.select_related('partner', 'settlement'),
                                                     'can_decide': b.status == 'submitted' and u.has_perm('erp.approve_schemeentitlementbatch') and u.pk not in (b.created_by_id, b.evaluation.created_by_id)})


@login_required
@require_POST
def batch_decide(request, pk, decision):
    require_company_finance(request.user)
    try:
        b = RW.decide_batch(request.user, pk, decision == 'approve', request.POST.get('note', ''))
        messages.success(request, f'{b}: {b.get_status_display().lower()}.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('scheme_batch', pk=pk)


@login_required
@require_POST
def payout_new(request, partner_pk):
    permitted(request.user, 'add_schemesettlement')
    partner = get_object_or_404(Partner, pk=partner_pk)
    try:
        s = RW.propose_payout(request.user, partner, request.POST.get('mode', 'ledger'), _banks().filter(pk=request.POST.get('bank') or 0).first(), request.POST.get('note', ''))
        messages.success(request, f'{s.number} for ₹{RW.inr(s.amount, 2)} proposed. A different user approves it.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('scheme_payouts')


@login_required
@require_POST
def payout_decide(request, pk, decision):
    require_company_finance(request.user)
    try:
        s = RW.decide_payout(request.user, pk, decision == 'approve', request.POST.get('reference', ''), request.POST.get('note', ''))
        messages.success(request, f'{s.number}: {s.get_status_display().lower()}.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('scheme_payouts')


@login_required
def statement(request, partner_pk):
    permitted(request.user, 'view_schemeentitlementbatch')
    permitted(request.user, 'view_schemesettlement')
    partner = get_object_or_404(Partner, pk=partner_pk)
    rows = RW.statement(partner)
    return render(request, 'erp/scheme_statement.html', {'title': f'Scheme statement · {partner.name}', 'partner': partner, 'rows': rows,
                                                         'totals': {k: sum((r[k] for r in rows), RW.ZERO) for k in ('earned', 'recovered', 'paid', 'pending', 'balance')}})
