"""Role-based home, management dashboards, drill-down to source documents and the 'My work' notification panel."""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone

from . import complaints as CM
from . import insights as I
from .models import (Invoice, Receipt, Order, Dispatch, GoodsReceipt, Production, Complaint, Partner, Product, INDIAN_STATES, COMPLAINT_CATEGORIES)

has = lambda u, p: u.has_perm('erp.' + p)
AREAS = [
    ('executive', 'Management', 'dashboard', lambda u: has(u, 'view_executive_dashboard')),
    ('sales', 'Sales', 'sales', lambda u: has(u, 'view_invoice')),
    ('credit', 'Credit & collections', 'book', lambda u: I.company_finance(u) and has(u, 'view_receipt')),
    ('stock', 'Stock', 'boxes', lambda u: has(u, 'view_stockbalance')),
    ('dispatch', 'Dispatch', 'truck', lambda u: has(u, 'view_dispatch')),
    ('supply', 'Partner supply', 'handshake', lambda u: has(u, 'view_supplycontract') or has(u, 'view_goodsreceipt')),
    ('plant', 'Plant', 'factory', lambda u: has(u, 'view_production')),
    ('quality', 'Quality', 'flask', lambda u: has(u, 'view_qualitytest') or has(u, 'view_complaint')),
    ('finance', 'Finance', 'chart', lambda u: I.company_finance(u) and has(u, 'view_financials')),
    ('dealers', 'Dealer network', 'crm', lambda u: has(u, 'view_partner') and has(u, 'view_invoice')),
    ('schemes', 'Schemes', 'gift', lambda u: I.company_finance(u) and (has(u, 'view_schemeentitlementbatch') and has(u, 'view_schemesettlement'))),
    ('procurement', 'Procurement', 'purchase', lambda u: has(u, 'view_purchase_order')),
    ('risk', 'Audit & risk', 'shield', lambda u: has(u, 'view_auditevent')),
]
AREA_MAP = {a[0]: a for a in AREAS}


def areas_for(user):
    return [{'key': k, 'label': l, 'icon': i} for k, l, i, ok in AREAS if ok(user)]


def _sites(request):
    sites = list(I.visible_sites(request.user))
    pick = request.GET.get('site', '')
    chosen = next((s for s in sites if str(s.pk) == pick), None)
    return ([chosen] if chosen else sites), sites, chosen


def _ctx(request, title, area=None):
    period = I.parse_period(request.GET)
    sites, all_sites, chosen = _sites(request)
    from .context import AREA_MODULE
    scope = AREA_MODULE.get(area, 'insights') if area else 'insights'
    areas = [a for a in areas_for(request.user) if scope == 'insights' or AREA_MODULE.get(a['key']) == scope]
    from .team_views import can_see_team
    return period, sites, {'tab_scope': scope, 'can_team_tab': can_see_team(request.user), 'title': title, 'period': period, 'periods': I.PERIODS, 'all_sites': all_sites, 'site': chosen, 'area': area,
                           'areas': areas if scope == 'insights' or len(areas) > 1 else [], 'fin': I.company_finance(request.user),
                           'site_q': f'&site={chosen.pk}' if chosen else '', 'generated': timezone.localtime()}


def _dealer_only(u):
    from .models import DealerLedgerAccess
    return not u.is_superuser and DealerLedgerAccess.objects.filter(user=u, active=True, partner__active=True, partner__kind='Dealer').exists()


def _module_of(path):
    from django.urls import resolve, Resolver404
    from .context import ROUTE_MODULE, AREA_MODULE
    try:
        m = resolve(path.split('?')[0])
    except Resolver404:
        return None
    return AREA_MODULE.get(m.kwargs.get('area')) if m.url_name == 'insight_area' else ROUTE_MODULE.get(m.url_name)


@login_required
def launcher(request):
    """Home after sign-in: one tile per module the user may open, each with the work waiting in it. A user with a single
    module goes straight into it."""
    u = request.user
    if _dealer_only(u):
        return redirect('dealer_dashboard')
    from .context import navigation
    modules = navigation(request)['nav_modules']
    from .context import COMMON_ROUTES                    # everyone has these; they don't make a module "theirs"
    working = [m for m in modules if any(i['route'] not in COMMON_ROUTES for sec in m['sections'] for i in sec['items'])]
    if len(working) == 1:
        return redirect(working[0]['first'])
    queue = I.my_queue(u)
    counts = {}
    for item in queue:
        k = _module_of(item['url'])
        counts[k] = counts.get(k, 0) + item['count']
    for m in modules:
        m['pending'] = counts.get(m['key'], 0)
        m['pages'] = [i for sec in m['sections'] for i in sec['items'] if not i.get('area')][:4]
        m['count'] = sum(len(sec['items']) for sec in m['sections'])
    hour = timezone.localtime().hour
    return render(request, 'erp/launcher.html', {
        'title': 'Home', 'modules': modules, 'queue': queue[:6], 'queue_total': sum(i['count'] for i in queue), 'queue_more': max(len(queue) - 6, 0),
        'greeting': 'Good morning' if hour < 12 else 'Good afternoon' if hour < 17 else 'Good evening', 'today': timezone.localdate()})


