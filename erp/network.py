"""Channel network: who sits above and below whom.

Levels (Sistech's order):
  1. C&F                                   – top, works directly with the company
  2. Sales promoter / handling agent / transporter – under a C&F
  3. Dealer (and distributor)              – under a C&F, sales promoter or handling agent
  4. Retailer (and project buyer)          – under a dealer or distributor
  5. Mason / contractor                    – linked to a dealer or retailer (Influencer.primary_dealer / dealers)

The upline is optional: a dealer may deal with the company directly. A partner's upline must sit on a higher level, so the
network can never loop. Changes go through set_parent only (permission, level rule, active check, audit trail).
"""
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q

from .models import Partner, Influencer, AuditEvent

ZERO = Decimal('0')

LEVELS = [
    ('cf', 'C&F', ['CF']),
    ('agent', 'Sales Promoter / Handling Agent / Transporter', ['SalesPromoter', 'HandlingAgent', 'Transporter']),
    ('dealer', 'Dealer', ['Dealer', 'Distributor']),
    ('retailer', 'Retailer', ['Retailer', 'Institutional']),
]
LEVEL_OF = {k: i for i, (_, _, kinds) in enumerate(LEVELS) for k in kinds}
ALLOWED_PARENTS = {
    'CF': [],
    'SalesPromoter': ['CF'], 'HandlingAgent': ['CF'], 'Transporter': ['CF'],
    'Dealer': ['CF', 'SalesPromoter', 'HandlingAgent'], 'Distributor': ['CF', 'SalesPromoter', 'HandlingAgent'],
    'Retailer': ['Dealer', 'Distributor'], 'Institutional': ['Dealer', 'Distributor'],
    'Supplier': [],
}


def allowed_parents(kind):
    return ALLOWED_PARENTS.get(kind, [])


def parent_choices(partner):
    kinds = allowed_parents(partner.kind)
    if not kinds:
        return Partner.objects.none()
    return Partner.objects.filter(kind__in=kinds, active=True).exclude(pk=partner.pk).order_by('kind', 'name')


def chain(partner):
    """Upline from the direct parent to the top, e.g. [promoter, C&F]."""
    out, seen, p = [], {partner.pk}, partner.parent
    while p is not None and p.pk not in seen and len(out) < 6:
        out.append(p)
        seen.add(p.pk)
        p = p.parent
    return out


def descendant_ids(partner_or_ids):
    """All partners below (any depth), not including the starting ones."""
    frontier = set(partner_or_ids) if isinstance(partner_or_ids, (set, list, tuple)) else {partner_or_ids.pk}
    seen = set(frontier)
    out = set()
    for _ in range(len(LEVELS)):
        nxt = set(Partner.objects.filter(parent_id__in=frontier).values_list('pk', flat=True)) - seen
        if not nxt:
            break
        out |= nxt
        seen |= nxt
        frontier = nxt
    return out


@transaction.atomic
def set_parent(user, partner, parent, note=''):
    if not user.has_perm('erp.change_partner'):
        raise PermissionDenied
    partner = Partner.objects.select_for_update().get(pk=partner.pk)
    old = partner.parent
    if parent is not None:
        if parent.pk == partner.pk:
            raise ValidationError('A partner cannot be its own upline.')
        allowed = allowed_parents(partner.kind)
        if parent.kind not in allowed:
            names = dict(Partner._meta.get_field('kind').choices)
            raise ValidationError(f'A {partner.get_kind_display()} can sit under ' + (' / '.join(names[k] for k in allowed) if allowed else 'nobody — it works directly with the company') + f', not a {parent.get_kind_display()}.')
        if not parent.active:
            raise ValidationError(f'{parent.name} is inactive — activate it first.')
        if parent.pk in descendant_ids(partner):
            raise ValidationError(f'{parent.name} is below {partner.name} in the network.')
    if (old.pk if old else None) == (parent.pk if parent else None):
        return partner
    partner.parent = parent
    partner.save(update_fields=['parent', 'updated_at'])
    AuditEvent.objects.create(actor=user, action='Upline changed', reference=f'partner:{partner.pk}',
                              detail=f'{partner.name}: {old.name if old else "company direct"} → {parent.name if parent else "company direct"}'
                                     + (f' · {note.strip()}' if (note or '').strip() else '')[:250])
    return partner


def clear_if_invalid(user, partner):
    """After a type change: drop an upline that the new type may not have."""
    if partner.parent_id and partner.parent.kind not in allowed_parents(partner.kind):
        set_parent(user, partner, None, 'type changed')


# ---------- Read models ----------

def level_summary():
    """Per level: active count and how many still have no upline (data to fix), plus the mason level."""
    act = Partner.objects.filter(active=True)
    rows = []
    for i, (key, label, kinds) in enumerate(LEVELS):
        qs = act.filter(kind__in=kinds)
        rows.append({'key': key, 'label': label, 'kinds': kinds, 'count': qs.count(),
                     'unlinked': qs.filter(parent__isnull=True).count() if i else None, 'first': kinds[0]})
    m = Influencer.objects.filter(active=True)
    rows.append({'key': 'mason', 'label': 'Mason', 'kinds': [], 'count': m.filter(kind='Mason').count(),
                 'unlinked': m.filter(primary_dealer__isnull=True, dealers__isnull=True).distinct().count(), 'first': 'Mason'})
    return rows


