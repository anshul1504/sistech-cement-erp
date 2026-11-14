"""Site-scoped customer returns; physical custody remains separate from financial credit."""
import hashlib
import json
from decimal import Decimal
from uuid import UUID

from django import forms
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum, Case, When, F, DecimalField
from django.utils import timezone

from .gst import next_number, money, qtxt
from .models import ReturnRequest, ReturnLine, AuditEvent, RETURN_REASONS, Invoice, InvoiceLine, Partner, Complaint
from .services import require_site, permitted

ZERO = Decimal('0')
OPEN = ['requested', 'approved', 'transit', 'inspected']


def quantity(value):
    return forms.DecimalField(max_digits=14, decimal_places=3, min_value=0).clean(value if value is not None else 0)


def _log(user, r, action, detail=''):
    AuditEvent.objects.create(actor=user, site=r.site, action=action, reference=r.number, detail=detail)


def reserved(line, exclude=None):
    rows = ReturnLine.objects.filter(invoice_line=line, request__status__in=OPEN)
    if exclude:
        rows = rows.exclude(request=exclude)
    return rows.aggregate(q=Sum(Case(When(request__status='inspected', then=F('good')+F('damaged')), default=F('quantity'),
                                     output_field=DecimalField(max_digits=18, decimal_places=3))))['q'] or ZERO


def returnable(line, exclude=None):
    credited = line.credit_lines.aggregate(q=Sum('quantity'))['q'] or ZERO
    return max(line.quantity - credited - reserved(line, exclude), ZERO)


def _invoice(user, pk):
    header = Invoice.objects.only('partner_id', 'site_id').get(pk=pk)
    require_site(user, header.site)
    Partner.objects.select_for_update().get(pk=header.partner_id)
    return Invoice.objects.select_for_update().select_related('site', 'partner').get(pk=pk)


def _lock(user, pk):
    header = ReturnRequest.objects.only('invoice_id').get(pk=pk)
    inv = _invoice(user, header.invoice_id)
    r = ReturnRequest.objects.select_for_update().get(pk=pk)
    r.invoice, r.site, r.partner = inv, inv.site, inv.partner
    return r


@transaction.atomic
def request_return(user, invoice, reason, description, quantities, complaint=None, submission_key=None):
    permitted(user, 'add_returnrequest')
    inv = _invoice(user, invoice.pk)
    if reason not in dict(RETURN_REASONS) or not (description or '').strip():
        raise ValidationError('Choose a return reason and describe the goods/problem.')
    if len(description.strip()) > 300:
        raise ValidationError('Keep the description within 300 characters.')
    picked = {}
    for line, raw in quantities.items():
        if not isinstance(line, InvoiceLine) or line.invoice_id != inv.pk:
            raise ValidationError('Each return line must belong to the selected invoice.')
        qty = quantity(raw)
        if qty:
            picked[line.pk] = qty
    if not picked:
        raise ValidationError('Enter a positive return quantity on at least one line.')
    if complaint:
        permitted(user, 'view_complaint')
        complaint = Complaint.objects.get(pk=complaint.pk)
        if complaint.partner_id != inv.partner_id or complaint.invoice_id != inv.pk:
            raise ValidationError('The linked complaint must match this customer, site and invoice.')
    payload = [inv.pk, reason, description.strip(), complaint.pk if complaint else None, sorted((pk, str(q.normalize())) for pk, q in picked.items())]
    fingerprint = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
    key = None
    if submission_key:
        try: key = UUID(str(submission_key))
        except (ValueError, TypeError, AttributeError): raise ValidationError('Reload the return form; its submission key is invalid.')
        old = ReturnRequest.objects.select_for_update().filter(submission_key=key).first()
        if old:
            if old.created_by_id != user.pk or old.submission_fingerprint != fingerprint:
                raise ValidationError('This form was used with different details. Open a fresh return form.')
            return old
    lines = list(inv.lines.filter(pk__in=picked).select_related('product'))
    if len(lines) != len(picked):
        raise ValidationError('One or more invoice lines are unavailable.')
    for line in lines:
        room = returnable(line)
        if picked[line.pk] > room:
            raise ValidationError(f'{line.description}: up to {qtxt(room)} {line.unit} can be returned.')
    r = ReturnRequest(number=next_number('RMA', inv.site), invoice=inv, partner=inv.partner, site=inv.site, complaint=complaint,
        reason=reason, description=description.strip(), created_by=user, submission_key=key, submission_fingerprint=fingerprint)
    r.full_clean(); r.save()
    for line in lines:
        row = ReturnLine(request=r, invoice_line=line, quantity=picked[line.pk]); row.full_clean(); row.save()
    _log(user, r, 'Return requested', f'{inv.number} · {r.get_reason_display()} · '+', '.join(f'{line.product.code} {qtxt(picked[line.pk])}' for line in lines))
    return r


@transaction.atomic
def decide(user, pk, approve, note=''):
    permitted(user, 'approve_returnrequest')
    r = _lock(user, pk)
    if r.status != 'requested': raise ValidationError('Only a waiting return can be decided.')
    if r.created_by_id == user.pk: raise ValidationError('A different user must approve the return (maker-checker).')
    if not (note or '').strip(): raise ValidationError('Record the review finding for this decision.')
    # Rejection must remain possible even if another correction has reduced the credit room.
    if approve:
        for line in r.lines.select_related('invoice_line'):
            if line.quantity > returnable(line.invoice_line, exclude=r):
                raise ValidationError(f'{line.invoice_line.description}: quantity was credited elsewhere meanwhile.')
    r.status, r.decided_by, r.decision_note = ('approved' if approve else 'rejected'), user, note.strip()[:200]
    r.save(); _log(user,r,'Return approved' if approve else 'Return rejected',note)
    return r


