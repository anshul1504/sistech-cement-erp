"""Trip exceptions on the road (SRS FR-LOG-015).

- Breakdown / delay: reported while the truck is in transit, resolved with a note (repaired, load shifted to another truck…).
- Diversion: new delivery point requested with reason; a different user (logistics/sales authority) approves. The tax
  invoice snapshot is not changed — update the e-way bill Part-B / ship-to with the GSP as required.
- Refused load: a different user approves the return to origin; this opens a customer return (RMA) for the whole trip
  quantity, and the trip is marked 'Returned to origin'. Goods come back through RMA pickup, inspection and credit note,
  so stock and receivables are corrected only through those controlled documents.
"""
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import Dispatch, DispatchIncident, AuditEvent, INCIDENT_KINDS
from .services import require_site


def _log(user, d, action, detail=''):
    AuditEvent.objects.create(actor=user, site=d.site, action=action, reference=d.number, detail=detail[:250])


@transaction.atomic
def report(user, dispatch_pk, kind, note, location='', new_ship_to=''):
    if not (user.has_perm('erp.pod_dispatch') or user.has_perm('erp.add_dispatch')):
        raise PermissionDenied
    d = Dispatch.objects.select_for_update().select_related('site').get(pk=dispatch_pk)
    require_site(user, d.site)
    if d.status != 'gate_out':
        raise ValidationError('Exceptions are reported while the truck is in transit.')
    if kind not in dict(INCIDENT_KINDS):
        raise ValidationError('Choose what happened.')
    if not (note or '').strip():
        raise ValidationError('Describe what happened.')
    if kind == 'diversion' and not (new_ship_to or '').strip():
        raise ValidationError('Enter the new delivery address.')
    if kind in ('diversion', 'refused') and d.incidents.filter(kind__in=['diversion', 'refused'], status='open').exists():
        raise ValidationError('A diversion or refusal is already waiting for a decision on this trip.')
    x = DispatchIncident.objects.create(dispatch=d, kind=kind, note=note.strip()[:300], location=(location or '').strip()[:120],
                                        new_ship_to=(new_ship_to or '').strip()[:300] if kind == 'diversion' else '', reported_by=user)
    _log(user, d, f'Trip exception: {x.get_kind_display()}', f'{x.note} {x.location}'.strip())
    return x


@transaction.atomic
def resolve(user, pk, note):
    x = DispatchIncident.objects.select_for_update().select_related('dispatch__site').get(pk=pk)
    if not (user.has_perm('erp.pod_dispatch') or user.has_perm('erp.add_dispatch')):
        raise PermissionDenied
    require_site(user, x.dispatch.site)
    if x.kind not in ('breakdown', 'delay') or x.status != 'open':
        raise ValidationError('Only an open breakdown or delay is resolved this way.')
    if not (note or '').strip():
        raise ValidationError('Say how it was resolved.')
    x.status, x.decided_by, x.decided_at, x.decision_note = 'resolved', user, timezone.now(), note.strip()[:200]
    x.save()
    _log(user, x.dispatch, 'Trip exception resolved', note)
    return x


@transaction.atomic
def decide(user, pk, approve, note=''):
    if not user.has_perm('erp.decide_dispatchincident'):
        raise PermissionDenied
    x = DispatchIncident.objects.select_for_update().select_related('dispatch__site', 'dispatch__order__partner').get(pk=pk)
    d = Dispatch.objects.select_for_update().get(pk=x.dispatch_id)
    require_site(user, d.site)
    if x.kind not in ('diversion', 'refused') or x.status != 'open':
        raise ValidationError('Only an open diversion or refusal needs a decision.')
    if user.pk == x.reported_by_id:
        raise ValidationError('A different user must decide (maker-checker).')
    if not approve and not (note or '').strip():
        raise ValidationError('Give the reason for rejecting.')
    if approve and d.status != 'gate_out':
        raise ValidationError('The trip is no longer in transit.')
    if approve and x.kind == 'refused':
        invoice = getattr(d, 'invoice', None)
        if invoice is None:
            raise ValidationError('The trip has no tax invoice — cancel the dispatch instead.')
        from . import returns
        qty = {}
        for l in d.lines.select_related('order_line'):
            il = invoice.lines.filter(product=l.order_line.product).first()
            if il and l.quantity:
                qty[il] = min(l.quantity, returns.returnable(il))
        x.return_request = returns.request_return(user, invoice, 'other', f'Load refused on {d.number}: {x.note}'[:300], qty)
        d.status = 'returned'
        d.save(update_fields=['status'])
    x.status, x.decided_by, x.decided_at, x.decision_note = ('approved' if approve else 'rejected'), user, timezone.now(), (note or '').strip()[:200]
    x.save()
    _log(user, d, f'{x.get_kind_display()} {"approved" if approve else "rejected"}',
         (f'{x.return_request.number} opened' if x.return_request_id else x.new_ship_to or '') + (f' · {note}' if note else ''))
    return x