def tree(q=''):
    """Nested network for the People page: top nodes (C&F, and dealers/agents that work with the company directly) with their
    downline down to dealers; retailers and masons are shown as counts per dealer (they can be thousands)."""
    nodes = list(Partner.objects.filter(active=True, kind__in=[k for _, _, ks in LEVELS[:3] for k in ks]).order_by('name').values('pk', 'name', 'code', 'kind', 'parent_id', 'city'))
    retail = dict(Partner.objects.filter(active=True, kind__in=LEVELS[3][2], parent__isnull=False).values_list('parent').annotate(n=Count('pk')))
    masons = dict(Influencer.objects.filter(active=True, primary_dealer__isnull=False).values_list('primary_dealer').annotate(n=Count('pk')))
    kinds = dict(Partner._meta.get_field('kind').choices)
    by_parent = defaultdict(list)
    ids = {n['pk'] for n in nodes}
    for n in nodes:
        n['label'] = kinds.get(n['kind'], n['kind'])
        n['retailers'], n['masons'] = retail.get(n['pk'], 0), masons.get(n['pk'], 0)
        by_parent[n['parent_id'] if n['parent_id'] in ids else None].append(n)

    def build(n, depth=0):
        n['children'] = [build(c, depth + 1) for c in by_parent.get(n['pk'], [])] if depth < 3 else []
        n['dealers'] = sum(1 for c in n['children'] if c['kind'] in ('Dealer', 'Distributor')) + sum(c['dealers'] for c in n['children'])
        n['total_retailers'] = n['retailers'] + sum(c['total_retailers'] for c in n['children'])
        return n

    roots = [build(n) for n in by_parent.get(None, [])]
    if q:
        ql = q.lower()

        def keep(n):
            n['children'] = [c for c in n['children'] if keep(c)]
            return bool(n['children']) or ql in n['name'].lower() or ql in (n['code'] or '').lower() or ql in (n['city'] or '').lower()
        roots = [r for r in roots if keep(r)]
    order = {'CF': 0, 'SalesPromoter': 1, 'HandlingAgent': 1, 'Transporter': 1}
    roots.sort(key=lambda n: (order.get(n['kind'], 2), n['name']))
    return {'cf': [r for r in roots if r['kind'] == 'CF'], 'direct': [r for r in roots if r['kind'] != 'CF']}


def network_card(user, partner, period=None):
    """Partner page: upline chain, direct downline, counts by type below, and this month's sales of self vs network."""
    from django.utils import timezone
    from . import insights as I
    below = descendant_ids(partner)
    direct = list(partner.downline.filter(active=True).order_by('kind', 'name')[:10])
    counts = []
    if below:
        kinds = dict(Partner._meta.get_field('kind').choices)
        counts = [(kinds.get(k, k), n) for k, n in Partner.objects.filter(pk__in=below, active=True).values_list('kind').annotate(n=Count('pk')).order_by('kind')]
    mason_q = Q(primary_dealer_id__in=below | {partner.pk}) | Q(dealers__pk__in=below | {partner.pk})
    masons = Influencer.objects.filter(active=True).filter(mason_q).distinct().count()
    sales = None
    if user.has_perm('erp.view_invoice') and (below or partner.is_customer):
        today = timezone.localdate()
        start = today.replace(day=1)
        rows = I.net_sales(I.visible_sites(user), start, today, dim='partner')
        own = rows.get(partner.pk, {'value': ZERO, 'mt': ZERO})
        net = {'value': sum((rows[k]['value'] for k in below if k in rows), ZERO), 'mt': sum((rows[k]['mt'] for k in below if k in rows), ZERO)}
        sales = {'own': own, 'network': net, 'total_value': own['value'] + net['value'], 'total_mt': own['mt'] + net['mt'], 'since': start}
    commission = None
    from .models import CHANNEL_KINDS, ChannelRate
    if partner.kind in CHANNEL_KINDS and user.has_perm('erp.view_channelrate'):
        from .channel_commission import statement
        rows = statement(partner) if user.has_perm('erp.view_channelrun') else []
        commission = {'rate': ChannelRate.objects.filter(partner=partner, status='approved').order_by('-valid_from').first(),
                      'earned': sum((r['line'].amount for r in rows), ZERO), 'due': sum((r['due'] for r in rows), ZERO), 'show_money': user.has_perm('erp.view_channelrun')}
    return {'commission': commission, 'chain': chain(partner), 'direct': direct, 'direct_total': partner.downline.filter(active=True).count(), 'below': len(below),
            'counts': counts, 'masons': masons, 'sales': sales, 'allowed': allowed_parents(partner.kind)}