@transaction.atomic
def pickup(user, pk, on, vehicle, lr=''):
    permitted(user,'inspect_returnrequest')
    r=_lock(user,pk)
    if r.status!='approved': raise ValidationError('Approve the return before pickup.')
    if (on or timezone.localdate()) > timezone.localdate() or (on and on < timezone.localtime(r.created_at).date()):
        raise ValidationError('Pickup must be between the return request date and today.')
    import re
    vehicle=re.sub(r'\s+','',vehicle or '').upper()
    if not re.fullmatch(r'[A-Z0-9-]{6,15}',vehicle): raise ValidationError('Enter a valid vehicle registration (6–15 letters/digits).')
    if len(lr or '')>30: raise ValidationError('Keep the LR/challan within 30 characters.')
    r.status,r.pickup_on,r.vehicle_number,r.lr_number='transit',on or timezone.localdate(),vehicle,lr or ''
    r.save();_log(user,r,'Return picked up',f'{r.vehicle_number} · {r.lr_number}')
    return r


@transaction.atomic
def inspect(user,pk,results):
    permitted(user,'inspect_returnrequest')
    r=_lock(user,pk)
    if r.status not in ('approved','transit'): raise ValidationError('Record inspection once, when approved goods arrive.')
    lines=list(r.lines.select_related('invoice_line__product'))
    if set(results)!={line.pk for line in lines}: raise ValidationError('Record every approved return line, including zero receipts.')
    for line in lines:
        raw=results[line.pk]
        received,good,damaged=map(quantity,raw[:3]);note=(raw[3] or '').strip()
        if received>line.quantity: raise ValidationError(f'{line.invoice_line.description}: received more than approved.')
        if good+damaged>received: raise ValidationError('Good plus damaged quantities cannot exceed received quantity.')
        if received-good-damaged>0 and not note: raise ValidationError('Record why received goods were not accepted.')
        if len(note)>150: raise ValidationError('Keep each inspection note within 150 characters.')
        line.received,line.good,line.damaged,line.note=received,good,damaged,note
        line.full_clean()
    if not any(line.received for line in lines): raise ValidationError('Nothing received. Record pickup/arrival only when goods arrive.')
    for line in lines: line.save()
    r.status='inspected' if any(line.good+line.damaged for line in lines) else 'closed'
    r.inspected_by,r.inspected_at=user,timezone.now();r.save()
    _log(user,r,'Return inspected',', '.join(f'{line.invoice_line.product.code}: received {qtxt(line.received)}, good {qtxt(line.good)}, damaged {qtxt(line.damaged)}, not accepted {qtxt(line.not_accepted)} · {line.note}' for line in lines))
    return r


@transaction.atomic
def credit(user,pk,notes=''):
    permitted(user,'add_creditnote')
    from .finance_access import require_company_finance
    require_company_finance(user)
    from . import gl
    gl.lock_finance()
    r=_lock(user,pk)
    if r.status=='credited': return r  # Same source can only post one credit note.
    if r.status!='inspected': raise ValidationError('Credit requires approved and inspected goods.')
    if user.pk in [r.created_by_id,r.inspected_by_id]: raise ValidationError('Finance must be independent of the requester and inspector.')
    picked,damaged=[],{}
    from .finance_views import credit_room
    for line in r.lines.select_related('invoice_line__product'):
        qty=line.good+line.damaged
        if qty<=0: continue
        room_value,room_qty=credit_room(line.invoice_line,exclude_return=r)
        if qty>room_qty: raise ValidationError('Another correction has consumed this return quantity. Review the original invoice.')
        picked.append((line.invoice_line,qty,min(money(qty*line.invoice_line.rate),room_value)))
        damaged[line.invoice_line_id]=line.damaged
    from .credits import create_credit_note
    cn=create_credit_note(user,r.invoice,'return',picked,notes or f'{r.number} · {r.get_reason_display()}',damaged=damaged,return_request=r)
    r.credit_note,r.status=cn,'credited';r.save()
    _log(user,r,'Return credited',f'{cn.number} · ₹{cn.total}; good stock available, damaged stock segregated')
    if r.complaint_id and r.complaint.resolution=='credit' and not r.complaint.credit_note_id:
        r.complaint.credit_note=cn;r.complaint.save(update_fields=['credit_note'])
    return r


@transaction.atomic
def cancel(user,pk,reason):
    r=_lock(user,pk)
    if not (user.has_perm('erp.approve_returnrequest') or (r.created_by_id==user.pk and user.has_perm('erp.add_returnrequest'))): raise PermissionDenied
    if r.status not in ('requested','approved'): raise ValidationError('Picked-up or inspected goods cannot disappear through cancellation. Finish inspection and credit/disposition.')
    if not (reason or '').strip(): raise ValidationError('Record the cancellation reason.')
    r.status='cancelled';r.save();_log(user,r,'Return cancelled',reason)
    return r
