"""Firm site/product commitments. Caller business services retain order/action authority."""
import uuid
from decimal import Decimal
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Sum,F,DecimalField,ExpressionWrapper
from accounts.security import access_allowed
from .models import StockBalance,StockReservation,StockReservationEvent,OrderLine,Order
ZERO=Decimal('0')


def reserved(site,product,exclude_order=None):
    qs=StockReservation.objects.filter(order_line__order__site=site,order_line__product=product)
    if exclude_order is not None:qs=qs.exclude(order_line__order_id=exclude_order)
    value=ExpressionWrapper(F('quantity')-F('consumed')-F('released'),output_field=DecimalField(max_digits=20,decimal_places=3))
    return (qs.aggregate(total=Sum(value))['total'] or ZERO).quantize(Decimal('.001'))


def lock_balances(order):
    ids=list(order.lines.order_by('product_id').values_list('product_id',flat=True).distinct())
    for product_id in ids:StockBalance.objects.get_or_create(site_id=order.site_id,product_id=product_id,status='available')
    return list(StockBalance.objects.select_for_update().filter(site=order.site,product_id__in=ids,status='available').order_by('product_id'))


def event(user,row,action,amount,reason,source=None):
    row.full_clean();row.save()
    record=StockReservationEvent(reservation=row,action=action,quantity=amount,remaining_after=row.remaining,actor=user,
        reason=reason[:200],source=source or f'reservation:{uuid.uuid4()}')
    record.full_clean();record.save()


@transaction.atomic
def allocate(user,order,reason='Approved sales allocation',respect_legacy=False):
    """Order is locked by the caller; stock mutex serializes all physical/commitment writes."""
    if order.kind!='Sale' or order.status not in ['Approved','Part dispatched','On hold']:return []
    balances={row.product_id:row for row in lock_balances(order)};result=[]
    for line in order.lines.select_related('product').order_by('product_id','pk'):
        row,_=StockReservation.objects.get_or_create(order_line=line)
        row=StockReservation.objects.select_for_update().get(pk=row.pk)
        need=max(line.quantity-line.dispatched_quantity()-row.remaining,ZERO)
        free=max(balances[line.product_id].quantity-reserved(order.site,line.product),ZERO)
        if respect_legacy:
            legacy=OrderLine.objects.filter(order__kind='Sale',order__site=order.site,order__status__in=['Approved','Part dispatched','On hold'],product=line.product,stock_reservation__isnull=True).exclude(order=order)
            free=max(free-sum((old.pending_quantity for old in legacy),ZERO),ZERO)
        amount=min(need,free)
        if amount:
            row.quantity+=amount;event(user,row,'reserve',amount,reason)
        result.append(row)
    return result


@transaction.atomic
def release(user,order,reason):
    lock_balances(order)
    for row in StockReservation.objects.select_for_update().filter(order_line__order=order).order_by('order_line__product_id','pk'):
        if row.remaining:
            amount=row.remaining;row.released+=amount;event(user,row,'release',amount,reason)


def consume(user,balance,amount,source,order_line=None):
    """Runs inside move_stock's transaction while the physical balance is locked."""
    total=reserved(balance.site,balance.product)
    if order_line is None:
        if amount>balance.quantity-total:raise ValidationError('This issue would consume stock firmly reserved for sales orders. Release/reallocate the commitments first.')
        return
    line=OrderLine.objects.select_related('order').get(pk=order_line.pk)
    if line.product_id!=balance.product_id or line.order.site_id!=balance.site_id or line.order.kind!='Sale' or line.order.status not in ['Approved','Part dispatched']:
        raise ValidationError('The stock issue requires a current approved sales allocation at this site/product.')
    row,_=StockReservation.objects.get_or_create(order_line=line)
    row=StockReservation.objects.select_for_update().get(pk=row.pk)
    if amount>min(line.quantity-line.dispatched_quantity(),line.quantity-row.consumed):raise ValidationError('Issue quantity exceeds the remaining ordered quantity.')
    missing=max(amount-row.remaining,ZERO)
    if missing>balance.quantity-total:raise ValidationError('Insufficient unreserved stock to complete this order allocation.')
    if missing:
        row.quantity+=missing;event(user,row,'reserve',missing,'Allocate immediately before authorized issue')
    row.consumed+=amount;event(user,row,'consume',amount,'Stock issue against sales commitment',f'{source}:reservation')


@transaction.atomic
def restore(user,line,amount,source):
    balance=StockBalance.objects.select_for_update().get(site=line.order.site,product=line.product,status='available')
    row=StockReservation.objects.select_for_update().filter(order_line=line).first()
    if row is None:return
    if amount>row.consumed:raise ValidationError('Returned reservation exceeds its retained issued quantity.')
    row.consumed-=amount;event(user,row,'restore',amount,'Return reserved stock from cancelled dispatch',f'{source}:reservation-return')


@transaction.atomic
def allocate_order(user,pk):
    from .services import scope,require_site,audit
    from accounts.roles import order_permission
    from .sales import dispatch_block
    if not access_allowed(user) or not order_permission(user,'Sale','post'):raise PermissionDenied
    order=scope(Order.objects.select_for_update(),user).get(pk=pk);require_site(user,order.site)
    if order.kind!='Sale' or order.status not in ['Approved','Part dispatched']:raise ValidationError('Allocate only an open, approved sales order.')
    errors=dispatch_block(order)
    if errors:raise ValidationError(errors)
    rows=allocate(user,order,'Reviewed allocation of approved/backordered demand')
    audit(user,'Sales stock allocated',order,f'{sum((row.remaining for row in rows),ZERO)} units firmly reserved')
    return order


@transaction.atomic
def release_order(user,pk,reason):
    from .services import scope,require_site,audit
    from accounts.roles import order_permission
    if not access_allowed(user) or not order_permission(user,'Sale','post'):raise PermissionDenied
    order=scope(Order.objects.select_for_update(),user).get(pk=pk);require_site(user,order.site)
    if order.kind!='Sale' or order.status not in ['Approved','Part dispatched','On hold']:raise ValidationError('Release commitments only for an open sales order.')
    reason=(reason or '').strip()
    if not reason or len(reason)>200:raise ValidationError('Record a stock deallocation reason of up to 200 characters.')
    if order.dispatches.filter(status__in=['planned','gate_in','tared','loading','loaded','weighed']).exists():raise ValidationError('Cancel or finish the active truck advice before releasing its stock commitment.')
    release(user,order,reason);audit(user,'Sales stock deallocated',order,reason)
    return order
