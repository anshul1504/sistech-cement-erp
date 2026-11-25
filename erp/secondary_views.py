"""Secondary sales screens: the sales team's sell-through view and entry, and the dealer's own entry page."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from . import insights as I
from . import secondary as S
from .models import SecondarySale, Partner


def _data(post, dealer):
    def dec(v):
        try:
            return Decimal(str(v).replace(',', '').strip()) if (v or '').strip() else None
        except InvalidOperation:
            return Decimal('-1')
    try:
        d = date.fromisoformat(post.get('date', ''))
    except ValueError:
        d = None
    rid = post.get('retailer', '')
    retailer = Partner.objects.filter(pk=rid, kind='Retailer').first() if rid.isdigit() else None
    return {'date': d, 'product': S.products().filter(pk=post.get('product') or 0).first(), 'quantity': dec(post.get('quantity')),
            'retailer': retailer, 'retailer_name': post.get('retailer_name', ''), 'rate': dec(post.get('rate')), 'note': post.get('note', '')}


@login_required
def secondary_sales(request):
    u = request.user
    if not u.has_perm('erp.view_secondarysale'):
        raise PermissionDenied
    can = u.has_perm('erp.manage_secondarysale')
    if request.method == 'POST':
        if not can:
            raise PermissionDenied
        try:
            if request.POST.get('cancel'):
                S.cancel(u, int(request.POST['cancel']), request.POST.get('reason', ''))
                messages.success(request, 'Entry cancelled.')
            elif request.FILES.get('file'):
                n = S.import_csv(u, request.FILES['file'].read().decode('utf-8', errors='replace'))
                messages.success(request, f'{n} rows imported.')
            else:
                dealer = Partner.objects.filter(pk=request.POST.get('dealer') or 0, kind__in=S.DEALER_KINDS).first()
                S.record(u, dealer, _data(request.POST, dealer), 'staff')
                messages.success(request, 'Secondary sale recorded.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        return redirect(request.get_full_path())
    period = I.parse_period(request.GET)
    dealer = Partner.objects.filter(pk=request.GET.get('dealer') or 0, kind__in=S.DEALER_KINDS).first()
    entries = SecondarySale.objects.filter(date__range=(period.start, period.end)).select_related('dealer', 'retailer', 'product', 'entered_by')
    if dealer:
        entries = entries.filter(dealer=dealer)
    return render(request, 'erp/secondary_sales.html', {
        'title': 'Secondary sales', 'period': period, 'periods': I.PERIODS, 'dealer': dealer, 'through': S.sell_through(period.start, period.end, dealer),
        'top': S.top_retailers(period.start, period.end, dealer), 'entries': entries[:50], 'can': can, 'today': timezone.localdate(),
        'dealers': Partner.objects.filter(kind__in=S.DEALER_KINDS, active=True).order_by('name'),
        'retailers': Partner.objects.filter(kind='Retailer', active=True, parent__isnull=False).select_related('parent').order_by('parent__name', 'name'),
        'products': S.products()})


@login_required
@never_cache
def dealer_secondary(request):
    from .dealer_views import dealer_for
    dealer = dealer_for(request.user)
    if request.method == 'POST':
        try:
            if request.POST.get('cancel'):
                S.cancel(request.user, int(request.POST['cancel']), request.POST.get('reason', '') or 'Entered by mistake', dealer=dealer)
                messages.success(request, 'Entry cancelled.')
            else:
                S.record(request.user, dealer, _data(request.POST, dealer), 'dealer')
                messages.success(request, 'Sale to retailer saved.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        return redirect('dealer_secondary')
    today = timezone.localdate()
    start = today.replace(day=1)
    return render(request, 'erp/dealer_secondary.html', {
        'title': 'Sales to retailers', 'partner': dealer, 'retailers': S.retailers_of(dealer), 'products': S.products(), 'today': today,
        'month': S.sell_through(start, today, dealer), 'top': S.top_retailers(start, today, dealer, 5),
        'entries': SecondarySale.objects.filter(dealer=dealer).select_related('retailer', 'product')[:40]})
