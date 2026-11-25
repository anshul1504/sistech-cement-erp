"""Sales commission screens: plans, runs, approval and each person's own statement."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import commission as C
from .finance_access import permitted as finance_permitted
from .services import permitted
from .models import CommissionPlan, CommissionRun, CommissionLine, Site, AuditEvent, COMMISSION_BASES


def _people():
    return get_user_model().objects.filter(is_active=True).order_by('first_name', 'username')


@login_required
def plans(request):
    permitted(request.user, 'view_commissionplan')
    return render(request, 'erp/commission_plans.html', {'title': 'Sales commission', 'rows': CommissionPlan.objects.select_related('site').prefetch_related('beneficiaries'),
                                                         'runs': CommissionRun.objects.select_related('plan')[:30], 'can_add': request.user.has_perm('erp.add_commissionplan')})


@login_required
def plan_form(request, pk=None):
    plan = get_object_or_404(CommissionPlan, pk=pk) if pk else None
    permitted(request.user, 'change_commissionplan' if plan else 'add_commissionplan')
    P, errors = request.POST, []
    if request.method == 'POST':
        try:
            data = {'name': P.get('name', ''), 'basis': P.get('basis'), 'rate': Decimal(P.get('rate') or 0), 'tds_rate': Decimal(P.get('tds_rate') or 0),
                    'site': Site.objects.filter(pk=P.get('site') or 0).first(), 'valid_from': date.fromisoformat(P['valid_from']) if P.get('valid_from') else None,
                    'valid_to': date.fromisoformat(P['valid_to']) if P.get('valid_to') else None}
            plan = C.save_plan(request.user, data, list(_people().filter(pk__in=P.getlist('people'))), pk)
        except (ValueError, InvalidOperation):
            errors.append('Enter the rate and dates correctly.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'{plan} saved. Submit it for approval.')
            return redirect('commission_plan', pk=plan.pk)
    v = {k: P.get(k, '') for k in ('name', 'basis', 'rate', 'tds_rate', 'site', 'valid_from', 'valid_to')} if request.method == 'POST' else \
        {'name': plan.name, 'basis': plan.basis, 'rate': plan.rate, 'tds_rate': plan.tds_rate, 'site': str(plan.site_id or ''), 'valid_from': plan.valid_from.isoformat(),
         'valid_to': plan.valid_to.isoformat() if plan.valid_to else ''} if plan else {'basis': 'sales', 'tds_rate': '0', 'valid_from': timezone.localdate().isoformat()}
    chosen = set(P.getlist('people')) if request.method == 'POST' else {str(u.pk) for u in plan.beneficiaries.all()} if plan else set()
    return render(request, 'erp/commission_plan_form.html', {'title': f'Edit {plan}' if plan else 'New commission plan', 'plan': plan, 'errors': errors, 'v': v,
                                                             'people': _people(), 'chosen': chosen, 'bases': COMMISSION_BASES, 'sites': Site.objects.filter(active=True).order_by('code')})


@login_required
def plan_detail(request, pk):
    permitted(request.user, 'view_commissionplan')
    plan = get_object_or_404(CommissionPlan.objects.select_related('site', 'created_by', 'decided_by'), pk=pk)
    u = request.user
    return render(request, 'erp/commission_plan.html', {
        'title': str(plan), 'plan': plan, 'runs': plan.runs.select_related('created_by', 'decided_by'), 'today': timezone.localdate().isoformat(),
        'history': AuditEvent.objects.filter(reference=f'commission-plan:{plan.pk}').select_related('actor').order_by('-created_at')[:10],
        'can': {'edit': plan.status in ('draft', 'rejected') and u.has_perm('erp.change_commissionplan'),
                'decide': plan.status == 'submitted' and u.has_perm('erp.approve_commissionplan') and plan.created_by_id != u.pk,
                'run': plan.status in ('approved', 'ended') and u.has_perm('erp.add_commissionrun'),
                'end': plan.status == 'approved' and u.has_perm('erp.approve_commissionplan')}})


@login_required
@require_POST
def plan_action(request, pk, action):
    get_object_or_404(CommissionPlan, pk=pk)
    P, u = request.POST, request.user
    try:
        if action == 'submit':
            C.submit_plan(u, pk); msg = 'Sent for approval.'
        elif action in ('approve', 'reject'):
            C.decide_plan(u, pk, action == 'approve', P.get('note', '')); msg = 'Decision recorded.'
        elif action == 'end':
            C.end_plan(u, pk, date.fromisoformat(P['on'])); msg = 'Plan ended.'
        elif action == 'calculate':
            run = C.calculate(u, pk, date.fromisoformat(P['end']))
            messages.success(request, f'{run.number} calculated. A different user approves and posts it.')
            return redirect('commission_run', pk=run.pk)
        else:
            raise Http404
        messages.success(request, msg)
    except (ValueError, KeyError):
        messages.error(request, 'Enter the date correctly.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('commission_plan', pk=pk)


@login_required
def run_detail(request, pk):
    finance_permitted(request.user, 'view_commissionrun')
    run = get_object_or_404(CommissionRun.objects.select_related('plan', 'created_by', 'decided_by'), pk=pk)
    u = request.user
    from .models import JournalEntry
    return render(request, 'erp/commission_run.html', {'title': run.number, 'run': run, 'lines': run.lines.select_related('person'),
                                                       'journal': JournalEntry.objects.filter(source=f'com:{run.pk}').first(),
                                                       'can_decide': run.status == 'draft' and u.has_perm('erp.approve_commissionrun') and run.created_by_id != u.pk
                                                       and not run.lines.filter(person=u).exists()})


@login_required
@require_POST
def run_action(request, pk, action):
    get_object_or_404(CommissionRun, pk=pk)
    try:
        if action not in ('approve', 'reject'):
            raise Http404
        C.decide_run(request.user, pk, action == 'approve', request.POST.get('note', ''))
        messages.success(request, 'Commission approved and posted to the ledger.' if action == 'approve' else 'Run rejected.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('commission_run', pk=pk)


@login_required
def my_commission(request):
    """Every user sees only their own commission lines (no finance access needed)."""
    rows = CommissionLine.objects.filter(person=request.user).select_related('run__plan').order_by('-run__end')
    approved = [r for r in rows if r.run.status == 'approved']
    return render(request, 'erp/my_commission.html', {'title': 'My commission', 'rows': rows,
                                                      'earned': sum((r.amount for r in approved), Decimal('0')), 'tds': sum((r.tds for r in approved), Decimal('0'))})