@login_required
def module_home(request, key):
    """Open a module at its first page (its dashboard when the user may see it)."""
    from .context import navigation
    m = next((m for m in navigation(request)['nav_modules'] if m['key'] == key), None)
    if m is None:
        raise Http404
    request.session['erp_module'] = key
    return redirect(m['first'])


@login_required
def home(request):
    """Overview dashboard: what is waiting for me, then the headline numbers of every dashboard I may open."""
    u = request.user
    if _dealer_only(u):
        return redirect('dealer_dashboard')
    period, sites, ctx = _ctx(request, 'Overview')
    ctx['queue'] = I.my_queue(u, sites)
    ok = {a['key'] for a in ctx['areas']}
    if 'sales' in ok or 'executive' in ok:
        ctx['sales'] = I.sales_summary(sites, period)
        ctx['book'] = I.order_book(sites)
    if ctx['fin'] and ('credit' in ok or 'executive' in ok):
        ctx['credit'] = I.receivables(period)
    if 'stock' in ok:
        ctx['stock'] = I.stock_summary(sites)
    if 'dispatch' in ok:
        ctx['dispatch'] = I.dispatch_summary(sites, period)
    if 'supply' in ok:
        ctx['supply'] = I.sourcing_summary(sites, period)
    if 'quality' in ok:
        ctx['quality'] = I.quality_summary(sites, period, user=request.user)
    if has(u, 'create_sales_order') or has(u, 'add_quotation') or has(u, 'add_crmlead'):
        ctx['mine'] = I.my_sales(u, sites, period)
    if 'sales' in ok or 'executive' in ok:
        ctx['trend'] = I.trend(sites, 6, finance=ctx['fin'] and ('credit' in ok or 'executive' in ok))
    hour = timezone.localtime().hour
    ctx['greeting'] = 'Good morning' if hour < 12 else 'Good afternoon' if hour < 17 else 'Good evening'
    ctx['today'] = timezone.localdate()
    return render(request, 'erp/home.html', ctx)


@login_required
def dashboard_area(request, area):
    if area not in AREA_MAP:
        raise Http404
    key, label, icon, ok = AREA_MAP[area]
    if not ok(request.user):
        raise PermissionDenied
    period, sites, ctx = _ctx(request, f'{label} dashboard', area)
    ctx['label'] = label
    fin = ctx['fin']
    if area == 'executive':
        ctx['sales'] = I.sales_summary(sites, period, ctx['site_q'])
        ctx['book'] = I.order_book(sites)
        ctx['trend'] = I.trend(sites, 12, finance=fin)
        ctx['stock'] = I.stock_summary(sites)
        ctx['dispatch'] = I.dispatch_summary(sites, period)
        ctx['supply'] = I.sourcing_summary(sites, period)
        ctx['quality'] = I.quality_summary(sites, period, ctx['sales']['mt'], user=request.user)
        if fin:
            ctx['credit'] = I.receivables(period)
            if has(request.user, 'view_financials'):
                ctx['finance'] = I.finance_summary(period)
    elif area == 'sales':
        ctx['sales'] = I.sales_summary(sites, period, ctx['site_q'])
        ctx['book'] = I.order_book(sites)
        ctx['quotes'] = I.quote_funnel(sites, period)
        ctx['crm'] = I.crm_pipeline(sites)
        ctx['trend'] = I.trend(sites, 12, finance=False)
    elif area == 'credit':
        ctx['credit'] = I.receivables(period)
        from .collect import worklist
        ctx['collect'] = worklist()
        ctx['trend'] = I.trend(sites, 12, finance=True)
    elif area == 'stock':
        ctx['stock'] = I.stock_summary(sites)
    elif area == 'dispatch':
        ctx['dispatch'] = I.dispatch_summary(sites, period)
    elif area == 'supply':
        ctx['supply'] = I.sourcing_summary(sites, period)
    elif area == 'plant':
        ctx['plant'] = I.plant_summary(sites, period)
        from .maintenance import kpis
        ctx['maint'] = kpis(sites)
    elif area == 'quality':
        sold = I._total(I.net_sales(sites, period.start, period.end))['mt']
        ctx['quality'] = I.quality_summary(sites, period, sold, user=request.user)
    elif area == 'finance':
        ctx['finance'] = I.finance_summary(period)
    elif area == 'dealers':
        ctx['dealers'] = I.dealer_network(sites, period)
        if fin:
            ctx['credit'] = I.receivables(period)
    elif area == 'schemes':
        ctx['schemes'] = I.scheme_summary(period, I._total(I.net_sales(sites, period.start, period.end))['mt'])
    elif area == 'procurement':
        ctx['proc'] = I.procurement_summary(sites, period)
        ctx['supply'] = I.sourcing_summary(sites, period)
    elif area == 'risk':
        ctx['risk'] = I.risk_summary(sites, period)
    return render(request, 'erp/insight_area.html', ctx)


