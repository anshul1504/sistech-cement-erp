"""Price list: versioned selling prices by product × plant × district × buyer type × dealer, with quantity slabs and a price checker."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Count
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import PriceRuleForm
from .gst import price_for
from .influencer_views import district_options
from .models import PriceRule, Product, Site, Partner, AuditEvent, CUSTOMER_KINDS
from .services import permitted


def log(user, action, rule, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=f'price:{rule.pk}', detail=detail or f'{rule.product.code} ₹{rule.rate} · {rule.scope_label}')


@login_required
def prices(request):
    permitted(request.user, 'view_pricerule')
    today = timezone.localdate()
    f = {k: request.GET.get(k, '').strip() for k in ['product', 'site', 'district', 'q']}
    f['state'] = request.GET.get('state', 'current')
    qs = PriceRule.objects.select_related('product', 'site', 'partner').annotate(uses=Count('order_lines'))
    current = Q(active=True, valid_from__lte=today) & (Q(valid_to__isnull=True) | Q(valid_to__gte=today))
    if f['state'] == 'current': qs = qs.filter(current)
    elif f['state'] == 'upcoming': qs = qs.filter(active=True, valid_from__gt=today)
    elif f['state'] == 'ended': qs = qs.filter(Q(active=False) | Q(valid_to__lt=today))
    if f['product'].isdigit(): qs = qs.filter(product_id=f['product'])
    if f['site'].isdigit(): qs = qs.filter(Q(site_id=f['site']) | Q(site__isnull=True))
    if f['district']: qs = qs.filter(Q(district__iexact=f['district']) | Q(district=''))
    if f['q']: qs = qs.filter(Q(product__name__icontains=f['q']) | Q(partner__name__icontains=f['q']) | Q(product__code__icontains=f['q']))
    all_rules = PriceRule.objects.all()
    counts = {'current': all_rules.filter(current).count(), 'upcoming': all_rules.filter(active=True, valid_from__gt=today).count(),
              'ended': all_rules.filter(Q(active=False) | Q(valid_to__lt=today)).count()}
    finished = Product.objects.filter(active=True, category__in=['Cement', 'TMT'])
    unpriced = finished.exclude(pk__in=all_rules.filter(current, site__isnull=True, district='', partner_kind='', partner__isnull=True).values('product'))
    # Price checker
    check = None
    if request.GET.get('check_product', '').isdigit():
        try:
            qty = Decimal(request.GET.get('check_qty') or '0')
        except InvalidOperation:
            qty = Decimal('0')
        product = Product.objects.filter(pk=request.GET['check_product']).first()
        site = Site.objects.filter(pk=request.GET.get('check_site') or 0).first()
        partner = Partner.objects.filter(pk=request.GET.get('check_partner') or 0).first()
        if product:
            check = {'product': product, 'site': site, 'partner': partner, 'qty': qty, 'rule': price_for(product, site, partner, qty)}
    return render(request, 'erp/prices.html', {
        'title': 'Price list', 'rules': qs.order_by('product__category', 'product__name', 'min_quantity', '-valid_from')[:400], 'filters': f, 'counts': counts,
        'products': finished.order_by('category', 'name'), 'sites': Site.objects.filter(active=True), 'districts': district_options(),
        'partners': Partner.objects.filter(active=True, kind__in=CUSTOMER_KINDS).order_by('name'), 'unpriced': unpriced, 'check': check, 'today': today,
        'can_add': request.user.has_perm('erp.add_pricerule'), 'can_change': request.user.has_perm('erp.change_pricerule')})


@login_required
def price_form(request, pk=None, revise=False):
    """New price, edit an unused price, or revise a used one (new version from a date; the old one ends the day before)."""
    permitted(request.user, 'change_pricerule' if pk else 'add_pricerule')
    rule = get_object_or_404(PriceRule.objects.select_related('product'), pk=pk) if pk else None
    used = bool(rule and rule.order_lines.exists())
    if rule and used and not revise:
        messages.info(request, 'This price is already on orders, so it is kept as history. Enter the new price and the date it starts.')
        return redirect('price_revise', pk=rule.pk)
    instance = None if revise or not rule else rule
    initial = {}
    if revise:
        initial = {f: getattr(rule, f) for f in ['product', 'min_quantity', 'site', 'district', 'partner_kind', 'partner', 'notes']}
        initial.update(valid_from=max(timezone.localdate(), rule.valid_from + timedelta(days=1)), rate=rule.rate, active=True)
    elif not rule and request.GET.get('product', '').isdigit():
        initial['product'] = request.GET['product']
    form = PriceRuleForm(request.POST or None, instance=instance, initial=initial)
    if revise:
        form.revising = rule
        for name in ['product', 'min_quantity', 'site', 'district', 'partner_kind', 'partner']:
            form.fields[name].disabled = True
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            if revise:
                new_from = form.cleaned_data['valid_from']
                if new_from <= rule.valid_from:
                    form.add_error('valid_from', f'The new price must start after {rule.valid_from:%d %b %Y}.')
                    return render(request, 'erp/price_form.html', {'title': 'Revise price', 'form': form, 'rule': rule, 'revise': True, 'districts': district_options()})
                rule.valid_to = new_from - timedelta(days=1)
                rule.save(update_fields=['valid_to'])
            saved = form.save(commit=False)
            if not instance:
                saved.created_by = request.user
            if revise:
                saved.replaces = rule
            saved.save()
            log(request.user, 'Price revised' if revise else 'Price updated' if instance else 'Price added', saved,
                f'{saved.product.code} ₹{rule.rate} → ₹{saved.rate} from {saved.valid_from:%d %b %Y}' if revise else '')
        messages.success(request, f'{saved.product.name}: ₹{saved.rate} {"from " + saved.valid_from.strftime("%d %b %Y") if revise else "saved"}.')
        return redirect(f"{redirect('prices').url}?product={saved.product_id}&state={'upcoming' if saved.valid_from > timezone.localdate() else 'current'}")
    return render(request, 'erp/price_form.html', {'title': 'Revise price' if revise else 'Edit price' if rule else 'Add price', 'form': form, 'rule': rule,
                                                   'revise': revise, 'used': used, 'districts': district_options()})


@login_required
def price_revise(request, pk):
    return price_form(request, pk, revise=True)


@login_required
@require_POST
def price_end(request, pk):
    """Stop a price from today (kept as history). Unused future prices are simply switched off."""
    permitted(request.user, 'change_pricerule')
    rule = get_object_or_404(PriceRule, pk=pk)
    today = timezone.localdate()
    if rule.valid_from > today:
        rule.active = False
        rule.save(update_fields=['active'])
    else:
        rule.valid_to = today
        rule.save(update_fields=['valid_to'])
    log(request.user, 'Price ended', rule)
    messages.success(request, f'{rule.product.name} price ₹{rule.rate} ({rule.scope_label}) ends {"now" if rule.active else "— switched off"}.')
    return redirect('prices')
