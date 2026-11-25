"""Bin-level stock inside a plant or depot (SRS FR-INV-001), layered on the single site stock ledger.

Site available stock (StockBalance) stays the one source of truth for quantity and value. Bins only say *where* it is:
  unbinned = site available − Σ bin quantities (received stock waits here until put away);
  put away = unbinned → bin; move = bin → bin (same site);
  any issue that leaves the site with less available stock than the bins hold reduces bins automatically, largest
  holding first, and records the reason — so Σ bins ≤ available always holds.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum

from .models import StorageBin, BinStock, BinMove, StockBalance, AuditEvent
from .services import require_site

ZERO = Decimal('0')


def binned(site, product):
    return BinStock.objects.filter(bin__site=site, product=product).aggregate(t=Sum('quantity'))['t'] or ZERO


def available(site, product):
    return StockBalance.objects.filter(site=site, product=product, status='available').aggregate(t=Sum('quantity'))['t'] or ZERO


def unbinned(site, product):
    return available(site, product) - binned(site, product)


def _need(user):
    if not user.has_perm('erp.move_binstock'):
        raise PermissionDenied


def _qty(q):
    try:
        q = Decimal(str(q))
    except Exception:
        raise ValidationError('Enter the quantity.')
    if not q.is_finite() or q <= 0:
        raise ValidationError('Quantity must be more than zero.')
    return q.quantize(Decimal('0.001'))


@transaction.atomic
def put_away(user, product, to_bin, quantity):
    _need(user)
    require_site(user, to_bin.site)
    q = _qty(quantity)
    list(StockBalance.objects.select_for_update().filter(site=to_bin.site, product=product, status='available'))
    if not to_bin.active:
        raise ValidationError(f'Bin {to_bin} is inactive.')
    free = unbinned(to_bin.site, product)
    if q > free:
        raise ValidationError(f'Only {free.normalize():f} {product.unit} of {product.name} is not yet in a bin at {to_bin.site.code}.')
    row, _ = BinStock.objects.select_for_update().get_or_create(bin=to_bin, product=product)
    row.quantity += q
    row.save()
    BinMove.objects.create(site=to_bin.site, product=product, to_bin=to_bin, quantity=q, kind='put', actor=user)
    AuditEvent.objects.create(actor=user, site=to_bin.site, action='Stock put away', reference=str(to_bin), detail=f'{product.code} {q.normalize():f}')
    return row


@transaction.atomic
def move(user, product, from_bin, to_bin, quantity):
    _need(user)
    require_site(user, from_bin.site)
    q = _qty(quantity)
    if from_bin.site_id != to_bin.site_id:
        raise ValidationError('Bins must be in the same plant / depot — use a stock transfer between sites.')
    if from_bin.pk == to_bin.pk:
        raise ValidationError('Choose a different bin.')
    if not to_bin.active:
        raise ValidationError(f'Bin {to_bin} is inactive.')
    src = BinStock.objects.select_for_update().filter(bin=from_bin, product=product).first()
    if not src or src.quantity < q:
        raise ValidationError(f'{from_bin} holds only {(src.quantity if src else ZERO).normalize():f} {product.unit}.')
    dst, _ = BinStock.objects.select_for_update().get_or_create(bin=to_bin, product=product)
    src.quantity -= q
    dst.quantity += q
    src.save(); dst.save()
    BinMove.objects.create(site=from_bin.site, product=product, from_bin=from_bin, to_bin=to_bin, quantity=q, kind='move', actor=user)
    AuditEvent.objects.create(actor=user, site=from_bin.site, action='Stock moved between bins', reference=f'{from_bin} → {to_bin}', detail=f'{product.code} {q.normalize():f}')
    return dst


def sync_after_issue(user, site, product, source=''):
    """Called by move_stock after available stock falls: trim bins so Σ bins ≤ available."""
    rows = list(BinStock.objects.select_for_update().filter(bin__site=site, product=product, quantity__gt=0).order_by('-quantity', 'bin__code'))
    if not rows:
        return
    excess = sum((r.quantity for r in rows), ZERO) - available(site, product)
    for r in rows:
        if excess <= 0:
            break
        take = min(r.quantity, excess)
        r.quantity -= take
        r.save(update_fields=['quantity'])
        BinMove.objects.create(site=site, product=product, from_bin=r.bin, quantity=take, kind='issue', source=(source or '')[:80], actor=user)
        excess -= take
