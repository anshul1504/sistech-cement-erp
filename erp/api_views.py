"""JSON API v1 for the Android apps. Same services, permissions and plant scope as the web screens.

Conventions: every response is {"ok": true, ...} or {"ok": false, "error": "..."}; money and quantities are strings
(exact decimals — the app parses them as BigDecimal); dates are ISO yyyy-mm-dd. A dealer login only ever sees its own
linked account; a staff login sees what its roles and plants allow.
"""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from accounts.api_auth import api, body, error
from accounts.roles import order_permission
from . import insights as I

ZERO = Decimal('0')


def s(v):
    """Decimal → string (None stays None)."""
    return None if v is None else str(v)


def ok(**data):
    return JsonResponse({'ok': True, **data})


def _dealer(user):
    from .models import DealerLedgerAccess
    a = DealerLedgerAccess.objects.select_related('partner').filter(user=user, active=True, partner__active=True, partner__kind='Dealer').first()
    return a.partner if a else None


def is_dealer(user):
    return _dealer(user) is not None


def is_sales(user):
    return order_permission(user, 'Sale', 'create') or user.has_perm('erp.view_sales_order')


def _date(v):
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def _dec(v):
    try:
        return Decimal(str(v).replace(',', '').strip()) if v not in (None, '') else None
    except InvalidOperation:
        return None


# ---------- Who am I ----------

@api()
def me(request):
    u = request.user
    dealer = _dealer(u)
    apps = []
    if dealer:
        apps.append('dealer')
    if is_sales(u):
        apps.append('sales')
    return ok(user={'id': u.pk, 'username': u.get_username(), 'name': u.get_full_name() or u.get_username(), 'email': u.email},
              roles=list(u.groups.values_list('name', flat=True)), apps=apps,
              dealer={'id': dealer.pk, 'name': dealer.name, 'code': dealer.code} if dealer else None,
              can={'create_order': order_permission(u, 'Sale', 'create'), 'record_secondary': u.has_perm('erp.manage_secondarysale')})


# ---------- Dealer app ----------

@api(perm=is_dealer)
def dealer_summary(request):
    from .ledger import balance, open_invoices, ageing
    from .dealer_orders import credit_room
    p = _dealer(request.user)
    invs = open_invoices(p)
    age = ageing(invs)
    c = credit_room(p)
    today = timezone.localdate()
    from .secondary import sell_through
    st = sell_through(today.replace(day=1), today, p)['rows']
    return ok(dealer={'name': p.name, 'code': p.code, 'city': p.city}, balance=s(balance(p)), overdue=s(age['overdue']), not_due=s(age['current']),
              credit={'limit': s(c['limit']), 'available': s(c['available']), 'used': s(c['exposure'])},
              month={'bought_mt': s(st[0]['primary_mt']) if st else '0', 'sold_mt': s(st[0]['secondary_mt']) if st else '0'},
              open_invoices=len(invs))


@api(perm=is_dealer)
def dealer_invoices(request):
    from .models import Invoice
    from .ledger import open_invoices
    p = _dealer(request.user)
    due = {i.pk: i.outstanding for i in open_invoices(p)}
    rows = [{'id': i.pk, 'number': i.number, 'date': i.date.isoformat(), 'due_date': i.due_date.isoformat() if i.due_date else None,
             'total': s(i.grand_total), 'unpaid': s(due.get(i.pk, ZERO))} for i in Invoice.objects.filter(partner=p).order_by('-date', '-pk')[:100]]
    return ok(invoices=rows)


@api(perm=is_dealer)
def dealer_ledger(request):
    from .ledger import ledger_rows
    p = _dealer(request.user)
    start, end = _date(request.GET.get('from')), _date(request.GET.get('to'))
    if start and end and start > end:
        return error('From date must be on or before To date.')
    opening, rows = ledger_rows(p, start, end)
    return ok(opening=s(opening), closing=s(rows[-1]['balance'] if rows else opening),
              rows=[{'date': r['date'].isoformat(), 'type': r['kind'], 'ref': r['ref'], 'note': r['note'], 'debit': s(r['debit']), 'credit': s(r['credit']),
                     'balance': s(r['balance'])} for r in rows[-300:]])


@api(perm=is_dealer)
def dealer_products(request):
    from . import dealer_orders as DO
    p = _dealer(request.user)
    return ok(products=[{'id': x.pk, 'name': x.name, 'code': x.code, 'unit': x.unit, 'gst_rate': s(x.gst_rate), 'rate': s(DO.indicative(p, x))} for x in DO.products()])


def _request_json(r):
    return {'id': r.pk, 'number': r.number, 'status': r.status, 'status_label': r.get_status_display(), 'created': r.created_at.isoformat(),
            'required_by': r.required_by.isoformat(), 'estimate': s(r.estimate), 'reason': r.reason,
            'order': {'number': r.order.number, 'status': r.order.status} if r.order_id else None,
            'lines': [{'product': l.product.name, 'quantity': s(l.quantity), 'unit': l.product.unit, 'rate': s(l.indicative_rate)} for l in r.lines.all()]}


