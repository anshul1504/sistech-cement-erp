"""Indian number formatting for templates: {{ value|indian }} → 12,34,567 and {{ value|indian:2 }} → 12,34,567.50."""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def indian(value, decimals=0):
    if value in (None, ''):
        return ''
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return value
    decimals = int(decimals)
    sign = '-' if number < 0 else ''
    text = f'{abs(number):.{decimals}f}'
    whole, _, fraction = text.partition('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:]); head = head[:-2]
        if head: groups.insert(0, head)
        whole = ','.join(groups + [tail])
    return sign + whole + ('.' + fraction if fraction else '')


@register.filter
def qty(value):
    """Quantity without trailing zeros, Indian grouping: 2400.000 → 2,400 ; 12.500 → 12.5."""
    if value in (None, ''):
        return ''
    try:
        number = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return value
    places = max(-number.as_tuple().exponent, 0)
    return indian(number, min(places, 3))


@register.filter
def cell(value):
    """Uniform table-cell formatting: dates, numbers and money (tagged as ('₹', amount))."""
    import datetime
    from django.utils import timezone
    from django.utils.formats import date_format
    if value is None or value == '':
        return '—'
    if isinstance(value, tuple) and len(value) == 2 and value[0] == '₹':
        return '₹' + indian(value[1], 2)
    if isinstance(value, datetime.datetime):
        return date_format(timezone.localtime(value) if timezone.is_aware(value) else value, 'd M Y, H:i')
    if isinstance(value, datetime.date):
        return date_format(value, 'd M Y')
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (Decimal, int, float)):
        return qty(value)
    return value


@register.filter
def bags_mt(value):
    """50 kg cement bags → metric tonnes: 400 → 20."""
    try:
        return qty(Decimal(str(value or 0)) * Decimal('0.05'))
    except (InvalidOperation, ValueError):
        return ''


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key, '')
    except AttributeError:
        return ''


@register.filter
def plus(value, arg):
    """Exact decimal addition (Django's |add truncates decimals to whole numbers)."""
    try:
        return Decimal(str(value or 0)) + Decimal(str(arg or 0))
    except (InvalidOperation, ValueError):
        return value
