"""TDS desk on one page: bills to deduct, register by month, reversal and challan deposit."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import tds as T
from .finance_access import permitted
from .models import TdsDeduction, TdsChallan, Account


@login_required
def tds(request):
    permitted(request.user, 'view_tdsdeduction')
    today = timezone.localdate()
    try:
        month = date.fromisoformat((request.GET.get('month') or today.strftime('%Y-%m')) + '-01')
    except ValueError:
        month = today.replace(day=1)
    rows = T.register(month)
    by_section = {r['section']: r for r in TdsDeduction.objects.filter(status='posted', challan__isnull=True).values('section').annotate(t=Sum('amount'))}
    return render(request, 'erp/tds.html', {'title': 'TDS', 'month': month, 'rows': rows, 'suggestions': T.suggestions(),
                                            'pending': sorted(by_section.values(), key=lambda r: r['section']), 'challans': TdsChallan.objects.select_related('bank')[:24],
                                            'banks': Account.objects.filter(is_bank=True, active=True).exclude(system_key='cash'),
                                            'month_total': rows.filter(status='posted').aggregate(t=Sum('amount'))['t'] or 0,
                                            'can_add': request.user.has_perm('erp.add_tdsdeduction'), 'can_deposit': request.user.has_perm('erp.add_tdschallan')})


@login_required
@require_POST
def tds_action(request, action):
    P, u = request.POST, request.user
    try:
        if action == 'deduct':
            d = T.deduct(u, P.get('kind'), int(P.get('bill') or 0), note=P.get('note', '')); msg = f'{d.number}: ₹{d.amount} deducted from {d.payee.name}.'
        elif action == 'reverse':
            T.reverse(u, int(P.get('deduction') or 0), P.get('reason', '')); msg = 'TDS reversed.'
        elif action == 'deposit':
            c = T.deposit(u, P.get('section', ''), date.fromisoformat(P['month'] + '-01'), Account.objects.filter(pk=P.get('bank') or 0).first(),
                          P.get('challan', ''), date.fromisoformat(P['paid_on']), P.get('bsr', ''))
            msg = f'Challan {c.challan_number} recorded for ₹{c.amount}.'
        else:
            raise Http404
        messages.success(request, msg)
    except (ValueError, KeyError):
        messages.error(request, 'Enter the month and dates correctly.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('tds')
