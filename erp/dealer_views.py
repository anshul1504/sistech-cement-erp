"""Dealer self-service reads only the administrator-linked account, never a submitted partner ID."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache

from .ledger import ageing, balance, open_invoices
from .models import DealerLedgerAccess, Order, Invoice, Receipt


def dealer_for(user):
    return get_object_or_404(
        DealerLedgerAccess.objects.select_related('partner'), user=user, active=True,
        partner__active=True, partner__kind='Dealer',
    ).partner


@login_required
@never_cache
def dashboard(request):
    partner = dealer_for(request.user)
    invoices = open_invoices(partner)
    return render(request, 'erp/dealer_dashboard.html', {
        'title': 'My dealer account', 'partner': partner, 'balance': balance(partner),
        'age': ageing(invoices), 'open_invoices': invoices[:10],
        'orders': Order.objects.filter(partner=partner, kind='Sale').exclude(
            status__in=['Draft', 'Submitted', 'Revised']).select_related('site').order_by('-created_at')[:10],
        'receipts': Receipt.objects.filter(partner=partner).order_by('-date', '-pk')[:10],
    })


@login_required
@never_cache
def invoices(request):
    partner = dealer_for(request.user)
    # No internal document links, accounting notes or other parties are exposed here.
    page = Paginator(Invoice.objects.filter(partner=partner).prefetch_related('settlements').order_by('-date', '-pk'), 25).get_page(request.GET.get('page'))
    return render(request, 'erp/dealer_invoices.html', {'title': 'My invoices', 'partner': partner, 'page': page})
