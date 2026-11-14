"""Collections desk: today's chase list, follow-up / promise / dispute log per customer."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from . import collect as C
from .finance_access import permitted
from .models import Partner, CollectionFollowUp, CUSTOMER_KINDS, FOLLOWUP_KINDS, DISPUTE_REASONS


@login_required
def collections(request):
    permitted(request.user, 'view_receipt')
    P, G = request.POST, request.GET
    customers = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).order_by('name')
    partner = customers.filter(pk=P.get('partner') or G.get('partner') or 0).first()
    errors = []
    if request.method == 'POST':
        try:
            if not partner:
                raise ValidationError('Choose the customer.')
            amt = Decimal(P['promised_amount']) if P.get('promised_amount') else None
            f = C.record(request.user, partner, P.get('kind', ''), P.get('note', ''), amt,
                         date.fromisoformat(P['promised_date']) if P.get('promised_date') else None, P.get('dispute_reason', ''),
                         date.fromisoformat(P['next_action_on']) if P.get('next_action_on') else None)
        except (ValueError, InvalidOperation):
            errors.append('Enter the amount and dates correctly.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'{f.get_kind_display()} recorded for {partner.name}.')
            return redirect(f"{request.path}?partner={partner.pk}")
    log = CollectionFollowUp.objects.select_related('partner', 'owner')
    if partner:
        log = log.filter(partner=partner)
    rows = [dict(f=f, state=C.promise_status(f)) for f in log[:60]]
    mine = G.get('mine') == '1'
    return render(request, 'erp/collections.html', {'title': 'Collections desk', 'work': C.worklist(request.user if mine else None), 'mine': mine,
                                                    'customers': customers, 'partner': partner, 'rows': rows, 'kinds': FOLLOWUP_KINDS, 'reasons': DISPUTE_REASONS[1:],
                                                    'errors': errors, 'post': P, 'can_add': request.user.has_perm('erp.add_receipt')})


@login_required
@require_POST
def dispute_resolve(request, pk):
    try:
        f = C.resolve_dispute(request.user, pk, request.POST.get('resolution', ''))
        messages.success(request, 'Dispute marked resolved.')
        return redirect(f"/collections/?partner={f.partner_id}")
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('collections')
