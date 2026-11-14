"""Manufacturing partners: plants, supply contracts (maker-checker), scorecard and the sourcing advisor."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import sourcing as SC
from .models import Partner, Site, Product, SupplyContract, CUSTOMER_KINDS
from .services import permitted


@login_required
def partners(request):
    permitted(request.user, 'view_supplycontract')
    rows = []
    for p in Partner.objects.filter(manufacturing_partner=True).order_by('name'):
        sc = SC.scorecard(p)
        rows.append({'partner': p, 'sites': sc['rows'], 'since': sc['since'],
                     'capacity': sum((r['site'].capacity_tpa or 0 for r in sc['rows']), Decimal('0'))})
    pending = SupplyContract.objects.filter(status='draft').select_related('partner', 'site', 'product', 'created_by')
    return render(request, 'erp/mfg_partners.html', {'title': 'Manufacturing partners', 'rows': rows, 'pending': pending,
                                                     'total_capacity': sum((r['capacity'] for r in rows), Decimal('0')),
                                                     'can_add': request.user.has_perm('erp.add_supplycontract'),
                                                     'can_decide': request.user.has_perm('erp.approve_supplycontract')})


@login_required
def contract_new(request):
    permitted(request.user, 'add_supplycontract')
    partners = list(Partner.objects.filter(manufacturing_partner=True, active=True).order_by('name'))
    sites = list(Site.objects.filter(active=True, operated_by__isnull=False).select_related('operated_by').order_by('code'))
    products = list(Product.objects.filter(active=True, category__in=['Cement', 'TMT']).order_by('category', 'name'))
    P, errors = request.POST, []
    if request.method == 'POST':
        site = next((s for s in sites if str(s.pk) == P.get('site')), None)
        product = next((p for p in products if str(p.pk) == P.get('product')), None)
        try:
            if not site or not product:
                raise ValidationError('Choose the partner plant and the product.')
            c = SC.save_contract(request.user, site.operated_by, site, product, Decimal(P.get('rate') or 0), date.fromisoformat(P.get('valid_from')),
                                 date.fromisoformat(P['valid_to']) if P.get('valid_to') else None, Decimal(P.get('monthly_quantity') or 0), P.get('reference', ''), P.get('notes', ''))
        except (ValueError, InvalidOperation):
            errors.append('Enter the rate, quantity and dates correctly.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'Rate for {c.product.name} at {c.site.code} sent for approval.')
            return redirect('mfg_partners')
    return render(request, 'erp/contract_new.html', {'title': 'New supply rate', 'sites': sites, 'products': products, 'errors': errors, 'post': P,
                                                     'today': timezone.localdate().isoformat(), 'chosen_site': P.get('site') or request.GET.get('site', '')})


@login_required
@require_POST
def contract_decide(request, pk, decision):
    try:
        c = SC.decide_contract(request.user, pk, decision == 'approve', request.POST.get('note', ''))
        messages.success(request, f'{c.product.code} at {c.site.code}: {c.get_status_display().lower()}.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('mfg_partners')


@login_required
def advisor(request):
    permitted(request.user, 'view_supplycontract')
    products = Product.objects.filter(active=True, category__in=['Cement', 'TMT']).order_by('category', 'name')
    dealers = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).exclude(district='').order_by('name')
    G = request.GET
    product = products.filter(pk=G.get('product') or 0).first()
    dealer = dealers.filter(pk=G.get('dealer') or 0).first()
    district = (dealer.district if dealer else G.get('district', '')).strip()
    try:
        qty = Decimal(G.get('qty') or 0)
    except InvalidOperation:
        qty = Decimal('0')
    opts = SC.options(product, qty or 1, district) if product and district else None
    return render(request, 'erp/sourcing_advisor.html', {'title': 'Sourcing advisor', 'products': products, 'dealers': dealers, 'product': product, 'dealer': dealer,
                                                         'district': district, 'qty': qty, 'opts': opts})
