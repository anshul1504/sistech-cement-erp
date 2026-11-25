"""Sales team performance: who sold how much, with charts (SRS §11 dashboards, FR-CRM performance).

Attribution follows the rest of the ERP: a sale belongs to the person who created the sales order (same as CRM performance
and commission). Figures are net of credit notes, before GST, for the plants the viewer can access. Collections are posted
receipts settled against that person's invoices. Charts are plain SVG/CSS built here so they work without any library.
"""
from collections import defaultdict
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Sum, Count

from . import insights as I
from .models import Invoice, Settlement, SalesTarget, Order

ZERO = Decimal('0')
PALETTE = ['#1d5fbf', '#f0a500', '#1f7a5a', '#b93a2f', '#6b3fb4', '#19717f', '#8a5a00', '#4a5668']


def _months(end, n):
    first = I.shift_months(end.replace(day=1), -(n - 1))
    return [I.shift_months(first, i) for i in range(n)]


def _targets(sites, start, end, owner=None):
    qs = SalesTarget.objects.filter(site__in=sites, month__gte=start.replace(day=1), month__lte=end)
    if owner is not None:
        qs = qs.filter(owner=owner)
    return qs


def _collected(sites, start, end, owner_ids=None):
    qs = Settlement.objects.filter(invoice__site__in=sites, receipt__status='posted', receipt__date__range=(start, end))
    if owner_ids is not None:
        qs = qs.filter(invoice__order__created_by__in=owner_ids)
    return {r['invoice__order__created_by']: r['t'] for r in qs.values('invoice__order__created_by').annotate(t=Sum('amount'))}


def _monthly(sites, owner, months, end):
    """{month: {'value','mt'}} for one person from the first month up to `end`."""
    rows = I.net_sales(sites, months[0], end, 'month', owner=owner)
    out = {}
    for k, v in rows.items():
        if k:
            out[(k.date() if hasattr(k, 'date') else k).replace(day=1)] = v
    return out


def line_chart(series, labels, width=460, height=250, pad=(56, 14, 12, 28)):
    """series: [{'label','values':[Decimal], 'color'}] → SVG geometry for a multi-line chart (₹ on the y axis)."""
    left, right, top, bottom = pad
    w, h = width - left - right, height - top - bottom
    top_v = max([v for s in series for v in s['values']] + [Decimal('1')])
    n = max(len(labels) - 1, 1)
    xs = [left + w * i / n for i in range(len(labels))]
    out = []
    for s in series:
        pts = [(xs[i], top + h - float(max(v, ZERO) / top_v) * h) for i, v in enumerate(s['values'])]
        out.append(dict(s, points=' '.join(f'{x:.1f},{y:.1f}' for x, y in pts), dots=[{'x': f'{x:.1f}', 'y': f'{y:.1f}', 'v': s['values'][i], 'm': labels[i]} for i, (x, y) in enumerate(pts)]))
    grid = [{'y': f'{top + h - h * f:.1f}', 'v': top_v * Decimal(str(f))} for f in (0, .25, .5, .75, 1)]
    return {'series': out, 'grid': grid, 'x': [{'x': f'{x:.1f}', 'label': labels[i]} for i, x in enumerate(xs)], 'width': width, 'height': height,
            'left': left, 'right': width - right, 'bottom': top + h}


