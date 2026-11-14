"""GST maths, Indian financial years, document numbering and price-list lookup (shared by orders, invoices and credit notes)."""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

PAISE = Decimal('0.01')


def money(value):
    return Decimal(value or 0).quantize(PAISE, rounding=ROUND_HALF_UP)


def line_tax(taxable, gst_rate, interstate):
    """→ (cgst, sgst, igst) for one line. Intra-state splits the rate half and half."""
    taxable, rate = money(taxable), Decimal(gst_rate or 0)
    if interstate:
        return Decimal('0.00'), Decimal('0.00'), money(taxable * rate / 100)
    half = money(taxable * rate / 200)
    return half, half, Decimal('0.00')


def summarise(lines, interstate):
    """Totals for order/invoice lines (objects with quantity, rate, gst_rate, hsn). Grand total is rounded to the rupee."""
    out = {'taxable': Decimal('0.00'), 'cgst': Decimal('0.00'), 'sgst': Decimal('0.00'), 'igst': Decimal('0.00'), 'rows': [], 'hsn': {}, 'interstate': interstate}
    for line in lines:
        taxable = money(line.quantity * line.rate)
        cgst, sgst, igst = line_tax(taxable, line.gst_rate, interstate)
        out['rows'].append({'line': line, 'taxable': taxable, 'cgst': cgst, 'sgst': sgst, 'igst': igst, 'total': taxable + cgst + sgst + igst})
        out['taxable'] += taxable; out['cgst'] += cgst; out['sgst'] += sgst; out['igst'] += igst
        key = (line.hsn or '—', Decimal(line.gst_rate or 0))
        h = out['hsn'].setdefault(key, {'hsn': key[0], 'rate': key[1], 'taxable': Decimal('0.00'), 'cgst': Decimal('0.00'), 'sgst': Decimal('0.00'), 'igst': Decimal('0.00')})
        h['taxable'] += taxable; h['cgst'] += cgst; h['sgst'] += sgst; h['igst'] += igst
    out['tax'] = out['cgst'] + out['sgst'] + out['igst']
    exact = out['taxable'] + out['tax']
    out['grand_total'] = exact.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    out['round_off'] = out['grand_total'] - exact
    out['hsn'] = sorted(out['hsn'].values(), key=lambda h: (h['hsn'], h['rate']))
    return out


# ---------- Financial year & numbering ----------

def fy_start_month():
    from .models import Organization
    return Organization.objects.filter(pk=1).values_list('fy_start_month', flat=True).first() or 4


def financial_year(on=None):
    """date → '2026-27' (April–March by default; follows Organisation setup)."""
    on = on or timezone.localdate()
    start = fy_start_month()
    first = on.year if on.month >= start else on.year - 1
    return f'{first}-{str(first + 1)[2:]}'


def default_prefix(doc_type, site):
    code = ''.join(ch for ch in (site.code if site else 'HO').upper() if ch.isalnum())
    if doc_type == 'INV':
        return code[:4]  # PREF/26-27/00001 = 16 characters, GST's maximum
    if doc_type == 'CN':
        return 'CN' + code[:2]  # credit notes follow the same 16-character rule
    return f'{doc_type}{code[:5]}'


@transaction.atomic
def next_number(doc_type, site=None, on=None):
    """Allocate the next number. Row-locked so two users never get the same number."""
    from .models import NumberSeries
    fy = financial_year(on)
    series, _ = NumberSeries.objects.get_or_create(doc_type=doc_type, site=site, fy=fy, defaults={'prefix': default_prefix(doc_type, site)})
    series = NumberSeries.objects.select_for_update().get(pk=series.pk)
    number = series.format(series.next_number)
    series.next_number += 1
    series.save(update_fields=['next_number'])
    return number


# ---------- Price list ----------

def price_for(product, site=None, partner=None, quantity=0, on=None):
    """Best current PriceRule for a sale, or None. Specific dealer > dealer type > district > plant > standard; highest slab reached; newest first."""
    from .models import PriceRule
    from .models import clean_place
    on = on or timezone.localdate()
    qs = PriceRule.objects.filter(product=product, active=True, valid_from__lte=on).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=on))
    qs = qs.filter(Q(site__isnull=True) | Q(site=site)) if site else qs.filter(site__isnull=True)
    if partner:
        qs = qs.filter(Q(partner__isnull=True) | Q(partner=partner)).filter(Q(partner_kind='') | Q(partner_kind=partner.kind))
        district = clean_place(partner.district)
        qs = qs.filter(Q(district='') | Q(district__iexact=district)) if district else qs.filter(district='')
    else:
        qs = qs.filter(partner__isnull=True, partner_kind='', district='')
    qs = qs.filter(min_quantity__lte=quantity or 0)
    rules = sorted(qs.select_related('site', 'partner'), key=lambda r: (r.specificity, r.min_quantity, r.valid_from, r.pk), reverse=True)
    return rules[0] if rules else None


# ---------- Amount in words (Indian system) ----------

ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen',
        'Fifteen', 'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _two(n):
    return ONES[n] if n < 20 else (TENS[n // 10] + (' ' + ONES[n % 10] if n % 10 else ''))


def _three(n):
    h, rest = divmod(n, 100)
    return ' '.join(p for p in [f'{ONES[h]} Hundred' if h else '', _two(rest) if rest else ''] if p)


def in_words(amount):
    """1234567.50 → 'Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Fifty Paise Only'."""
    amount = money(amount)
    rupees, paise = int(amount), int((amount - int(amount)) * 100)
    if rupees == 0 and paise == 0:
        return 'Rupees Zero Only'
    parts, n = [], rupees
    crore, n = divmod(n, 10 ** 7)
    lakh, n = divmod(n, 10 ** 5)
    thousand, n = divmod(n, 1000)
    if crore: parts.append((in_words(crore)[7:-5] if crore >= 1000 else _three(crore) if crore >= 100 else _two(crore)) + ' Crore')
    if lakh: parts.append(_two(lakh) + ' Lakh')
    if thousand: parts.append(_two(thousand) + ' Thousand')
    if n: parts.append(_three(n))
    text = 'Rupees ' + ' '.join(parts) if parts else 'Rupees Zero'
    if paise:
        text += f' and {_two(paise)} Paise'
    return text + ' Only'
