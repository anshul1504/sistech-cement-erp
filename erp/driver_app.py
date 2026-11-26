"""Driver app: a linked driver sees only the trips assigned to them and, while the truck is on the road, records the
proof of delivery (with photo) and reports breakdowns, delays, diversions and refused loads.

Same rules as the office screens — they call the same core functions (logistics.record_pod, incidents.create):
- a driver login is linked by an administrator (Driver.user + app_enabled), never by the driver;
- the link stops working when the login or the driver record is disabled;
- only trips of that driver, and only while in transit, can be changed; nothing else in the ERP opens for them;
- diversions and refusals still need a decision by an office user, as on the web.
"""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Driver, Dispatch

ACTIVE = ['planned', 'gate_in', 'tared', 'loading', 'loaded', 'weighed', 'gate_out']
VIA = 'driver app'


def driver_for(user):
    if not user or not user.is_authenticated or not user.is_active:
        return None
    return Driver.objects.select_related('transporter').filter(user=user, app_enabled=True, active=True).first()


def is_driver(user):
    return driver_for(user) is not None


def trips(driver, days=30):
    """Open trips first, then trips finished in the last `days` days."""
    since = timezone.now() - timedelta(days=days)
    qs = Dispatch.objects.filter(driver=driver).filter(Q(status__in=ACTIVE) | Q(delivered_at__gte=since) | Q(created_at__gte=since))
    return qs.exclude(status='cancelled').select_related('order__partner', 'site', 'vehicle', 'transporter').prefetch_related('lines__order_line__product', 'incidents')


def trip(driver, pk):
    d = trips(driver, days=90).filter(pk=pk).first()
    if not d:
        raise PermissionDenied
    return d


def ship_to(d):
    o, p = d.order, d.order.partner
    return (o.ship_to or p.shipping_address or p.address or p.city or '').strip()


def _lock(user, pk):
    driver = driver_for(user)
    if not driver:
        raise PermissionDenied
    d = Dispatch.objects.select_for_update().select_related('site', 'driver').filter(pk=pk, driver=driver).first()
    if not d:
        raise PermissionDenied
    if d.status != 'gate_out':
        raise ValidationError('This trip is not on the road — only a truck in transit can be updated from the app.')
    return d


@transaction.atomic
def deliver(user, pk, received_by, receiver_mobile='', shortage=0, damaged=0, notes='', file=None):
    from .logistics import record_pod
    d = _lock(user, pk)
    if not file:
        raise ValidationError('Take a photo of the signed delivery copy (POD).')
    return record_pod(user, d, received_by, receiver_mobile, shortage, damaged, notes, file, via=VIA)


@transaction.atomic
def report(user, pk, kind, note, location='', new_ship_to=''):
    from .incidents import create
    d = _lock(user, pk)
    return create(user, d, kind, note, location, new_ship_to, via=VIA)


def summary(driver):
    qs = list(trips(driver))
    today = timezone.localdate()
    return {'on_road': sum(1 for d in qs if d.status == 'gate_out'),
            'at_plant': sum(1 for d in qs if d.status in ACTIVE and d.status != 'gate_out'),
            'delivered_today': sum(1 for d in qs if d.status == 'delivered' and d.delivered_at and timezone.localtime(d.delivered_at).date() == today),
            'delivered_month': sum(1 for d in qs if d.status == 'delivered' and d.delivered_at and timezone.localtime(d.delivered_at).date() >= today.replace(day=1)),
            'licence_valid_until': driver.licence_valid_until, 'licence_valid': driver.licence_valid}