def team(sites, period, months=6):
    """Leaderboard for the period plus a monthly trend of the top people."""
    User = get_user_model()
    sales = I.net_sales(sites, period.start, period.end, 'owner')
    inv = Invoice.objects.filter(site__in=sites, date__range=(period.start, period.end))
    counts = {r['order__created_by']: r for r in inv.values('order__created_by').annotate(n=Count('pk'), buyers=Count('partner', distinct=True))}
    coll = _collected(sites, period.start, period.end)
    tgt = defaultdict(lambda: ZERO)
    for t in _targets(sites, period.start, period.end):
        tgt[t.owner_id] += t.sales_amount
    open_orders = {r['created_by']: r['n'] for r in Order.objects.filter(kind='Sale', site__in=sites, status__in=['Submitted', 'Approved', 'Part dispatched', 'On hold'])
                   .values('created_by').annotate(n=Count('pk'))}
    ids = {k for k in sales if k} | {k for k in tgt if k}
    users = User.objects.in_bulk(ids)
    rows = []
    for uid in ids:
        u = users.get(uid)
        if u is None:
            continue
        g = sales.get(uid, {'value': ZERO, 'mt': ZERO})
        target = tgt.get(uid, ZERO)
        rows.append({'user': u, 'name': u.get_full_name() or u.get_username(), 'value': g['value'], 'mt': g['mt'],
                     'invoices': counts.get(uid, {}).get('n', 0), 'buyers': counts.get(uid, {}).get('buyers', 0), 'collected': coll.get(uid, ZERO),
                     'target': target, 'pct': I.pct(g['value'], target) if target else None, 'open_orders': open_orders.get(uid, 0),
                     'per_mt': (g['value'] / g['mt']).quantize(Decimal('1')) if g['mt'] > 0 else None})
    rows.sort(key=lambda r: (-r['value'], r['name']))
    top = max([r['value'] for r in rows] + [Decimal('1')])
    total = sum((r['value'] for r in rows), ZERO)
    for i, r in enumerate(rows):
        r['bar'] = I.pct(max(r['value'], ZERO), top) or ZERO
        r['share'] = I.pct(r['value'], total) if total else None
        r['color'] = PALETTE[i % len(PALETTE)]
        r['rank'] = i + 1
    ms = _months(period.end, months)
    series = []
    for r in rows[:6]:
        by = _monthly(sites, r['user'], ms, period.end)
        series.append({'label': r['name'], 'color': r['color'], 'values': [by.get(m, {}).get('value', ZERO) for m in ms]})
    chart = line_chart(series, [m.strftime('%b') for m in ms]) if series else None
    return {'rows': rows, 'total': total, 'mt': sum((r['mt'] for r in rows), ZERO), 'collected': sum((r['collected'] for r in rows), ZERO),
            'target': sum((r['target'] for r in rows), ZERO), 'chart': chart, 'months': ms}


def person(user, sites, period, months=12):
    """One person's numbers for the period, a 12-month chart with target, product mix and top customers."""
    from .models import Partner, Product
    g = I.net_sales(sites, period.start, period.end, 'product', owner=user)
    value = sum((v['value'] for v in g.values()), ZERO)
    mt = sum((v['mt'] for v in g.values()), ZERO)
    inv = Invoice.objects.filter(site__in=sites, order__created_by=user)
    in_period = inv.filter(date__range=(period.start, period.end))
    target = _targets(sites, period.start, period.end, user).aggregate(t=Sum('sales_amount'))['t'] or ZERO
    collected = _collected(sites, period.start, period.end, [user.pk]).get(user.pk, ZERO)
    ms = _months(period.end, months)
    by = _monthly(sites, user, ms, period.end)
    tg = defaultdict(lambda: ZERO)
    for t in _targets(sites, ms[0], period.end, user):
        tg[t.month.replace(day=1)] += t.sales_amount
    bars = [{'month': m, 'label': m.strftime('%b'), 'year': m.year, 'value': by.get(m, {}).get('value', ZERO), 'mt': by.get(m, {}).get('mt', ZERO), 'target': tg.get(m, ZERO)} for m in ms]
    top = max([max(b['value'], b['target']) for b in bars] + [Decimal('1')])
    for b in bars:
        b['h'] = I.pct(max(b['value'], ZERO), top) or ZERO
        b['t'] = I.pct(b['target'], top) if b['target'] else None
        b['hit'] = b['target'] and b['value'] >= b['target']
    products = I._ranked(g, lambda k: str(Product.objects.filter(pk=k).first() or '—'), 8)
    buyers = I.net_sales(sites, period.start, period.end, 'partner', owner=user)
    names = Partner.objects.in_bulk([k for k in buyers if k])
    customers = I._ranked(buyers, lambda k: names[k].name if k in names else '—', 8)
    return {'value': value, 'mt': mt, 'invoices': in_period.count(), 'buyers': in_period.values('partner').distinct().count(), 'collected': collected,
            'target': target, 'pct': I.pct(value, target) if target else None, 'per_mt': (value / mt).quantize(Decimal('1')) if mt > 0 else None,
            'bars': bars, 'products': products, 'customers': customers,
            'open_orders': Order.objects.filter(kind='Sale', site__in=sites, created_by=user, status__in=['Submitted', 'Approved', 'Part dispatched', 'On hold']).count(),
            'recent': in_period.select_related('partner').order_by('-date', '-pk')[:8]}
