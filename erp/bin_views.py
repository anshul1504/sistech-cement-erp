"""Bins & locations on one page: bins with contents, stock not yet put away, put-away / move forms and recent moves."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST

from . import bins as B
from .models import StorageBin, BinStock, BinMove, StockBalance, Product, Site
from .services import permitted, require_site


def _sites(user):
    qs = Site.objects.filter(active=True).order_by('code')
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


@login_required
def bins(request):
    permitted(request.user, 'view_storagebin')
    sites = list(_sites(request.user))
    pick = request.GET.get('site', '')
    site = next((s for s in sites if str(s.pk) == pick), sites[0] if sites else None)
    if not site:
        raise Http404
    rows = list(StorageBin.objects.filter(site=site).prefetch_related('stock__product'))
    stock = StockBalance.objects.filter(site=site, status='available', quantity__gt=0).select_related('product')
    binned = defaultdict(Decimal)
    for r in BinStock.objects.filter(bin__site=site):
        binned[r.product_id] += r.quantity
    loose = [{'product': b.product, 'available': b.quantity, 'binned': binned[b.product_id], 'loose': b.quantity - binned[b.product_id]} for b in stock]
    return render(request, 'erp/bins.html', {'title': 'Bins & locations', 'sites': sites, 'site': site, 'rows': rows, 'loose': loose,
                                             'unbinned_total': sum((x['loose'] for x in loose), Decimal('0')), 'products': [x['product'] for x in loose],
                                             'moves': BinMove.objects.filter(site=site).select_related('product', 'from_bin', 'to_bin', 'actor')[:25],
                                             'can_move': request.user.has_perm('erp.move_binstock'), 'can_add': request.user.has_perm('erp.add_storagebin')})


@login_required
@require_POST
def bin_action(request, action):
    P, u = request.POST, request.user
    site_pk = P.get('site', '')
    try:
        if action == 'add':
            permitted(u, 'add_storagebin')
            site = Site.objects.get(pk=site_pk)
            require_site(u, site)
            code = (P.get('code') or '').strip().upper()
            if not code:
                raise ValidationError('Enter the bin code.')
            if StorageBin.objects.filter(site=site, code=code).exists():
                raise ValidationError(f'Bin {code} already exists.')
            StorageBin.objects.create(site=site, code=code[:20], zone=(P.get('zone') or '')[:60])
            msg = f'Bin {code} added.'
        elif action == 'put':
            b = B.put_away(u, Product.objects.get(pk=P.get('product')), StorageBin.objects.get(pk=P.get('to_bin')), P.get('quantity')); msg = 'Put away.'
        elif action == 'move':
            B.move(u, Product.objects.get(pk=P.get('product')), StorageBin.objects.get(pk=P.get('from_bin')), StorageBin.objects.get(pk=P.get('to_bin')), P.get('quantity')); msg = 'Moved.'
        else:
            raise Http404
        messages.success(request, msg)
    except (Site.DoesNotExist, Product.DoesNotExist, StorageBin.DoesNotExist, ValueError, InvalidOperation):
        messages.error(request, 'Choose the bin, product and quantity.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect(f'/inventory/bins/?site={site_pk}')