@login_required
def definitions(request):
    if not areas_for(request.user):
        raise PermissionDenied
    return render(request, 'erp/insight_definitions.html', {'title': 'KPI definitions', 'rows': I.DEFINITIONS})


# ---------- Drill-down ----------

DRILLS = {
    'sales': ('view_invoice', 'Sales invoices'),
    'collections': ('view_receipt', 'Collections'),
    'overdue': ('view_receipt', 'Open invoices'),
    'orders': ('view_sales_order', 'Open sales orders'),
    'dispatches': ('view_dispatch', 'Truck trips'),
    'supply': ('view_goodsreceipt', 'Partner receipts (GRN)'),
    'production': ('view_production', 'Production batches'),
    'complaints': ('view_complaint', 'Complaints'),
}


@login_required
def drill(request, kind):
    """The documents behind a dashboard number, with the same period, site and filter, each linking to its source document."""
    if kind not in DRILLS:
        raise Http404
    perm, title = DRILLS[kind]
    u, G = request.user, request.GET
    if not has(u, perm) or (kind in ('collections', 'overdue') and not I.company_finance(u)):
        raise PermissionDenied
    period, sites, ctx = _ctx(request, title)
    filters, cols, rows, total = [], [], [], Decimal('0')
    today = timezone.localdate()

    def pick(model, key):
        v = G.get(key, '')
        return model.objects.filter(pk=v).first() if v.isdigit() else None
    if kind == 'sales':
        qs = Invoice.objects.filter(site__in=sites, date__range=(period.start, period.end)).select_related('partner', 'site', 'order__created_by')
        if (p := pick(Product, 'product')): qs = qs.filter(lines__product=p).distinct(); filters.append(p.name)
        if (p := pick(Partner, 'partner')): qs = qs.filter(partner=p); filters.append(p.name)
        if 'state' in G: qs = qs.filter(place_of_supply=G['state']); filters.append(dict(INDIAN_STATES).get(G['state'], 'State not set'))
        if 'district' in G: qs = qs.filter(partner__district=G['district']); filters.append(G['district'] or 'District not set')
        if G.get('owner', '').isdigit(): qs = qs.filter(order__created_by_id=G['owner']); filters.append('Sales person')
        cols = ['Invoice', 'Date', 'Customer', 'Plant', 'Sales person', 'Taxable', 'Invoice total']
        for i in qs[:500]:
            rows.append({'url': reverse('invoice_detail', args=[i.pk]), 'cells': [i.number, i.date, i.partner.name, i.site.code, (i.order.created_by.get_full_name() or i.order.created_by.username) if i.order_id else '', ('₹', i.taxable), ('₹', i.grand_total)]})
            total += i.taxable
    elif kind == 'collections':
        qs = Receipt.objects.filter(date__range=(period.start, period.end)).select_related('partner')
        cols = ['Receipt', 'Date', 'From', 'Mode', 'Status', 'Amount']
        for r in qs[:500]:
            rows.append({'url': reverse('receipt_detail', args=[r.pk]), 'cells': [r.number, r.date, r.partner.name, r.get_mode_display(), r.get_status_display(), ('₹', r.amount)]})
            total += r.amount if r.status == 'posted' else 0
    elif kind == 'overdue':
        qs = I._open_invoices().select_related('partner', 'site').order_by('due_date')
        if (p := pick(Partner, 'partner')): qs = qs.filter(partner=p); filters.append(p.name)
        b = G.get('bucket', '')
        span = {'current': (None, 0), 'd30': (1, 30), 'd60': (31, 60), 'd90': (61, 90), 'd90p': (91, None)}.get(b)
        if span:
            lo, hi = span
            from datetime import timedelta
            if lo is None: qs = qs.filter(due_date__gte=today)
            else:
                qs = qs.filter(due_date__lte=today - timedelta(days=lo))
                if hi: qs = qs.filter(due_date__gte=today - timedelta(days=hi))
            filters.append(dict((('current', 'Not due'), ('d30', '1–30 days'), ('d60', '31–60 days'), ('d90', '61–90 days'), ('d90p', '90+ days')))[b])
        elif not G.get('partner'):
            qs = qs.filter(due_date__lt=today); filters.append('Past due date')
        cols = ['Invoice', 'Customer', 'Due date', 'Days late', 'Invoice total', 'Outstanding']
        for i in qs[:500]:
            rows.append({'url': reverse('partner_ledger', args=[i.partner_id]), 'cells': [i.number, i.partner.name, i.due_date, max((today - i.due_date).days, 0), ('₹', i.grand_total), ('₹', i.due)]})
            total += i.due
    elif kind == 'orders':
        qs = Order.objects.filter(kind='Sale', status__in=['Approved', 'Part dispatched', 'On hold'], site__in=sites).select_related('partner', 'site').prefetch_related('lines').order_by('created_at')
        cols = ['Order', 'Customer', 'Plant', 'Ordered on', 'Status', 'Value']
        for o in qs[:500]:
            rows.append({'url': reverse('order_detail', args=[o.pk]), 'cells': [o.number, o.partner.name, o.site.code, timezone.localtime(o.created_at).date(), o.status, ('₹', o.total)]})
            total += o.total
    elif kind == 'dispatches':
        qs = Dispatch.objects.filter(site__in=sites).select_related('order__partner', 'vehicle', 'site')
        state = G.get('state', 'out')
        if state == 'transit': qs = qs.filter(status='gate_out').order_by('gate_out_at'); filters.append('In transit · POD pending')
        elif state == 'yard': qs = qs.exclude(status__in=['gate_out', 'delivered', 'cancelled']); filters.append('Inside the plant')
        elif state == 'claims': qs = qs.filter(delivered_at__date__range=(period.start, period.end)).filter(Q(shortage_qty__gt=0) | Q(damaged_qty__gt=0)); filters.append('Shortage / damage at delivery')
        else: qs = qs.filter(gate_out_at__date__range=(period.start, period.end)); filters.append('Left the gate in the period')
        cols = ['Trip', 'Customer', 'Truck', 'Plant', 'Gate-out', 'Status']
        for d in qs[:500]:
            rows.append({'url': reverse('dispatch_detail', args=[d.pk]), 'cells': [d.number, d.order.partner.name, d.vehicle.registration, d.site.code,
                                                                                  timezone.localtime(d.gate_out_at).strftime('%d %b %H:%M') if d.gate_out_at else '—', d.get_status_display()]})
    elif kind == 'supply':
        qs = GoodsReceipt.objects.filter(status='posted', posted_at__date__range=(period.start, period.end), site__in=sites, supplier__manufacturing_partner=True).select_related('supplier', 'site')
        if (p := pick(Partner, 'partner')): qs = qs.filter(supplier=p); filters.append(p.name)
        cols = ['GRN', 'Partner', 'Plant', 'Vehicle', 'Challan', 'Posted']
        for g in qs[:500]:
            rows.append({'url': reverse('grn_detail', args=[g.pk]), 'cells': [g.number, g.supplier.name, g.site.code, g.vehicle_number, g.challan_number, timezone.localtime(g.posted_at).date()]})
    elif kind == 'production':
        qs = Production.objects.filter(site__in=sites, completed_at__date__range=(period.start, period.end)).select_related('product', 'site')
        cols = ['Batch', 'Product', 'Plant', 'Output', 'Status']
        for b in qs[:500]:
            rows.append({'url': reverse('production_detail', args=[b.pk]), 'cells': [b.batch, b.product.name, b.site.code, b.actual_quantity, b.status]})
    elif kind == 'complaints':
        qs = CM.scope(Complaint.objects.all(), request.user).filter(Q(invoice__site__in=sites) | Q(invoice__isnull=True), received_on__range=(period.start, period.end)).select_related('partner')
        if G.get('category'): qs = qs.filter(category=G['category']); filters.append(dict(COMPLAINT_CATEGORIES).get(G['category'], ''))
        cols = ['Complaint', 'Customer', 'Received', 'Resolve by', 'Status']
        for c in qs[:500]:
            rows.append({'url': reverse('complaint_detail', args=[c.pk]), 'cells': [c.number, c.partner.name, c.received_on, c.due_on, c.get_status_display()]})
    if G.get('export') == 'csv':
        import csv
        from django.http import HttpResponse
        from .models import AuditEvent
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="{kind}-{period.start}-{period.end}.csv"'
        resp.write('\ufeff')
        w = csv.writer(resp)
        safe = lambda v: ("'" + v) if isinstance(v, str) and v[:1] in ('=', '+', '-', '@', '\t', '\r') else v
        w.writerow([title + (f' · {period.label}' if kind not in ('overdue', 'orders') else ' · as of today'), f'Plants: {chosen_label(ctx)}', 'Filters: ' + (', '.join(filters) or 'none'),
                    f'Generated {timezone.localtime():%d-%m-%Y %H:%M} IST by {u.get_username()}'])
        w.writerow(cols)
        for r in rows:
            w.writerow([safe(c[1] if isinstance(c, tuple) else c) for c in r['cells']])
        AuditEvent.objects.create(actor=u, action='Dashboard export', reference=kind, detail=f'{len(rows)} rows · {period.start} – {period.end} · {", ".join(filters)}'[:250])
        return resp
    ctx.update(kind=kind, cols=cols, rows=rows, filters=filters, export=request.get_full_path() + ('&' if '?' in request.get_full_path() else '?') + 'export=csv', total=total if kind in ('sales', 'collections', 'overdue', 'orders') else None,
               dated=kind not in ('overdue', 'orders') and not (kind == 'dispatches' and G.get('state') in ('transit', 'yard')), capped=len(rows) >= 500)
    return render(request, 'erp/insight_drill.html', ctx)


