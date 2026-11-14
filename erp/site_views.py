"""Plants & depots: list, detail, create/edit, activate, delete-if-unused."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from .forms import SiteForm
from .models import (Site, SiteAccess, Order, StockBalance, StockMovement, Production, AuditEvent, OrganizationDocument)
from .services import audit, permitted

KIND_ICONS = {'Plant': 'factory', 'Depot': 'truck', 'Warehouse': 'boxes'}


def visible_sites(user):
    """Super administrators see every location; everyone else only their assigned ones."""
    qs = Site.objects.all()
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


def site_usage(site):
    return {'orders': Order.objects.filter(site=site).count(), 'stock_rows': StockBalance.objects.filter(site=site).count(),
            'movements': StockMovement.objects.filter(site=site).count(), 'production': Production.objects.filter(site=site).count(),
            'documents': OrganizationDocument.objects.filter(site=site).count(), 'audit': AuditEvent.objects.filter(site=site).count()}


@login_required
def sites(request):
    permitted(request.user, 'view_site')
    kind, status, q = request.GET.get('kind', ''), request.GET.get('status', 'active'), request.GET.get('q', '').strip()
    base = visible_sites(request.user)
    qs = base.annotate(people=Count('siteaccess', distinct=True), stock_lines=Count('stockbalance', distinct=True)).select_related('manager')
    if kind: qs = qs.filter(kind=kind)
    if status == 'active': qs = qs.filter(active=True)
    elif status == 'inactive': qs = qs.filter(active=False)
    if q: qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(city__icontains=q) | Q(district__icontains=q) | Q(gstin__icontains=q))
    counts = dict(base.filter(active=True).values_list('kind').annotate(n=Count('id')))
    tabs = [('', 'All', sum(counts.values())), ('Plant', 'Plants', counts.get('Plant', 0)), ('Depot', 'Depots', counts.get('Depot', 0)),
            ('Warehouse', 'Warehouses', counts.get('Warehouse', 0))]
    docs = {}
    for d in OrganizationDocument.objects.filter(site__in=base).only('site_id', 'valid_until'):
        state = d.status[0]
        if state in ('expired', 'expiring'):
            docs.setdefault(d.site_id, {'expired': 0, 'expiring': 0})[state] += 1
    rows = [{'site': s, 'docs': docs.get(s.pk), 'icon': KIND_ICONS.get(s.kind, 'building')} for s in qs.order_by('kind', 'code')]
    return render(request, 'erp/sites.html', {'title': 'Plants & depots', 'rows': rows, 'tabs': tabs,
                                              'filters': {'kind': kind, 'status': status, 'q': q}, 'can_add': request.user.has_perm('erp.add_site')})


@login_required
def site_detail(request, pk):
    permitted(request.user, 'view_site')
    site = get_object_or_404(visible_sites(request.user).select_related('manager'), pk=pk)
    balances = StockBalance.objects.filter(site=site, quantity__gt=0).select_related('product').order_by('product__category', 'product__name')
    can_docs = request.user.has_perm('erp.view_organization') or request.user.has_perm('erp.change_organization')
    documents = OrganizationDocument.objects.filter(site=site).defer('file').order_by('valid_until') if can_docs else []
    usage = site_usage(site)
    return render(request, 'erp/site_detail.html', {
        'title': site.name, 'site': site, 'icon': KIND_ICONS.get(site.kind, 'building'),
        'people': User.objects.filter(siteaccess__site=site).select_related('profile').order_by('first_name', 'username'),
        'balances': balances[:12], 'balance_count': balances.count(), 'documents': documents, 'can_docs': can_docs,
        'can_upload': request.user.has_perm('erp.change_organization'),
        'activity': AuditEvent.objects.filter(site=site).select_related('actor').order_by('-created_at')[:8],
        'open_orders': Order.objects.filter(site=site, status__in=['Draft', 'Submitted', 'Approved']).count(),
        'usage': usage, 'used': any(usage.values()), 'can_change': request.user.has_perm('erp.change_site')})


@login_required
def site_form(request, pk=None):
    permitted(request.user, 'change_site' if pk else 'add_site')
    site = get_object_or_404(visible_sites(request.user), pk=pk) if pk else None
    form = SiteForm(request.POST or None, instance=site)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            if not pk and not request.user.is_superuser:
                SiteAccess.objects.get_or_create(user=request.user, site=saved)  # creators keep access to what they add
            audit(request.user, 'Master updated' if pk else 'Master created', saved, f'Site {saved.code}')
        messages.success(request, f'{saved.name} {"updated" if pk else "added"}.')
        return redirect('site_detail', pk=saved.pk)
    return render(request, 'erp/site_form.html', {'title': 'Edit location' if pk else 'Add plant or depot', 'form': form, 'site': site})


@login_required
@require_POST
def site_status(request, pk):
    permitted(request.user, 'change_site')
    site = get_object_or_404(visible_sites(request.user), pk=pk)
    site.active = not site.active
    site.save(update_fields=['active', 'updated_at'])
    audit(request.user, 'Location activated' if site.active else 'Location deactivated', site, f'Site {site.code}')
    messages.success(request, f'{site.name} is {"active again" if site.active else "now inactive — it no longer appears on new orders, production or stock entries"}.')
    return redirect('site_detail', pk=site.pk)


@login_required
@require_POST
def site_delete(request, pk):
    permitted(request.user, 'change_site')
    if not request.user.is_superuser:
        raise PermissionDenied
    site = get_object_or_404(Site, pk=pk)
    if any(site_usage(site).values()):
        messages.error(request, f'{site.name} has orders, stock, documents or history, so it cannot be deleted. Deactivate it instead.')
        return redirect('site_detail', pk=site.pk)
    name = site.name
    audit(request.user, 'Location deleted', site, f'Site {site.code}')
    site.delete()
    messages.success(request, f'{name} deleted.')
    return redirect('sites')