@api(methods=('GET', 'POST'), perm=is_dealer)
def dealer_orders(request):
    from . import dealer_orders as DO
    from .models import DealerOrderRequest, Product
    p = _dealer(request.user)
    if request.method == 'POST':
        data = body(request)
        prods = {x.pk: x for x in DO.products()}
        lines = []
        for l in data.get('lines') or []:
            pid, q = l.get('product'), _dec(l.get('quantity'))
            if pid not in prods or q is None:
                return error('Each line needs a product and a valid quantity.')
            lines.append((prods[pid], q))
        try:
            req = DO.submit(request.user, p, _date(data.get('required_by')), lines, data.get('ship_to', ''), data.get('note', ''))
        except ValidationError as e:
            return error(' '.join(e.messages))
        return ok(request=_request_json(req))
    qs = DealerOrderRequest.objects.filter(partner=p).select_related('order').prefetch_related('lines__product')[:50]
    return ok(requests=[_request_json(r) for r in qs])


@api(methods=('POST',), perm=is_dealer)
def dealer_order_cancel(request, pk):
    from . import dealer_orders as DO
    try:
        DO.cancel(request.user, _dealer(request.user), pk)
    except ValidationError as e:
        return error(' '.join(e.messages))
    return ok()


@api(methods=('GET', 'POST'), perm=is_dealer)
def dealer_secondary(request):
    from . import secondary as S
    from .models import SecondarySale, Partner
    p = _dealer(request.user)
    if request.method == 'POST':
        data = body(request)
        retailer = Partner.objects.filter(pk=data.get('retailer') or 0, kind='Retailer').first()
        try:
            x = S.record(request.user, p, {'date': _date(data.get('date')) or timezone.localdate(), 'product': S.products().filter(pk=data.get('product') or 0).first(),
                                           'quantity': _dec(data.get('quantity')), 'retailer': retailer, 'retailer_name': data.get('retailer_name', ''),
                                           'rate': _dec(data.get('rate')), 'note': data.get('note', '')}, 'dealer')
        except ValidationError as e:
            return error(' '.join(e.messages))
        return ok(id=x.pk)
    return ok(retailers=[{'id': r.pk, 'name': r.name, 'city': r.city} for r in S.retailers_of(p)],
              entries=[{'id': e.pk, 'date': e.date.isoformat(), 'buyer': e.buyer, 'product': e.product.name, 'quantity': s(e.quantity), 'unit': e.product.unit,
                        'status': e.status} for e in SecondarySale.objects.filter(dealer=p).select_related('retailer', 'product')[:50]])


# ---------- Sales app ----------

@api(perm=is_sales)
def sales_summary(request):
    u = request.user
    sites = list(I.visible_sites(u))
    period = I.parse_period({})
    m = I.my_sales(u, sites, period)
    queue = I.my_queue(u, sites)
    return ok(month={'value': s(m['month']), 'target': s(m['target']), 'target_pct': s(m['target_pct']), 'mt': s(m['mt'])},
              open_orders=m['orders'], quotes=m['quotes'], leads={'open': m['pipeline']['open'], 'follow_ups': m['pipeline']['follow_ups']},
              work=[{'label': q['label'], 'count': q['count'], 'hint': q['hint']} for q in queue])


@api(perm=is_sales)
def sales_customers(request):
    from .models import Partner, CUSTOMER_KINDS
    from .partner_views import credit_position
    q = (request.GET.get('q') or '').strip()
    qs = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(city__icontains=q) | Q(phone__icontains=q))
    out = []
    for p in qs.order_by('name')[:40]:
        c = credit_position(p)
        out.append({'id': p.pk, 'name': p.name, 'code': p.code, 'kind': p.kind, 'city': p.city, 'phone': p.phone,
                    'credit': {'limit': s(c['limit']), 'available': s(c['available']), 'overdue': s(c['overdue'])}})
    return ok(customers=out)


@api(perm=is_sales)
def sales_orders(request):
    from .models import Order
    from .services import scope
    qs = scope(Order.objects.filter(kind='Sale'), request.user).select_related('partner', 'site').prefetch_related('lines')
    if request.GET.get('mine') != '0':
        qs = qs.filter(created_by=request.user)
    return ok(orders=[{'id': o.pk, 'number': o.number, 'customer': o.partner.name, 'site': o.site.code, 'status': o.status, 'due_date': o.due_date.isoformat(),
                       'total': s(o.total)} for o in qs.order_by('-created_at')[:50]])


@api(perm=lambda u: order_permission(u, 'Sale', 'create'))
def sales_dealer_requests(request):
    from . import dealer_orders as DO
    qs = DO.queue().filter(status='submitted').prefetch_related('lines__product')[:50]
    return ok(requests=[dict(_request_json(r), dealer=r.partner.name) for r in qs])


@api()
def modules(request):
    from .context import modules_for
    return ok(modules=modules_for(request.user))


@api()
def work(request):
    """Everything waiting for this user (same list as the web bell / My work); each item opens its web page."""
    items = I.my_queue(request.user)
    return ok(total=sum(i['count'] for i in items),
              items=[{'label': i['label'], 'count': i['count'], 'hint': i['hint'], 'tone': i.get('tone', 'warn'), 'path': i['url']} for i in items])