def chosen_label(ctx):
    return ctx['site'].code if ctx.get('site') else 'all permitted'


# ---------- My work (notification bell) ----------

def queue_for(user):
    key = f'myq:{user.pk}'
    items = cache.get(key)
    if items is None:
        items = I.my_queue(user)
        cache.set(key, items, 60)
    return items


@login_required
def my_work(request):
    items = queue_for(request.user)
    if request.GET.get('format') == 'json':
        return JsonResponse({'count': sum(i['count'] for i in items), 'items': items})
    return render(request, 'erp/my_work.html', {'title': 'My work', 'items': I.my_queue(request.user)})


# ---------- Excel report packs ----------

@login_required
def report_packs(request):
    """Download a pack now, or subscribe to receive it by email (built with your own access at send time)."""
    from . import report_packs as RP
    from .models import ReportSubscription, REPORT_PACKS, REPORT_FREQUENCIES
    u = request.user
    packs = [(k, l) for k, l in REPORT_PACKS if RP.allowed(u, k)]
    if not packs:
        raise PermissionDenied
    if request.method == 'POST':
        from django.contrib import messages
        pack, freq = request.POST.get('pack'), request.POST.get('frequency')
        if pack in dict(packs) and freq in dict(REPORT_FREQUENCIES):
            if request.POST.get('remove') == '1':
                ReportSubscription.objects.filter(user=u, pack=pack).delete()
                messages.success(request, 'Subscription removed.')
            else:
                ReportSubscription.objects.update_or_create(user=u, pack=pack, defaults={'frequency': freq, 'active': True})
                messages.success(request, f'{dict(packs)[pack]} will be emailed {dict(REPORT_FREQUENCIES)[freq].split(" (")[0].lower()} to {u.email or "your email (add it in My account)"}.')
        return redirect('report_packs')
    if request.GET.get('download') in dict(packs):
        from django.http import HttpResponse
        from .models import AuditEvent
        period = I.parse_period(request.GET)
        fmt = 'pdf' if request.GET.get('format') == 'pdf' else 'xlsx'
        name, data = RP.build(u, request.GET['download'], period, fmt)
        AuditEvent.objects.create(actor=u, action='Report pack downloaded', reference=request.GET['download'], detail=f'{fmt} · {period.start} – {period.end}')
        resp = HttpResponse(data, content_type='application/pdf' if fmt == 'pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'{"inline" if fmt == "pdf" else "attachment"}; filename="{name}"'
        return resp
    subs = {s.pack: s for s in ReportSubscription.objects.filter(user=u)}
    return render(request, 'erp/report_packs.html', {'title': 'Report packs', 'packs': [(k, l, subs.get(k)) for k, l in packs], 'frequencies': REPORT_FREQUENCIES,
                                                     'periods': I.PERIODS, 'period': I.parse_period(request.GET), 'areas': areas_for(u), 'tab_scope': 'insights'})
