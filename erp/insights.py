"""Dashboards and KPIs (SRS §11, Step 9).

One read-only engine for every dashboard, the role-based home page and drill-down lists, so a number on the
executive page, the sales page and the source-document list always agree.

Rules:
- every operational figure is limited to the plants/depots the user may see (`sites`); company money (collections,
  receivables, cash, P&L) is shown only with the explicit company-finance scope, as on the accounting screens;
- sales are net: tax-invoice lines minus credit-note lines (value and returned quantity) in the period;
- tonnage converts bags / kg to MT with the product's bag weight (stock.mt_factor); pieces without a weight count 0 MT;
- nothing here writes: dashboards never post, allocate or change a document.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum, Count, Q, F, DecimalField, ExpressionWrapper, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.urls import reverse
from django.utils import timezone

from .models import (Site, Product, Partner, Order, OrderLine, Invoice, InvoiceLine, CreditNote, CreditNoteLine, Receipt, Dispatch, DispatchLine,
                     GoodsReceipt, GRNLine, SupplyContract, Production, ShiftLog, PackingRun, QualityResult, LabEquipment, Complaint, CapaAction,
                     Quotation, CRMLead, SalesTarget, StockTransfer, DamageReport, StockCount, PurchaseRequest, SupplierInvoice, SupplierPayment,
                     JournalEntry, JournalLine, Account, FreightBill, SchemeVersion, SchemeEntitlementBatch, SchemeSettlement, DealerApplication,
                     InfluencerUsage, BankStatementLine, CUSTOMER_KINDS, COMPLAINT_CATEGORIES)
from .stock import mt_factor, position

ZERO = Decimal('0')
DEC = DecimalField(max_digits=18, decimal_places=3)
FG = ('Cement', 'TMT')


# ---------- Period & scope ----------

PERIODS = [('today', 'Today'), ('week', 'Last 7 days'), ('mtd', 'This month'), ('last_month', 'Last month'), ('qtd', 'This quarter'),
           ('ytd', 'This financial year'), ('custom', 'Custom')]


def fy_start(d):
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def shift_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


class Period:
    """Reporting window plus the comparable earlier window (same days last month / last year / preceding days)."""

    def __init__(self, key, start, end, label):
        self.key, self.start, self.end, self.label = key, start, end, label
        if key in ('mtd', 'last_month', 'qtd'):
            n = 3 if key == 'qtd' else 1
            self.prev_start, self.prev_end = shift_months(start, -n), shift_months(end, -n)
        elif key == 'ytd':
            self.prev_start, self.prev_end = shift_months(start, -12), shift_months(end, -12)
        else:
            days = (end - start).days + 1
            self.prev_start, self.prev_end = start - timedelta(days=days), start - timedelta(days=1)

    @property
    def days(self):
        return (self.end - self.start).days + 1

    @property
    def query(self):
        return f'p={self.key}' + (f'&from={self.start.isoformat()}&to={self.end.isoformat()}' if self.key == 'custom' else '')


def parse_period(params, today=None):
    today = today or timezone.localdate()
    key = params.get('p') or 'mtd'
    if key == 'today':
        return Period(key, today, today, 'Today')
    if key == 'week':
        return Period(key, today - timedelta(days=6), today, 'Last 7 days')
    if key == 'last_month':
        end = today.replace(day=1) - timedelta(days=1)
        return Period(key, end.replace(day=1), end, end.strftime('%B %Y'))
    if key == 'qtd':
        q = (today.month - 4) % 12 // 3
        start = shift_months(fy_start(today), q * 3)
        return Period(key, start, today, f'Q{q + 1} FY {fy_label(today)}')
    if key == 'ytd':
        return Period(key, fy_start(today), today, f'FY {fy_label(today)}')
    if key == 'custom':
        try:
            start, end = date.fromisoformat(params.get('from', '')), date.fromisoformat(params.get('to', ''))
        except ValueError:
            start = end = None
        if start and end and start <= end and (end - start).days <= 3660:
            return Period(key, start, end, f'{start:%d %b %Y} – {end:%d %b %Y}')
    return Period('mtd', today.replace(day=1), today, today.strftime('%B %Y'))


def fy_label(d):
    s = fy_start(d).year
    return f'{s}-{str(s + 1)[2:]}'


def visible_sites(user):
    qs = Site.objects.filter(active=True).order_by('code')
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


def company_finance(user):
    return user.has_perm('erp.access_company_finance')


def change(now, before):
    """% change vs the earlier period; None when there is nothing to compare with."""
    if not before:
        return None
    return ((Decimal(now) - Decimal(before)) * 100 / abs(Decimal(before))).quantize(Decimal('0.1'))


def pct(part, whole):
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal('0.1')) if whole else None


class _Factors(dict):
    def __missing__(self, pid):
        p = Product.objects.get(pk=pid)
        self[pid] = mt_factor(p) or ZERO
        return self[pid]


def _mt(factors, pid, q):
    return ((q or ZERO) * factors[pid]).quantize(Decimal('0.001'))


# ---------- Sales ----------

DIMS = {
    'product': (None, None),
    'state': ('invoice__place_of_supply', 'credit_note__invoice__place_of_supply'),
    'district': ('invoice__partner__district', 'credit_note__partner__district'),
    'partner': ('invoice__partner', 'credit_note__partner'),
    'owner': ('invoice__order__created_by', 'credit_note__invoice__order__created_by'),
    'site': ('invoice__site', 'credit_note__invoice__site'),
    'month': (TruncMonth('invoice__date'), TruncMonth('credit_note__date')),
}


def net_sales(sites, start, end, dim='product', owner=None):
    """{key: {'value', 'mt', 'qty', 'products'}} — invoice lines minus credit-note lines, grouped by `dim` (and product for tonnage)."""
    di, dc = DIMS[dim]
    il = InvoiceLine.objects.filter(invoice__site__in=sites, invoice__date__range=(start, end))
    cl = CreditNoteLine.objects.filter(credit_note__invoice__site__in=sites, credit_note__date__range=(start, end))
    if owner is not None:
        il, cl = il.filter(invoice__order__created_by=owner), cl.filter(credit_note__invoice__order__created_by=owner)
    il = il.annotate(k=F('product') if di is None else (di if not isinstance(di, str) else F(di)), pid=F('product'))
    cl = cl.annotate(k=F('invoice_line__product') if dc is None else (dc if not isinstance(dc, str) else F(dc)), pid=F('invoice_line__product'))
    out = defaultdict(lambda: {'value': ZERO, 'mt': ZERO, 'qty': ZERO})
    f = _Factors()
    for r in il.values('k', 'pid').annotate(q=Sum('quantity'), v=Sum('taxable')):
        o = out[r['k']]
        o['value'] += r['v'] or ZERO
        o['qty'] += r['q'] or ZERO
        o['mt'] += _mt(f, r['pid'], r['q'])
    for r in cl.values('k', 'pid').annotate(q=Sum('quantity'), v=Sum('taxable')):
        o = out[r['k']]
        o['value'] -= r['v'] or ZERO
        o['qty'] -= r['q'] or ZERO
        o['mt'] -= _mt(f, r['pid'], r['q'])
    return dict(out)


def _total(groups):
    return {'value': sum((g['value'] for g in groups.values()), ZERO), 'mt': sum((g['mt'] for g in groups.values()), ZERO)}


def _ranked(groups, labels, limit=None, link=None):
    total = sum((g['value'] for g in groups.values()), ZERO)
    top_mt = max((g['mt'] for g in groups.values()), default=ZERO)
    top_val = max((g['value'] for g in groups.values()), default=ZERO)
    rows = []
    for k, g in sorted(groups.items(), key=lambda kv: (-kv[1]['mt'], -kv[1]['value'])):
        rows.append({'key': k, 'label': labels(k), 'value': g['value'], 'mt': g['mt'], 'qty': g['qty'],
                     'share': pct(g['value'], total), 'bar': pct(g['mt'], top_mt) if top_mt > 0 else pct(g['value'], top_val),
                     'per_mt': (g['value'] / g['mt']).quantize(Decimal('1')) if g['mt'] > 0 else None,
                     'url': link(k) if link else None})
    return rows[:limit] if limit else rows


def sales_summary(sites, period, drill=''):
    by_product = net_sales(sites, period.start, period.end, 'product')
    now = _total(by_product)
    before = _total(net_sales(sites, period.prev_start, period.prev_end, 'product'))
    invoices = Invoice.objects.filter(site__in=sites, date__range=(period.start, period.end))
    products = {p.pk: p for p in Product.objects.filter(pk__in=list(by_product))}
    q = period.query
    d = reverse('insight_drill', args=['sales'])
    from .models import INDIAN_STATES
    states = dict(INDIAN_STATES)
    partners = {p.pk: p.name for p in Partner.objects.filter(pk__in=[k for k in net_sales(sites, period.start, period.end, 'partner')])}
    users = _user_names()
    targets = SalesTarget.objects.filter(site__in=sites, month__range=(period.start.replace(day=1), period.end))
    target = targets.aggregate(t=Sum('sales_amount'))['t'] or ZERO
    month_start = period.start.replace(day=1)
    target_actual = _total(net_sales(sites, month_start, period.end, 'product'))['value'] if target else ZERO
    return {
        'value': now['value'], 'mt': now['mt'], 'prev_value': before['value'], 'prev_mt': before['mt'],
        'value_change': change(now['value'], before['value']), 'mt_change': change(now['mt'], before['mt']),
        'invoices': invoices.count(), 'dealers': invoices.values('partner').distinct().count(),
        'per_mt': (now['value'] / now['mt']).quantize(Decimal('1')) if now['mt'] > 0 else None,
        'target': target, 'target_actual': target_actual, 'target_pct': pct(target_actual, target),
        'products': _ranked(by_product, lambda k: products[k].name if k in products else '—', link=lambda k: f'{d}?{q}&product={k}{drill}'),
        'states': _ranked(net_sales(sites, period.start, period.end, 'state'), lambda k: states.get(k, k or 'Not set'), 10, lambda k: f'{d}?{q}&state={k or ""}{drill}'),
        'districts': _ranked(net_sales(sites, period.start, period.end, 'district'), lambda k: k or 'District not set', 10, lambda k: f'{d}?{q}&district={k or ""}{drill}'),
        'dealers': _ranked(net_sales(sites, period.start, period.end, 'partner'), lambda k: partners.get(k, '—'), 10, lambda k: f'{d}?{q}&partner={k}{drill}'),
        'owners': _ranked(net_sales(sites, period.start, period.end, 'owner'), lambda k: users.get(k, '—'), 10, lambda k: f'{d}?{q}&owner={k}{drill}'),
        'sites': _ranked(net_sales(sites, period.start, period.end, 'site'), lambda k: next((s.code for s in sites if s.pk == k), '—'), None, lambda k: f'{d}?{q}&site={k}'),
    }


def _user_names():
    from django.contrib.auth import get_user_model
    return {u.pk: (u.get_full_name() or u.username) for u in get_user_model().objects.all()}


def trend(sites, months=12, today=None, finance=False):
    """Last `months` calendar months: net MT and value, plus collections when finance scope allows."""
    today = today or timezone.localdate()
    first = shift_months(today.replace(day=1), -(months - 1))
    groups = net_sales(sites, first, today, 'month')
    by_month = {(k.date() if hasattr(k, 'date') else k).replace(day=1): v for k, v in groups.items() if k}
    coll = {}
    if finance:
        for r in Receipt.objects.filter(date__gte=first, date__lte=today, status='posted').annotate(m=TruncMonth('date')).values('m').annotate(t=Sum('amount')):
            coll[(r['m'].date() if hasattr(r['m'], 'date') else r['m']).replace(day=1)] = r['t']
    rows, m = [], first
    while m <= today:
        g = by_month.get(m, {'value': ZERO, 'mt': ZERO})
        rows.append({'month': m, 'label': m.strftime('%b'), 'year': m.year, 'mt': g['mt'], 'value': g['value'], 'collected': coll.get(m, ZERO)})
        m = shift_months(m, 1)
    top = max([r['mt'] for r in rows] + [Decimal('0.001')])
    top_v = max([max(r['value'], r['collected']) for r in rows] + [Decimal('1')])
    for r in rows:
        r['h'] = pct(max(r['mt'], ZERO), top) or ZERO
        r['hv'] = pct(max(r['value'], ZERO), top_v) or ZERO
        r['hc'] = pct(max(r['collected'], ZERO), top_v) or ZERO
    return rows


def order_book(sites):
    lines = OrderLine.objects.filter(order__kind='Sale', order__status__in=['Approved', 'Part dispatched'], order__site__in=sites).annotate(
        sent=Coalesce(Sum(Coalesce('dispatch_lines__loaded_quantity', 'dispatch_lines__planned_quantity'), filter=~Q(dispatch_lines__dispatch__status='cancelled')),
                      Value(ZERO), output_field=DEC))
    f, mt, value, orders = _Factors(), ZERO, ZERO, set()
    for l in lines.values('pk', 'product', 'quantity', 'rate', 'sent', 'order'):
        pending = max(l['quantity'] - l['sent'], ZERO)
        if pending > 0:
            mt += _mt(f, l['product'], pending)
            value += pending * l['rate']
            orders.add(l['order'])
    waiting = Order.objects.filter(kind='Sale', status='Submitted', site__in=sites)
    oldest = Order.objects.filter(pk__in=orders).order_by('created_at').first()
    return {'mt': mt, 'value': value.quantize(Decimal('1')), 'orders': len(orders), 'waiting': waiting.count(),
            'oldest_days': (timezone.now() - oldest.created_at).days if oldest else None}


def quote_funnel(sites, period):
    qs = Quotation.objects.filter(site__in=sites, date__range=(period.start, period.end)).exclude(status='revised')
    c = dict(qs.values_list('status').annotate(n=Count('pk')))
    decided = sum(c.get(k, 0) for k in ('accepted', 'converted', 'rejected', 'expired'))
    won = c.get('accepted', 0) + c.get('converted', 0)
    return {'made': sum(c.values()), 'open': c.get('draft', 0) + c.get('approval', 0) + c.get('ready', 0) + c.get('sent', 0),
            'won': won, 'lost': c.get('rejected', 0) + c.get('expired', 0), 'win_rate': pct(won, decided), 'approval': c.get('approval', 0)}


def crm_pipeline(sites, user=None):
    qs = CRMLead.objects.filter(site__in=sites).exclude(stage__in=['won', 'lost'])
    if user is not None:
        qs = qs.filter(owner=user)
    today = timezone.localdate()
    return {'open': qs.count(), 'follow_ups': qs.filter(next_follow_up__lte=today).count(), 'value': qs.aggregate(t=Sum('estimated_value'))['t'] or ZERO}


# ---------- Receivables & credit (company finance scope) ----------

def _open_invoices():
    return Invoice.objects.annotate(paid=Coalesce(Sum('settlements__amount'), Value(ZERO), output_field=DEC)).annotate(
        due=ExpressionWrapper(F('grand_total') - F('paid'), output_field=DEC)).filter(due__gt=0)


def partner_balances():
    """Partner → ledger balance (same formula as ledger.balance, in four grouped queries)."""
    from .models import SchemeSettlement
    out = defaultdict(lambda: ZERO)
    for r in Invoice.objects.values('partner').annotate(t=Sum('grand_total')):
        out[r['partner']] += r['t']
    for r in Receipt.objects.filter(status='posted').values('partner').annotate(t=Sum('amount')):
        out[r['partner']] -= r['t']
    for r in CreditNote.objects.values('partner').annotate(t=Sum('total')):
        out[r['partner']] -= r['t']
    for r in SchemeSettlement.objects.filter(mode='ledger', status='posted').values('partner').annotate(t=Sum('amount')):
        out[r['partner']] -= r['t']
    return out


def receivables(period, today=None):
    today = today or timezone.localdate()
    buckets = {'current': ZERO, 'd30': ZERO, 'd60': ZERO, 'd90': ZERO, 'd90p': ZERO}
    per_partner = defaultdict(lambda: {'due': ZERO, 'overdue': ZERO, 'oldest': 0})
    for i in _open_invoices().values('partner', 'due', 'due_date'):
        days = (today - i['due_date']).days
        k = 'current' if days <= 0 else 'd30' if days <= 30 else 'd60' if days <= 60 else 'd90' if days <= 90 else 'd90p'
        buckets[k] += i['due']
        p = per_partner[i['partner']]
        p['due'] += i['due']
        if days > 0:
            p['overdue'] += i['due']
            p['oldest'] = max(p['oldest'], days)
    total = sum(buckets.values(), ZERO)
    overdue = total - buckets['current']
    balances = partner_balances()
    partners = Partner.objects.in_bulk(set(per_partner) | {k for k, v in balances.items() if v})
    drill = reverse('insight_drill', args=['overdue'])
    overdue_rows = sorted(({'partner': partners[k], 'due': v['due'], 'overdue': v['overdue'], 'days': v['oldest'], 'url': f'{drill}?partner={k}'}
                           for k, v in per_partner.items() if v['overdue'] > 0), key=lambda r: -r['overdue'])
    over_limit = []
    for k, bal in balances.items():
        p = partners.get(k)
        if p and p.kind in CUSTOMER_KINDS and bal > 0 and bal > p.credit_limit:
            over_limit.append({'partner': p, 'balance': bal, 'limit': p.credit_limit, 'excess': bal - p.credit_limit})
    over_limit.sort(key=lambda r: -r['excess'])
    advances = -sum((v for k, v in balances.items() if v < 0 and partners.get(k) and partners[k].kind in CUSTOMER_KINDS), ZERO)
    billed_90 = Invoice.objects.filter(date__gt=today - timedelta(days=90), date__lte=today).aggregate(t=Sum('grand_total'))['t'] or ZERO
    collected = Receipt.objects.filter(date__range=(period.start, period.end), status='posted').aggregate(t=Sum('amount'), n=Count('pk'))
    prev = Receipt.objects.filter(date__range=(period.prev_start, period.prev_end), status='posted').aggregate(t=Sum('amount'))['t'] or ZERO
    bounced = Receipt.objects.filter(status='bounced', bounced_on__range=(period.start, period.end)).aggregate(t=Sum('amount'), n=Count('pk'))
    billed = Invoice.objects.filter(date__range=(period.start, period.end)).aggregate(t=Sum('grand_total'))['t'] or ZERO
    labels = [('current', 'Not due'), ('d30', '1–30 days'), ('d60', '31–60 days'), ('d90', '61–90 days'), ('d90p', '90+ days')]
    return {'total': total, 'overdue': overdue, 'overdue_pct': pct(overdue, total), 'd90p': buckets['d90p'],
            'ageing': [{'key': k, 'label': l, 'amount': buckets[k], 'share': pct(buckets[k], total) or ZERO,
                        'url': f'{drill}?bucket={k}'} for k, l in labels],
            'dso': (total / (billed_90 / 90)).quantize(Decimal('1')) if billed_90 else None,
            'overdue_dealers': overdue_rows[:10], 'overdue_count': len(overdue_rows), 'over_limit': over_limit[:10], 'over_limit_count': len(over_limit),
            'advances': advances, 'collected': collected['t'] or ZERO, 'receipts': collected['n'], 'collected_prev': prev,
            'collected_change': change(collected['t'] or ZERO, prev), 'billed': billed, 'efficiency': pct(collected['t'] or ZERO, billed),
            'bounced': bounced['t'] or ZERO, 'bounced_count': bounced['n']}


# ---------- Stock ----------

def stock_summary(sites, today=None):
    today = today or timezone.localdate()
    rows = position(list(sites))
    since = today - timedelta(days=30)
    use = defaultdict(lambda: ZERO)
    for r in InvoiceLine.objects.filter(invoice__site__in=sites, invoice__date__gt=since).values('invoice__site', 'product').annotate(q=Sum('quantity')):
        use[(r['invoice__site'], r['product'])] += r['q']
    f = _Factors()
    fg = {'available': ZERO, 'reserved': ZERO, 'free': ZERO, 'transit': ZERO}
    value = ZERO
    low, short, idle = [], [], []
    for r in rows:
        value += r['value']
        p = r['product']
        daily = use[(r['site'].pk, p.pk)] / 30
        r['cover'] = (r['available'] / daily).quantize(Decimal('0.1')) if daily else None
        r['url'] = reverse('stock_card', args=[r['site'].pk, p.pk])
        if p.category in FG:
            for k in ('available', 'reserved', 'transit'):
                fg[k] += _mt(f, p.pk, r[k])
            fg['free'] += _mt(f, p.pk, r['free'])
            if r['free'] < 0:
                short.append(r)
            if r['available'] > 0 and not daily:
                idle.append(r)
        if r['low']:
            low.append(r)
    damaged = sum((_mt(f, r['product'].pk, r['damaged'] + r['blocked']) for r in rows if r['product'].category in FG), ZERO)
    by_site = defaultdict(lambda: {'mt': ZERO, 'value': ZERO})
    for r in rows:
        if r['product'].category in FG:
            by_site[r['site']]['mt'] += _mt(f, r['product'].pk, r['available'])
        by_site[r['site']]['value'] += r['value']
    top = max([v['mt'] for v in by_site.values()] + [Decimal('0.001')])
    cover_rows = sorted((r for r in rows if r['product'].category in FG and r['cover'] is not None), key=lambda r: r['cover'])
    return {'fg': fg, 'value': value, 'damaged_mt': damaged, 'low': low, 'short': short, 'idle': idle[:10], 'cover': cover_rows[:10],
            'sites': [{'site': s, 'mt': v['mt'], 'value': v['value'], 'bar': pct(v['mt'], top)} for s, v in sorted(by_site.items(), key=lambda kv: kv[0].code)],
            'transfers': StockTransfer.objects.filter(Q(from_site__in=sites) | Q(to_site__in=sites), status='in_transit').distinct().count(),
            'damage_open': DamageReport.objects.filter(site__in=sites, status='reported').count(), 'rows': rows}


# ---------- Dispatch & logistics ----------

def _line_mt(lines):
    f, mt = _Factors(), ZERO
    for r in lines.values('order_line__product').annotate(q=Sum(Coalesce('loaded_quantity', 'planned_quantity'))):
        mt += _mt(f, r['order_line__product'], r['q'])
    return mt


def dispatch_summary(sites, period, today=None):
    now = timezone.now()
    today = today or timezone.localdate()
    live = Dispatch.objects.filter(site__in=sites).exclude(status__in=['delivered', 'cancelled'])
    yard = live.exclude(status='gate_out')
    stages = dict(yard.values_list('status').annotate(n=Count('pk')))
    from .models import DISPATCH_STATUSES
    queue = [{'status': k, 'label': l, 'count': stages.get(k, 0)} for k, l in DISPATCH_STATUSES if k not in ('gate_out', 'delivered', 'cancelled')]
    out = Dispatch.objects.filter(site__in=sites, gate_out_at__date__range=(period.start, period.end))
    trips = out.count()
    mt = _line_mt(DispatchLine.objects.filter(dispatch__in=out))
    turn = [(d.gate_out_at - d.gate_in_at).total_seconds() / 3600 for d in out.exclude(gate_in_at=None).only('gate_in_at', 'gate_out_at')]
    freight = out.filter(freight_payable_by='company').aggregate(t=Sum('freight_amount'))['t'] or ZERO
    transit = live.filter(status='gate_out').select_related('order__partner', 'vehicle').order_by('gate_out_at')
    pod = [{'d': d, 'hours': int((now - d.gate_out_at).total_seconds() // 3600)} for d in transit[:10]]
    delivered = Dispatch.objects.filter(site__in=sites, delivered_at__date__range=(period.start, period.end))
    claims = delivered.filter(Q(shortage_qty__gt=0) | Q(damaged_qty__gt=0)).count()
    on_time = delivered.count()
    by_transporter = defaultdict(lambda: {'trips': 0})
    for r in out.values('transporter__name').annotate(n=Count('pk')):
        by_transporter[r['transporter__name'] or 'Own / customer vehicle']['trips'] += r['n']
    return {'queue': queue, 'in_yard': sum(stages.values()), 'trips': trips, 'mt': mt, 'transit': transit.count(),
            'turnaround': (Decimal(sum(turn) / len(turn)).quantize(Decimal('0.1'))) if turn else None,
            'freight': freight, 'freight_per_mt': (freight / mt).quantize(Decimal('1')) if mt > 0 and freight else None,
            'pod': pod, 'pod_late': sum(1 for p in pod if p['hours'] > 48), 'delivered': on_time, 'claims': claims, 'claim_pct': pct(claims, on_time),
            'exceptions': live.exclude(exception='').filter(exception_approved_by=None).count(),
            'planned_today': Dispatch.objects.filter(site__in=sites, planned_for=today).exclude(status='cancelled').count(),
            'transporters': sorted(({'name': k, **v} for k, v in by_transporter.items()), key=lambda r: -r['trips'])[:8]}


# ---------- Manufacturing-partner supply (asset-light sourcing) ----------

def sourcing_summary(sites, period):
    lines = GRNLine.objects.filter(grn__status='posted', grn__posted_at__date__range=(period.start, period.end), grn__site__in=sites,
                                   grn__supplier__manufacturing_partner=True)
    f = _Factors()
    rows = defaultdict(lambda: {'received': ZERO, 'usable': ZERO, 'rejected': ZERO, 'value': ZERO, 'trucks': set()})
    for l in lines.values('grn__supplier', 'grn__site', 'product', 'received', 'accepted', 'qc_accepted', 'rejected', 'damaged', 'qc_rejected', 'po_line__rate', 'grn'):
        r = rows[(l['grn__supplier'], l['grn__site'])]
        usable = l['accepted'] + l['qc_accepted']
        r['received'] += _mt(f, l['product'], l['received'])
        r['usable'] += _mt(f, l['product'], usable)
        r['rejected'] += _mt(f, l['product'], l['rejected'] + l['damaged'] + l['qc_rejected'])
        r['value'] += usable * (l['po_line__rate'] or ZERO)
        r['trucks'].add(l['grn'])
    months = Decimal(period.days) / Decimal('30.4')
    commit = defaultdict(lambda: ZERO)
    for c in SupplyContract.objects.filter(status__in=['approved', 'ended'], site__in=sites, valid_from__lte=period.end).filter(
            Q(valid_to__isnull=True) | Q(valid_to__gte=period.start)).select_related('product'):
        commit[(c.partner_id, c.site_id)] += _mt(f, c.product_id, c.monthly_quantity) * months
    keys = set(rows) | {k for k, v in commit.items() if v}
    partners, site_map = Partner.objects.in_bulk({k[0] for k in keys}), Site.objects.in_bulk({k[1] for k in keys})
    out = []
    drill = reverse('insight_drill', args=['supply'])
    for k in keys:
        r = rows[k] if k in rows else {'received': ZERO, 'usable': ZERO, 'rejected': ZERO, 'value': ZERO, 'trucks': set()}
        c = commit.get(k, ZERO).quantize(Decimal('0.1'))
        out.append({'partner': partners[k[0]], 'site': site_map[k[1]], 'received': r['received'], 'usable': r['usable'], 'commit': c,
                    'fulfil': pct(r['usable'], c), 'reject_pct': pct(r['rejected'], r['received']), 'trucks': len(r['trucks']),
                    'per_mt': (r['value'] / r['usable']).quantize(Decimal('1')) if r['usable'] > 0 else None,
                    'url': f'{drill}?{period.query}&partner={k[0]}&site={k[1]}'})
    out.sort(key=lambda r: -r['usable'])
    usable, committed, received = sum((r['usable'] for r in out), ZERO), sum((r['commit'] for r in out), ZERO), sum((r['received'] for r in out), ZERO)
    open_po = OrderLine.objects.filter(order__kind='Purchase', order__status__in=['Approved', 'Part received'], order__site__in=sites,
                                       order__partner__manufacturing_partner=True)
    pending = ZERO
    for l in open_po.select_related('product'):
        pending += _mt(f, l.product_id, max(l.quantity - l.received_quantity(), ZERO))
    return {'rows': out, 'usable': usable, 'commit': committed, 'fulfil': pct(usable, committed), 'received': received,
            'reject_pct': pct(sum((r['received'] - r['usable'] for r in out), ZERO), received), 'pending_po': pending,
            'value': sum((rows[k]['value'] for k in rows), ZERO), 'contracts_waiting': SupplyContract.objects.filter(status='draft', site__in=sites).count(),
            'partners': len({r['partner'].pk for r in out})}


# ---------- Plant ----------

def plant_summary(sites, period):
    batches = Production.objects.filter(site__in=sites, completed_at__date__range=(period.start, period.end))
    f = _Factors()
    made = defaultdict(lambda: {'qty': ZERO, 'scrap': ZERO, 'batches': 0})
    for r in batches.values('product').annotate(q=Sum('actual_quantity'), s=Sum('scrap_quantity'), n=Count('pk')):
        made[r['product']] = {'qty': r['q'] or ZERO, 'scrap': r['s'] or ZERO, 'batches': r['n']}
    products = Product.objects.in_bulk(list(made))
    output_mt = sum((_mt(f, k, v['qty']) for k, v in made.items()), ZERO)
    logs = ShiftLog.objects.filter(site__in=sites, date__range=(period.start, period.end))
    agg = logs.aggregate(run=Sum('run_hours'), down=Sum('downtime_minutes'), kwh=Sum('power_kwh'), out=Sum('output'), n=Count('pk'))
    run, down = agg['run'] or ZERO, Decimal(agg['down'] or 0) / 60
    reasons = [{'reason': dict(ShiftLog._meta.get_field('downtime_reason').choices).get(r['downtime_reason'], 'Other'), 'hours': (Decimal(r['m']) / 60).quantize(Decimal('0.1'))}
               for r in logs.exclude(downtime_minutes=0).values('downtime_reason').annotate(m=Sum('downtime_minutes')).order_by('-m')]
    packing = PackingRun.objects.filter(site__in=sites, date__range=(period.start, period.end)).aggregate(bags=Sum('bags_packed'), burst=Sum('bags_burst'), diff=Sum('difference_mt'), n=Count('pk'))
    status = dict(Production.objects.filter(site__in=sites).exclude(status__in=['Released', 'Rejected', 'Cancelled']).values_list('status').annotate(n=Count('pk')))
    return {'output_mt': output_mt, 'batches': batches.count(), 'status': status,
            'products': [{'product': products[k], 'qty': v['qty'], 'scrap_pct': pct(v['scrap'], v['qty'] + v['scrap']), 'batches': v['batches']} for k, v in made.items()],
            'run': run, 'down': down.quantize(Decimal('0.1')), 'availability': pct(run, run + down), 'shifts': agg['n'],
            'kwh_unit': (agg['kwh'] / agg['out']).quantize(Decimal('0.01')) if agg['out'] and agg['kwh'] else None, 'reasons': reasons,
            'bags': packing['bags'] or 0, 'burst_pct': pct(packing['burst'] or 0, (packing['bags'] or 0) + (packing['burst'] or 0)),
            'pack_diff': packing['diff'] or ZERO, 'pack_runs': packing['n']}


# ---------- Quality & complaints ----------

def quality_summary(sites, period, sold_mt=None):
    today = timezone.localdate()
    res = QualityResult.objects.filter(production__site__in=sites, tested_at__date__range=(period.start, period.end), superseded_by__isnull=True)
    n, ok = res.count(), res.filter(ok=True).count()
    disp = dict(Production.objects.filter(site__in=sites, released_at__date__range=(period.start, period.end)).values_list('disposition').annotate(n=Count('pk')))
    holds = Production.objects.filter(site__in=sites, status='Quality hold').count() + GRNLine.objects.filter(grn__site__in=sites, grn__status='posted', hold__gt=0, qc_at__isnull=True).count()
    # Complaints are customer-level: a complaint is in scope through its invoice's plant, or company-wide when not linked.
    cq = Complaint.objects.filter(Q(invoice__site__in=sites) | Q(invoice__isnull=True))
    new = cq.filter(received_on__range=(period.start, period.end))
    open_ = cq.exclude(status='closed')
    closed = cq.filter(status='closed', closed_at__date__range=(period.start, period.end))
    days = [(c.closed_at.date() - c.received_on).days for c in closed.only('closed_at', 'received_on')]
    within = sum(1 for c in closed.only('closed_at', 'due_on') if c.closed_at.date() <= c.due_on)
    cats = dict(COMPLAINT_CATEGORIES)
    drill = reverse('insight_drill', args=['complaints'])
    by_cat = [{'label': cats.get(r['category'], r['category']).split(' (')[0], 'count': r['n'], 'url': f'{drill}?{period.query}&category={r["category"]}'}
              for r in new.values('category').annotate(n=Count('pk')).order_by('-n')]
    capa = CapaAction.objects.filter(Q(complaint__in=cq) | Q(complaint__isnull=True))
    verified = capa.filter(verified_at__date__range=(period.start, period.end))
    return {'tests': n, 'pass_pct': pct(ok, n), 'failed': n - ok, 'released': disp.get('release', 0), 'deviation': disp.get('deviation', 0), 'rejected': disp.get('reject', 0),
            'holds': holds, 'calibration_due': LabEquipment.objects.filter(site__in=sites, active=True, calibration_due__lt=today).count(),
            'complaints_new': new.count(), 'complaints_open': open_.count(), 'complaints_late': open_.filter(due_on__lt=today).count(),
            'per_1000': (Decimal(new.count()) * 1000 / sold_mt).quantize(Decimal('0.01')) if sold_mt else None,
            'resolve_days': (Decimal(sum(days)) / len(days)).quantize(Decimal('0.1')) if days else None, 'sla_pct': pct(within, len(days)),
            'by_category': by_cat, 'capa_open': capa.filter(status__in=['open', 'reopened']).count(), 'capa_late': capa.filter(status__in=['open', 'reopened'], due_on__lt=today).count(),
            'capa_verify': capa.filter(status='done').count(), 'capa_effective_pct': pct(verified.filter(status='effective').count(), verified.count())}


# ---------- Finance (company scope) ----------

def finance_summary(period):
    from . import gl, treasury
    pl = gl.profit_and_loss(period.start, period.end)
    group = lambda rows, name: next((g[2] for g in rows if g[0] == name), ZERO)
    revenue = group(pl['income'], 'Revenue from operations')
    cogs = group(pl['expense'], 'Cost of materials consumed')
    banks = []
    for a in Account.objects.filter(is_bank=True, active=True).order_by('code'):
        b = JournalLine.objects.filter(account=a, entry__status='posted', entry__date__lte=period.end).aggregate(d=Sum('debit'), c=Sum('credit'))
        banks.append({'account': a, 'balance': (b['d'] or ZERO) - (b['c'] or ZERO)})

    def control(*keys, sign=1):
        b = JournalLine.objects.filter(account__system_key__in=keys, entry__status='posted', entry__date__lte=period.end).aggregate(d=Sum('debit'), c=Sum('credit'))
        return sign * ((b['d'] or ZERO) - (b['c'] or ZERO))
    gst = treasury.gst_summary(period.start, period.end)
    pay = SupplierPayment.objects.filter(status__in=['proposed', 'approved'])
    return {'income': pl['total_income'], 'expense': pl['total_expense'], 'profit': pl['profit'], 'revenue': revenue, 'cogs': cogs,
            'gross': revenue - cogs, 'gross_pct': pct(revenue - cogs, revenue), 'net_pct': pct(pl['profit'], pl['total_income']),
            'expenses': sorted(((g[0], g[2]) for g in pl['expense']), key=lambda x: -x[1]),
            'banks': banks, 'cash': sum((b['balance'] for b in banks), ZERO), 'ar': control('ar'), 'ap': control('ap', 'ap_freight', sign=-1),
            'grni': control('grni', sign=-1), 'scheme_payable': control('scheme_payable', sign=-1), 'inventory': control('inventory_rm', 'inventory_stores', 'inventory_fg', 'wip', 'stock_transit'),
            'gst_output': gst['output_tax'], 'gst_input': gst['input_tax'], 'gst_net': gst['net'],
            'payments_waiting': pay.filter(status='proposed').aggregate(t=Sum('amount'), n=Count('pk')), 'payments_to_release': pay.filter(status='approved').aggregate(t=Sum('amount'), n=Count('pk')),
            'sinv_exceptions': SupplierInvoice.objects.filter(status='exception').count(), 'vouchers': JournalEntry.objects.filter(status='draft').count(),
            'unmatched': BankStatementLine.objects.filter(matched_line__isnull=True).count(), 'freight_bills': FreightBill.objects.filter(status='submitted').count()}


# ---------- Work waiting for this user (role-based home) ----------

def my_queue(user, sites=None):
    """Approvals and tasks waiting for `user`, each gated by the permission the action needs; own documents are excluded where
    the rule is maker-checker, so the count is what this person can actually act on."""
    sites = list(sites if sites is not None else visible_sites(user))
    today = timezone.localdate()
    has = lambda p: user.has_perm('erp.' + p)
    fin = company_finance(user)
    items = []

    def add(label, qs, url, hint, tone='warn'):
        n = qs.count() if hasattr(qs, 'count') else qs
        if n:
            items.append({'label': label, 'count': n, 'url': url, 'hint': hint, 'tone': tone})
    if has('approve_sales_order'):
        add('Sales orders to approve', Order.objects.filter(kind='Sale', status='Submitted', site__in=sites).exclude(created_by=user), reverse('sales') + '?status=Submitted', 'Credit and price checked on approval')
    if has('approve_purchase_order'):
        add('Purchase orders to approve', Order.objects.filter(kind='Purchase', status='Submitted', site__in=sites).exclude(created_by=user), reverse('purchases') + '?status=Submitted', 'Contract rate and budget')
    if has('approve_purchaserequest'):
        add('Purchase requests to approve', PurchaseRequest.objects.filter(status='submitted', site__in=sites).exclude(created_by=user), reverse('purchase_requests'), 'Plant needs')
    if has('approve_quotation'):
        add('Quotation discounts to approve', Quotation.objects.filter(status='approval', site__in=sites).exclude(created_by=user), reverse('quotations') + '?status=approval', 'Below list price')
    if has('approve_supplycontract'):
        add('Partner rate contracts to approve', SupplyContract.objects.filter(status='draft', site__in=sites).exclude(created_by=user), reverse('mfg_partners'), 'Manufacturing partner rates')
    if has('approve_stocktransfer'):
        add('Stock transfers to approve', StockTransfer.objects.filter(status='requested', from_site__in=sites).exclude(created_by=user), reverse('transfers'), 'Between plants and depots')
    if has('decide_damagereport'):
        add('Damaged stock to decide', DamageReport.objects.filter(status='reported', site__in=sites).exclude(created_by=user), reverse('damages'), 'Recover or scrap')
    if has('approve_stockcount'):
        add('Stock counts to approve', StockCount.objects.filter(status='submitted', site__in=sites).exclude(created_by=user), reverse('counts'), 'Variance posting')
    if has('release_production'):
        add('Batches on quality hold', Production.objects.filter(status='Quality hold', site__in=sites), reverse('production'), 'Release or reject')
    if has('release_goodsreceipt'):
        add('Received material on hold', GRNLine.objects.filter(grn__status='posted', hold__gt=0, qc_at__isnull=True, grn__site__in=sites), reverse('inbound'), 'Quality release of GRN')
    if has('approve_weight_exception'):
        add('Truck weight exceptions', Dispatch.objects.filter(site__in=sites).exclude(exception='').filter(exception_approved_by=None).exclude(status__in=['delivered', 'cancelled']), reverse('dispatch_board'), 'Overload or variance', 'danger')
    if has('pod_dispatch'):
        add('Deliveries awaiting POD', Dispatch.objects.filter(site__in=sites, status='gate_out'), reverse('dispatch_board'), 'Trucks out of the gate')
    if has('approve_freightbill'):
        add('Freight bills to check', FreightBill.objects.filter(status='submitted').exclude(created_by=user), reverse('freight_bills'), 'Against contracted freight')
    if has('decide_complaint'):
        add('Complaints to decide', Complaint.objects.filter(status='investigating').exclude(created_by=user).exclude(owner=user), reverse('complaints') + '?status=investigating', 'Findings recorded')
    if has('view_complaint'):
        add('Complaints you own', Complaint.objects.filter(owner=user).exclude(status='closed'), reverse('complaints'), 'Investigation and closure')
    if has('verify_capaaction'):
        add('CAPA to verify', CapaAction.objects.filter(status='done').exclude(owner=user), reverse('complaints'), 'Effectiveness check')
    add('CAPA actions you own', CapaAction.objects.filter(owner=user, status__in=['open', 'reopened']), reverse('complaints'), 'Corrective action due')
    if has('verify_influencerusage'):
        add('Mason / engineer purchases to verify', InfluencerUsage.objects.filter(status='pending').exclude(created_by=user), reverse('influencer_usage'), 'Star programme points')
    if has('approve_schemeversion'):
        add('Scheme rules to approve', SchemeVersion.objects.filter(status='submitted').exclude(created_by=user), reverse('schemes'), 'Incentive versions')
    if has('approve_schemeentitlementbatch'):
        add('Scheme entitlements to approve', SchemeEntitlementBatch.objects.filter(status='submitted').exclude(created_by=user), reverse('scheme_payouts'), 'Accrual')
    if has('approve_schemesettlement'):
        add('Scheme payouts to approve', SchemeSettlement.objects.filter(status='proposed').exclude(created_by=user), reverse('scheme_payouts'), 'Dealer credit or bank')
    for perm, state, label in (('review_dealerkyc', 'submitted', 'Dealer KYC to review'), ('review_dealercredit', 'kyc', 'Dealer credit to review'),
                               ('approve_dealerapplication', 'credit', 'Dealer applications to approve')):
        if has(perm):
            add(label, DealerApplication.objects.filter(status=state, site__in=sites).exclude(created_by=user), reverse('crm_applications'), 'Onboarding')
    if has('view_crmlead'):
        add('CRM follow-ups due', CRMLead.objects.filter(owner=user, next_follow_up__lte=today).exclude(stage__in=['won', 'lost']), reverse('crm_leads'), 'Today or overdue')
    if fin and has('approve_supplierinvoice'):
        add('Supplier invoice exceptions', SupplierInvoice.objects.filter(status='exception'), reverse('supplier_invoices'), 'Three-way match differences', 'danger')
    if fin and has('approve_supplierpayment'):
        add('Payments to approve', SupplierPayment.objects.filter(status='proposed').exclude(created_by=user), reverse('payments'), 'Supplier and transporter')
    if fin and has('release_supplierpayment'):
        add('Approved payments to release', SupplierPayment.objects.filter(status='approved'), reverse('payments'), 'Enter UTR after paying')
    if fin and has('approve_journalentry'):
        add('Journal vouchers to approve', JournalEntry.objects.filter(status='draft').exclude(created_by=user), reverse('journals'), 'Manual vouchers')
    return items


# ---------- Personal sales (sales executives) ----------

def my_sales(user, sites, period):
    groups = net_sales(sites, period.start, period.end, 'product', owner=user)
    t = _total(groups)
    target = SalesTarget.objects.filter(owner=user, month=period.end.replace(day=1)).aggregate(t=Sum('sales_amount'))['t'] or ZERO
    month = _total(net_sales(sites, period.end.replace(day=1), period.end, 'product', owner=user))['value']
    open_orders = Order.objects.filter(kind='Sale', created_by=user, status__in=['Draft', 'Submitted', 'Approved', 'Part dispatched'])
    return {'value': t['value'], 'mt': t['mt'], 'target': target, 'month': month, 'target_pct': pct(month, target),
            'orders': open_orders.count(), 'pipeline': crm_pipeline(sites, user),
            'quotes': Quotation.objects.filter(created_by=user, status__in=['draft', 'approval', 'ready', 'sent']).count()}
