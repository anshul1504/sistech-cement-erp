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


# ---------- Order requests (dealer side) ----------

@login_required
@never_cache
def order_requests(request):
    from datetime import date
    from decimal import Decimal, InvalidOperation
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect
    from django.utils import timezone
    from . import dealer_orders as DO
    from .models import DealerOrderRequest
    partner = dealer_for(request.user)
    products = list(DO.products())
    if request.method == 'POST':
        lines = []
        by_pk = {p.pk: p for p in products}
        for i in range(DO.MAX_LINES):
            pid, qty = request.POST.get(f'product_{i}', ''), request.POST.get(f'qty_{i}', '').replace(',', '').strip()
            if not pid and not qty:
                continue
            try:
                q = Decimal(qty)
            except (InvalidOperation, ValueError):
                q = None
            lines.append((by_pk.get(int(pid)) if pid.isdigit() else None, q))
        try:
            needed = date.fromisoformat(request.POST.get('required_by', ''))
        except ValueError:
            needed = None
        try:
            if any(p is None or q is None for p, q in lines):
                raise ValidationError('Choose a product and a valid quantity on every filled row.')
            req = DO.submit(request.user, partner, needed, lines, request.POST.get('ship_to', ''), request.POST.get('note', ''))
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        else:
            messages.success(request, f'Request {req.number} sent. Sistech will confirm it as a sales order.')
            return redirect('dealer_orders')
    rows = [{'product': p, 'rate': DO.indicative(partner, p)} for p in products]
    return render(request, 'erp/dealer_orders.html', {
        'title': 'My order requests', 'partner': partner, 'rows': rows, 'slots': range(4), 'credit': DO.credit_room(partner),
        'requests': DealerOrderRequest.objects.filter(partner=partner).select_related('order').prefetch_related('lines__product')[:30],
        'min_date': timezone.localdate(), 'default_ship': partner.shipping_address or partner.full_address})


@login_required
def order_request_cancel(request, pk):
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect
    from . import dealer_orders as DO
    if request.method != 'POST':
        return redirect('dealer_orders')
    try:
        DO.cancel(request.user, dealer_for(request.user), pk)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, 'Request cancelled.')
    return redirect('dealer_orders')
