"""Secondary sales: what dealers and distributors sell on to retailers (sell-through).

Entered by the dealer's own login (only for itself and its own retailers), by the sales team, or imported from the
monthly sheet dealers send. A retailer is either a registered Retailer under that dealer in the People network or a
plain name for shops not registered yet. Entries are never edited; a wrong one is cancelled with a reason.

Sell-through for a period compares primary sales (Sistech's invoices to the dealer, net of credit notes) with the
secondary sales the dealer reported, in MT. Primary minus secondary is the estimated stock added at the dealer.
"""
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import SecondarySale, Partner, Product, Site, AuditEvent

ZERO = Decimal('0')
DEALER_KINDS = ['Dealer', 'Distributor']


def products():
    return Product.objects.filter(active=True, category__in=['Cement', 'TMT']).order_by('category', 'name')


def retailers_of(dealer):
    return Partner.objects.filter(kind='Retailer', parent=dealer, active=True).order_by('name')


def _mt(product, qty):
    from .stock import mt_factor
    return qty * (mt_factor(product) or ZERO)


@transaction.atomic
def record(user, dealer, data, source='staff'):
    """data: date, product, quantity, retailer (Partner or None), retailer_name, rate, note."""
    if source != 'dealer' and not user.has_perm('erp.manage_secondarysale'):
        raise PermissionDenied
    today = timezone.localdate()
    problems = []
    if not dealer or dealer.kind not in DEALER_KINDS:
        problems.append('Choose the dealer or distributor who sold.')
    d = data.get('date')
    if not d or d > today or d < today - timedelta(days=120):
        problems.append('Date must be within the last 120 days and not in the future.')
    p = data.get('product')
    if not p or not products().filter(pk=p.pk).exists():
        problems.append('Choose a cement or TMT product.')
    q = data.get('quantity')
    if q is None or q <= 0 or q > 1000000:
        problems.append('Enter a quantity above 0.')
    r = data.get('retailer')
    name = (data.get('retailer_name') or '').strip()
    if r is not None and not Partner.objects.filter(pk=r.pk, kind='Retailer', parent=dealer).exists():
        problems.append(f'{r.name} is not a retailer under this dealer — link it in People first or type the shop name.')
    if r is None and not name:
        problems.append('Choose the retailer or type the shop name.')
    rate = data.get('rate')
    if rate is not None and (rate < 0 or rate > 100000):
        problems.append('Rate looks wrong.')
    if problems:
        raise ValidationError(problems)
    s = SecondarySale.objects.create(dealer=dealer, retailer=r, retailer_name='' if r else name[:120], date=d, product=p, quantity=q, rate=rate,
                                     source=source, note=(data.get('note') or '').strip()[:200], entered_by=user)
    return s


@transaction.atomic
def cancel(user, pk, reason, dealer=None):
    qs = SecondarySale.objects.select_for_update().filter(pk=pk, status='active')
    if dealer is not None:
        qs = qs.filter(dealer=dealer)
    elif not user.has_perm('erp.manage_secondarysale'):
        raise PermissionDenied
    s = qs.first()
    if s is None:
        raise ValidationError('Entry not found or already cancelled.')
    if not (reason or '').strip():
        raise ValidationError('Give the reason.')
    s.status, s.cancel_reason = 'cancelled', reason.strip()[:200]
    s.save(update_fields=['status', 'cancel_reason'])
    AuditEvent.objects.create(actor=user, action='Secondary sale cancelled', reference=f'secondary:{s.pk}', detail=f'{s.dealer.name} · {reason}'[:250])
    return s


