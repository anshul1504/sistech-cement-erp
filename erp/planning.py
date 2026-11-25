"""Supply & production plan (SRS FR-PRD-001, asset-light): what each plant/depot needs for the next N days and where it
should come from.

Per site × finished product:
  demand     = larger of (approved open-order balance) and (average daily invoiced quantity of the last 30 days × horizon)
  available  = available stock + transfers in transit to the site
  on order   = open purchase-order balance + purchase requests not yet ordered
  shortfall  = demand + reorder level (safety) − available − on order
Suggested source: the manufacturing partner with a current approved rate for that plant (and how much of its monthly
commitment is still open this month), own production when an approved recipe exists, otherwise purchase / transfer.
Planners raise draft purchase requests from the shortfalls; nothing is ordered automatically.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .models import Product, OrderLine, InvoiceLine, StockTransferLine, GRNLine, Recipe
from .procurement import stock_at, on_order
from .sourcing import current

ZERO = Decimal('0')
OPEN = ['Approved', 'Part dispatched', 'On hold']


def plan(sites, horizon=7, today=None):
    today = today or timezone.localdate()
    since = today - timedelta(days=30)
    products = list(Product.objects.filter(active=True, category__in=['Cement', 'TMT']).order_by('category', 'name'))
    rows = []
    for site in sites:
        sold = {r['product']: r['q'] for r in InvoiceLine.objects.filter(invoice__site=site, invoice__date__gt=since).values('product').annotate(q=Sum('quantity'))}
        for p in products:
            open_qty = sum((l.pending_quantity for l in OrderLine.objects.filter(order__kind='Sale', order__site=site, order__status__in=OPEN, product=p)), ZERO)
            daily = (sold.get(p.pk, ZERO) / 30).quantize(Decimal('0.001'))
            forecast = daily * horizon
            stock = stock_at(site, p)
            transit = StockTransferLine.objects.filter(transfer__status='in_transit', transfer__to_site=site, product=p).aggregate(q=Sum('dispatched'))['q'] or ZERO
            po, pr = on_order(site, p)
            contract = current(site, p)
            if not any([open_qty, daily, stock, transit, po, pr, contract]):
                continue
            demand = max(open_qty, forecast)
            available = stock + transit
            short = max(demand + (p.reorder_level or ZERO) - available - po - pr, ZERO).quantize(Decimal('0.001'))
            source, room = 'buy', None
            if contract:
                source = 'partner'
                if contract.monthly_quantity:
                    got = GRNLine.objects.filter(grn__status='posted', grn__site=site, grn__supplier=contract.partner, product=p,
                                                 grn__posted_at__date__gte=today.replace(day=1)).aggregate(a=Sum('accepted'), b=Sum('qc_accepted'))
                    room = max(contract.monthly_quantity - (got['a'] or ZERO) - (got['b'] or ZERO), ZERO)
            elif Recipe.objects.filter(product=p, status='approved').exists() and site.kind == 'Plant':
                source = 'make'
            rows.append({'site': site, 'product': p, 'open': open_qty, 'daily': daily, 'forecast': forecast.quantize(Decimal('0.001')), 'demand': demand,
                         'stock': stock, 'transit': transit, 'po': po, 'pr': pr, 'short': short, 'contract': contract, 'source': source, 'room': room,
                         'cover': (available / daily).quantize(Decimal('0.1')) if daily else None, 'key': f'{site.pk}:{p.pk}',
                         'suggest': (min(short, room) if room is not None and short > room else short)})
    rows.sort(key=lambda r: (-(r['short'] > 0), r['cover'] if r['cover'] is not None else Decimal('9999'), r['site'].code))
    return rows
