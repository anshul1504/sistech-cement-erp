"""Dealer security deposit ledger screens."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Sum, Q
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import deposits as DP
from .finance_access import permitted
from .models import DepositEntry, Partner, Account, CUSTOMER_KINDS


@login_required
def deposits(request):
    permitted(request.user, 'view_depositentry')
    P, G = request.POST, request.GET
    customers = Partner.objects.filter(kind__in=CUSTOMER_KINDS).order_by('name')
    partner = customers.filter(pk=P.get('partner') or G.get('partner') or 0).first()
    banks = Account.objects.filter(is_bank=True, active=True).order_by('code')
    errors = []
    if request.method == 'POST':
        try:
            if not partner:
                raise ValidationError('Choose the dealer.')
            args = (request.user, partner, Decimal(P.get('amount') or 0), banks.filter(pk=P.get('bank') or 0).first(), P.get('reference', ''),
                    date.fromisoformat(P.get('date') or timezone.localdate().isoformat()))
            e = DP.receive(*args, P.get('note', '')) if P.get('kind') == 'receipt' else DP.propose_refund(*args, P.get('note', ''))
        except (ValueError, InvalidOperation):
            errors.append('Enter the amount and date correctly.')
        except ValidationError as ex:
            errors += ex.messages
        else:
            messages.success(request, f'{e.number} {"posted" if e.status == "posted" else "sent for approval"}.')
            return redirect(f'{request.path}?partner={partner.pk}')
    rows = DepositEntry.objects.select_related('partner', 'bank', 'created_by', 'approved_by')
    if partner:
        rows = rows.filter(partner=partner)
    held = {r['partner']: r for r in DepositEntry.objects.filter(status='posted').values('partner').annotate(
        rec=Sum('amount', filter=Q(kind='receipt')), ref=Sum('amount', filter=Q(kind='refund')))}
    holders = [{'partner': p, 'held': (held[p.pk]['rec'] or 0) - (held[p.pk]['ref'] or 0)} for p in customers.filter(pk__in=held)]
    holders.sort(key=lambda r: -r['held'])
    return render(request, 'erp/deposits.html', {'title': 'Security deposits', 'customers': customers, 'partner': partner, 'banks': banks, 'rows': rows[:100],
                                                 'holders': holders, 'total': sum((h['held'] for h in holders), Decimal('0')), 'errors': errors, 'post': P,
                                                 'partner_held': DP.held(partner) if partner else None, 'today': timezone.localdate().isoformat(),
                                                 'can_add': request.user.has_perm('erp.add_depositentry'), 'can_approve': request.user.has_perm('erp.approve_depositentry')})


@login_required
@require_POST
def deposit_decide(request, pk, action):
    try:
        e = DP.decide_refund(request.user, pk, action == 'approve')
        messages.success(request, f'{e.number} {"refund posted" if e.status == "posted" else "rejected"}.')
    except ValidationError as ex:
        messages.error(request, ' '.join(ex.messages))
    return redirect('deposits')
