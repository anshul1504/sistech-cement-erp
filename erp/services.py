from decimal import Decimal
from .gst import inr
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from .models import Order, Partner, StockBalance, StockMovement, AuditEvent, Production

def audit(user, action, obj, detail=''):
    AuditEvent.objects.create(actor=user, site=getattr(obj, 'site', None), action=action, reference=str(getattr(obj, 'number', obj)), detail=detail)

def permitted(user, permission):
    if not user.has_perm('erp.' + permission): raise PermissionDenied

def require_site(user, site):
    """Service-layer site boundary; a module grant never implies access to every plant."""
    from .models import SiteAccess
    if site is None:
        raise ValidationError('Choose a plant or depot.')
    if not user.is_active or (not user.is_superuser and not SiteAccess.objects.filter(user=user, site_id=site.pk).exists()):
        raise PermissionDenied


def scope(queryset, user, field='site'):
    return queryset if user.is_superuser else queryset.filter(**{field + '__siteaccess__user': user})

@transaction.atomic
def move_stock(user, site, product, quantity, source, kind, notes='', status='available', unit_cost=None, value=None):
    """The stock/GL value is exact currency; rounded average cost is only a display rate.

    Explicit incoming value preserves production/transfer cost. Outgoing value is
    allocated from the carrying amount; a final issue takes every remaining paisa.
    """
    require_site(user, site)
    quantity = Decimal(quantity)
    if not quantity.is_finite():
        raise ValidationError('Stock quantity must be a finite number.')
    StockBalance.objects.get_or_create(site=site, product=product, status=status)
    balance = StockBalance.objects.select_for_update().get(site=site, product=product, status=status)
    if quantity < 0 and balance.quantity < -quantity:
        label = '' if status == 'available' else f' ({status})'
        raise ValidationError(f'Insufficient available stock for {product.name}{label} at {site.code}: {balance.quantity.normalize():f} on hand.')
    before = balance.value
    if quantity > 0:
        cost = Decimal(unit_cost) if unit_cost is not None else balance.avg_cost
        if not cost.is_finite() or cost < 0:
            raise ValidationError('Unit cost must be a finite, nonnegative number.')
        incoming = Decimal(value) if value is not None else quantity * cost
        if not incoming.is_finite() or incoming < 0:
            raise ValidationError('Stock value must be a finite, nonnegative number.')
        delta = incoming.quantize(Decimal('0.01'))
    elif quantity < 0:
        delta = -before if -quantity == balance.quantity else (before * quantity / balance.quantity).quantize(Decimal('0.01'))
        cost = balance.avg_cost
    else:
        delta, cost = Decimal('0'), balance.avg_cost
    balance.quantity += quantity
    balance.carrying_value = before + delta
    balance.avg_cost = (balance.carrying_value / balance.quantity).quantize(Decimal('0.0001')) if balance.quantity else Decimal('0')
    balance.save(update_fields=['quantity', 'avg_cost', 'carrying_value'])
    movement = StockMovement.objects.create(site=site, product=product, quantity=quantity, source=source, kind=kind, created_by=user, notes=notes,
        status=status, uom=product.unit, unit_cost=cost, balance_after=balance.quantity, value_delta=delta)
    from .gl import post_stock
    post_stock(user, movement)
    return movement


@transaction.atomic
def transfer_state(user, site, product, quantity, source, kind_out, kind_in, from_status, to_status, notes=''):
    """Move quantity between stock states at one site (e.g. available → damaged) at the current average cost."""
    out = move_stock(user, site, product, -Decimal(quantity), source + ':out', kind_out, notes, status=from_status)
    return move_stock(user, site, product, Decimal(quantity), source + ':in', kind_in, notes, status=to_status, unit_cost=out.unit_cost, value=abs(out.value))


