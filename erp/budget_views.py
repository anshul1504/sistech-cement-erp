"""Cost centres, budgets (versioned grid with CSV import/export) and budget vs actual."""
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import budgets as B
from .finance_access import permitted
from .models import Budget, CostCentre, Site, AuditEvent


def _fy_choices():
    this = B.fy_of(timezone.localdate())
    return [date(this.year + d, 4, 1) for d in (-1, 0, 1)]


@login_required
def cost_centres(request):
    permitted(request.user, 'view_costcentre')
    P, errors = request.POST, []
    edit = CostCentre.objects.filter(pk=request.GET.get('edit') or 0).first()
    if request.method == 'POST':
        try:
            cc = B.save_cost_centre(request.user, P.get('code'), P.get('name'), P.get('kind', 'department'), Site.objects.filter(pk=P.get('site') or 0).first(),
                                    P.get('active') == '1', int(P['pk']) if P.get('pk', '').isdigit() else None)
        except ValidationError as e:
            errors = e.messages
        else:
            messages.success(request, f'{cc} saved.')
            return redirect('cost_centres')
    form = {'code': P.get('code', edit.code if edit else ''), 'name': P.get('name', edit.name if edit else '')}
    return render(request, 'erp/cost_centres.html', {'title': 'Cost centres', 'tab': 'cc', 'rows': CostCentre.objects.select_related('site'), 'edit': edit, 'errors': errors,
                                                     'form': form, 'sites': Site.objects.filter(active=True).order_by('code'), 'kinds': CostCentre._meta.get_field('kind').choices})


@login_required
def budgets(request):
    permitted(request.user, 'view_budget')
    if request.method == 'POST':
        try:
            fy = date.fromisoformat(request.POST.get('fy_start', ''))
            src = Budget.objects.filter(pk=request.POST.get('copy_from') or 0).first()
            b = B.create_budget(request.user, fy, request.POST.get('name'), src)
        except ValueError:
            messages.error(request, 'Choose the financial year.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        else:
            messages.success(request, f'{b} created as draft.')
            return redirect('budget_detail', pk=b.pk)
        return redirect('budgets')
    return render(request, 'erp/budgets.html', {'title': 'Budgets', 'tab': 'budget', 'rows': Budget.objects.select_related('created_by', 'decided_by'),
                                                'years': _fy_choices(), 'can_add': request.user.has_perm('erp.add_budget')})


@login_required
def budget_detail(request, pk):
    permitted(request.user, 'view_budget')
    b = get_object_or_404(Budget, pk=pk)
    cc = CostCentre.objects.filter(pk=request.GET.get('cc') or request.POST.get('cc') or 0).first()
    months = B.fy_months(b.fy_start)
    accounts = list(B.budget_accounts())
    editable = b.status in ('draft', 'rejected') and request.user.has_perm('erp.change_budget')
    if request.method == 'POST' and editable:
        try:
            if request.FILES.get('file'):
                n = B.import_csv(request.user, b.pk, request.FILES['file'].read().decode('utf-8-sig', errors='replace'))
                messages.success(request, f'{n} budget rows imported.')
            else:
                cells = {}
                for a in accounts:
                    for i, m in enumerate(months):
                        v = request.POST.get(f'c_{a.pk}_{i}', '').replace(',', '').strip()
                        cells[(a.pk, m)] = Decimal(v) if v else None
                B.set_cells(request.user, b.pk, cc, cells)
                messages.success(request, 'Budget saved.')
        except InvalidOperation:
            messages.error(request, 'Enter amounts as numbers.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        return redirect(f"{request.path}{'?cc=' + str(cc.pk) if cc else ''}")
    cells = {(l.account_id, l.month): l.amount for l in b.lines.filter(cost_centre=cc)}
    rows = []
    for a in accounts:
        vals = [cells.get((a.pk, m)) for m in months]
        rows.append({'account': a, 'cells': list(enumerate(vals)), 'total': sum((v for v in vals if v), Decimal('0'))})
    if request.GET.get('export') == 'csv':
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="budget-{b.fy_start.year}-v{b.version}.csv"'
        resp.write('﻿')
        w = csv.writer(resp)
        w.writerow(['Account code', 'Cost centre'] + B.MONTHS + ['Account name'])
        for l in sorted({(x.account, x.cost_centre) for x in b.lines.select_related('account', 'cost_centre')}, key=lambda t: (t[0].code, t[1].code if t[1] else '')):
            vals = {x.month: x.amount for x in b.lines.filter(account=l[0], cost_centre=l[1])}
            w.writerow([l[0].code, l[1].code if l[1] else ''] + [vals.get(m, '') for m in months] + [l[0].name])
        return resp
    totals = [sum((r['cells'][i][1] or 0 for r in rows), Decimal('0')) for i in range(12)]
    return render(request, 'erp/budget_detail.html', {'title': str(b), 'tab': 'budget', 'b': b, 'cc': cc, 'centres': CostCentre.objects.filter(active=True), 'months': B.MONTHS,
                                                      'rows': rows, 'totals': totals, 'editable': editable,
                                                      'can_submit': b.status in ('draft', 'rejected') and request.user.has_perm('erp.change_budget'),
                                                      'can_decide': b.status == 'submitted' and request.user.has_perm('erp.approve_budget') and b.created_by_id != request.user.pk,
                                                      'others': Budget.objects.filter(fy_start=b.fy_start).exclude(pk=b.pk),
                                                      'history': AuditEvent.objects.filter(reference=str(b)).select_related('actor').order_by('-created_at')[:10]})


@login_required
@require_POST
def budget_action(request, pk, action):
    get_object_or_404(Budget, pk=pk)
    try:
        if action == 'submit':
            B.submit(request.user, pk); msg = 'Sent for approval.'
        elif action in ('approve', 'reject'):
            B.decide(request.user, pk, action == 'approve', request.POST.get('note', '')); msg = 'Decision recorded.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('budget_detail', pk=pk)


@login_required
def budget_vs_actual(request):
    permitted(request.user, 'view_budget')
    G = request.GET
    today = timezone.localdate()
    budget = Budget.objects.filter(pk=G.get('budget') or 0).first() or Budget.objects.filter(status='approved', fy_start=B.fy_of(today)).first() \
        or Budget.objects.filter(status='approved').first()
    cc = CostCentre.objects.filter(pk=G.get('cc') or 0).first()
    site = Site.objects.filter(pk=G.get('site') or 0).first()
    data, upto = None, None
    if budget:
        fy_end = date(budget.fy_start.year + 1, 3, 31)
        try:
            upto = date.fromisoformat(G['upto']) if G.get('upto') else min(today, fy_end)
        except ValueError:
            upto = min(today, fy_end)
        upto = max(min(upto, fy_end), budget.fy_start)
        data = B.comparison(budget, upto, cc, site)
    return render(request, 'erp/budget_vs_actual.html', {'title': 'Budget vs actual', 'tab': 'bva', 'budget': budget, 'budgets': Budget.objects.exclude(status='rejected'),
                                                         'data': data, 'upto': upto, 'cc': cc, 'site': site, 'centres': CostCentre.objects.all(),
                                                         'sites': Site.objects.filter(active=True).order_by('code')})
