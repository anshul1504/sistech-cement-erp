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