@transaction.atomic
def transition_order(user, pk, action):
    order = scope(Order.objects.select_for_update(), user).get(pk=pk)
    from accounts.roles import order_permission
    if action in ['submit', 'approve', 'post', 'cancel', 'close'] and not order_permission(user, order.kind, 'post' if action == 'close' else action):
        raise PermissionDenied
    if action == 'submit':
        if order.status != 'Draft': raise ValidationError('Only draft orders can be submitted.')
        if not order.lines.exists(): raise ValidationError('Add at least one order line.')
        if order.kind == 'Purchase':
            _purchase_checks(order)
        order.status = 'Submitted'
    elif action == 'approve':
        if order.status != 'Submitted': raise ValidationError('Only submitted orders can be approved.')
        if order.created_by_id == user.pk: raise ValidationError('A different user must approve this order (maker-checker).')
        if not order.partner.active: raise ValidationError('The partner is inactive.')
        if order.kind == 'Sale':
            # Serialize credit approvals for a dealer on row-locking databases.
            partner = Partner.objects.select_for_update().get(pk=order.partner_id)
            from .ledger import exposure, overdue
            late = overdue(partner)
            if late:
                raise ValidationError(f'{len(late)} invoice{"s" if len(late) > 1 else ""} past due (₹{inr(sum(i.outstanding for i in late), 0)}, oldest {late[0].number}). Collect payment before approving new orders.')
            exp = exposure(partner, exclude_order=order.pk)
            if exp['total'] + order.grand_total > partner.credit_limit:
                raise ValidationError(f'Dealer credit limit exceeded: outstanding ₹{inr(exp["balance"], 0)} + orders not yet invoiced ₹{inr(exp["pipeline"], 0)} + this order ₹{inr(order.grand_total, 0)} > limit ₹{inr(partner.credit_limit, 0)}.')
        else:
            _purchase_checks(order)
            from .procurement import approval_limit
            limit = approval_limit()
            if order.grand_total > limit and not user.has_perm('erp.approve_high_value_purchase'):
                raise ValidationError(f'PO value ₹{inr(order.grand_total, 0)} is above the ₹{inr(limit, 0)} approval limit — a senior purchase approver must approve it.')
        order.status, order.approved_by = 'Approved', user
    elif action == 'post':
        if order.status != 'Approved': raise ValidationError('Only approved orders can be posted, once.')
        if order.kind == 'Sale' and order.site.uses_weighbridge:
            raise ValidationError(f'{order.site.code} dispatches through gate & weighbridge — plan a truck instead.')
        if order.kind == 'Purchase':
            raise ValidationError('Purchases are received truck by truck through Goods receipt (gate-in → GRN).')
        if not order.site.active or not order.partner.active: raise ValidationError('Site and partner must be active.')
        for line in order.lines.select_related('product').order_by('product_id', 'pk'):
            if not line.product.active: raise ValidationError('An order product is inactive.')
            move_stock(user, order.site, line.product, line.quantity if order.kind == 'Purchase' else -line.quantity, f'order:{order.pk}:line:{line.pk}', 'Receipt' if order.kind == 'Purchase' else 'Dispatch')
        order.status = 'Completed'
    elif action == 'cancel':
        if order.status in ('Part dispatched', 'Part received'): raise ValidationError('Part of this order has already moved — short-close it instead.')
        if order.status not in ['Draft', 'Submitted', 'Approved']: raise ValidationError('Completed or cancelled orders cannot be cancelled.')
        if order.dispatches.exclude(status='cancelled').exists(): raise ValidationError('A truck is planned or on its way — cancel the dispatch first.')
        if order.kind == 'Purchase' and order.grns.exclude(status='cancelled').exists(): raise ValidationError('Material has been received on this order — short-close it instead.')
        order.status = 'Cancelled'
    elif action == 'close':
        # Short-close: the customer does not want the rest. Nothing open may remain on the gate.
        if order.status not in ('Part dispatched', 'Part received'): raise ValidationError('Only part-dispatched or part-received orders can be short-closed.')
        if order.kind == 'Purchase' and order.grns.filter(status__in=['gate_in', 'weighed', 'unloaded']).exists():
            raise ValidationError('A supplier truck is still inside for this order — post or cancel its receipt first.')
        if order.dispatches.filter(status__in=['planned', 'gate_in', 'tared', 'loading', 'loaded', 'weighed']).exists():
            raise ValidationError('Finish or cancel the trucks still inside the plant first.')
        order.status = 'Completed'
    else: raise ValidationError('Unknown action.')
    order.save(update_fields=['status', 'approved_by'])
    if order.kind == 'Purchase' and action in ('approve', 'cancel', 'close'):
        from .procurement import refresh_requests
        refresh_requests(order)
        if action == 'cancel' and order.quote_id and order.quote.rfq.status == 'awarded':
            rfq = order.quote.rfq
            rfq.status = 'open'  # buyer can award the next supplier
            rfq.save(update_fields=['status'])
    audit(user, f'Order {action}', order)
    return order


def _purchase_checks(order):
    problems = []
    for line in order.lines.select_related('product'):
        if line.rate <= 0:
            problems.append(f'{line.product.name}: enter the agreed rate.')
    from .procurement import check_po_against_requests
    from .sourcing import contract_problems
    problems += check_po_against_requests(order)
    problems += contract_problems(order)
    if order.site.operated_by_id and order.partner_id != order.site.operated_by_id:
        problems.append(f'{order.site.code} is operated by {order.site.operated_by.name} — raise the PO on that partner.')
    if problems:
        raise ValidationError(problems)

@transaction.atomic
def transition_production(user, pk, action, **kwargs):
    """complete → quality hold (via production.complete); release → stock at actual cost once quality evidence is in."""
    if action == 'complete':
        from .production import complete
        return complete(user, pk, kwargs.get('output'), kwargs.get('scrap', 0), kwargs.get('note', ''))
    if action != 'release':
        raise ValidationError('Unknown action.')
    from .quality import release
    return release(user, pk, kwargs.get('decision', 'release'), kwargs.get('note', ''))
