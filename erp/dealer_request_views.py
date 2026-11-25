"""Sales team side of dealer order requests: review, turn into a draft sales order, or decline with a reason."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from accounts.roles import order_permission
from . import dealer_orders as DO


def _allowed(user):
    return order_permission(user, 'Sale', 'create')


@login_required
def dealer_requests(request):
    if not _allowed(request.user):
        raise PermissionDenied
    status = request.GET.get('status', 'submitted')
    qs = DO.queue()
    if status in ('submitted', 'accepted', 'rejected', 'cancelled'):
        qs = qs.filter(status=status)
    counts = {s: DO.queue().filter(status=s).count() for s in ('submitted', 'accepted', 'rejected', 'cancelled')}
    return render(request, 'erp/dealer_requests.html', {'title': 'Dealer order requests', 'requests': qs.prefetch_related('lines__product')[:100],
                                                         'status': status, 'counts': counts})


@login_required
def dealer_request(request, pk):
    if not _allowed(request.user):
        raise PermissionDenied
    req = get_object_or_404(DO.queue(), pk=pk)
    sites = DO.my_sites(request.user).order_by('code')
    if request.method == 'POST':
        try:
            if request.POST.get('decision') == 'accept':
                order = DO.accept(request.user, pk, sites.filter(pk=request.POST.get('site') or 0).first())
                messages.success(request, f'Draft {order.number} created from {req.number}. Check it and submit for approval.')
                return redirect('order_detail', pk=order.pk)
            DO.reject(request.user, pk, request.POST.get('reason', ''))
            messages.success(request, f'{req.number} declined — the dealer sees your reason.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        return redirect('dealer_request', pk=pk)
    lines = []
    for l in req.lines.select_related('product'):
        lines.append({'line': l, 'value': (l.indicative_rate * l.quantity) if l.indicative_rate else None})
    return render(request, 'erp/dealer_request.html', {'title': req.number, 'req': req, 'lines': lines, 'sites': sites, 'credit': DO.credit_room(req.partner)})