def _parse_date(v):
    v = (v or '').strip()
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def import_csv(user, raw):
    """Columns: dealer code, retailer code or shop name, product code, quantity, date, rate (optional). All-or-nothing."""
    if not user.has_perm('erp.manage_secondarysale'):
        raise PermissionDenied
    dealers = {p.code.upper(): p for p in Partner.objects.filter(kind__in=DEALER_KINDS)}
    retailers = {p.code.upper(): p for p in Partner.objects.filter(kind='Retailer')}
    prods = {p.code.upper(): p for p in products()}
    rows, problems = [], []
    for n, row in enumerate(csv.reader(io.StringIO(raw.lstrip('﻿'))), 1):
        if not row or not row[0].strip() or row[0].strip().lower() in ('dealer', 'dealer code'):
            continue
        row = [c.strip() for c in row] + [''] * 6
        dealer, prod = dealers.get(row[0].upper()), prods.get(row[2].upper())
        try:
            qty = Decimal(row[3].replace(',', ''))
            rate = Decimal(row[5].replace(',', '')) if row[5] else None
        except InvalidOperation:
            problems.append(f'Row {n}: quantity or rate is not a number.'); continue
        if not dealer:
            problems.append(f'Row {n}: dealer code {row[0]} not found.'); continue
        if not prod:
            problems.append(f'Row {n}: product code {row[2]} not found.'); continue
        ret = retailers.get(row[1].upper())
        rows.append((n, dealer, {'retailer': ret if ret and ret.parent_id == dealer.pk else None, 'retailer_name': '' if ret and ret.parent_id == dealer.pk else row[1],
                                 'product': prod, 'quantity': qty, 'date': _parse_date(row[4]), 'rate': rate}))
    if problems:
        raise ValidationError(problems[:20])
    if not rows:
        raise ValidationError('The file has no rows.')
    with transaction.atomic():
        for n, dealer, data in rows:
            try:
                record(user, dealer, data, 'import')
            except ValidationError as e:
                raise ValidationError([f'Row {n}: {m}' for m in e.messages][:20])
    AuditEvent.objects.create(actor=user, action='Secondary sales imported', reference='secondary-import', detail=f'{len(rows)} rows')
    return len(rows)


def sell_through(start, end, dealer=None):
    """Per dealer: primary MT/value (Sistech invoices − credit notes) vs secondary MT reported, retailers served."""
    from . import insights as I
    primary = I.net_sales(list(Site.objects.all()), start, end, 'partner')
    sec = SecondarySale.objects.filter(status='active', date__range=(start, end))
    if dealer is not None:
        sec = sec.filter(dealer=dealer)
    smt, shops = defaultdict(lambda: ZERO), defaultdict(set)
    for s in sec.select_related('product'):
        smt[s.dealer_id] += _mt(s.product, s.quantity)
        shops[s.dealer_id].add(s.retailer_id or s.retailer_name.strip().lower())
    ids = set(smt) | {k for k in primary if k}
    dealers = Partner.objects.filter(pk__in=ids, kind__in=DEALER_KINDS)
    if dealer is not None:
        dealers = dealers.filter(pk=dealer.pk)
    rows = []
    for d in dealers:
        p = primary.get(d.pk, {'mt': ZERO, 'value': ZERO})
        s = smt.get(d.pk, ZERO)
        rows.append({'dealer': d, 'primary_mt': p['mt'], 'primary_value': p['value'], 'secondary_mt': s.quantize(Decimal('0.001')),
                     'through': I.pct(s, p['mt']) if p['mt'] > 0 else None, 'stock_added': (p['mt'] - s).quantize(Decimal('0.001')),
                     'retailers': len(shops.get(d.pk, ()))})
    rows.sort(key=lambda r: (-r['primary_mt'], r['dealer'].name))
    total = {'primary_mt': sum((r['primary_mt'] for r in rows), ZERO), 'secondary_mt': sum((r['secondary_mt'] for r in rows), ZERO)}
    total['through'] = I.pct(total['secondary_mt'], total['primary_mt']) if total['primary_mt'] > 0 else None
    return {'rows': rows, 'total': total}


def top_retailers(start, end, dealer=None, limit=10):
    sec = SecondarySale.objects.filter(status='active', date__range=(start, end)).select_related('product', 'retailer', 'dealer')
    if dealer is not None:
        sec = sec.filter(dealer=dealer)
    agg = defaultdict(lambda: {'mt': ZERO, 'dealer': None, 'entries': 0})
    for s in sec:
        key = (s.retailer_id, s.retailer_name.strip().lower())
        a = agg[key]
        a['mt'] += _mt(s.product, s.quantity)
        a['dealer'], a['name'], a['retailer'] = s.dealer, s.buyer, s.retailer
        a['entries'] += 1
    return sorted(agg.values(), key=lambda a: -a['mt'])[:limit]
