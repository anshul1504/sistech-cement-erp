"""Customer complaints and CAPA (SRS §4.6, §5.12, FR-QC-007/008).

Flow: claim → linked to invoice / truck / batch → investigation (owner, findings) → decision by a different user
(accept / partial / reject, with quantity and settlement) → settlement evidence (credit note on that invoice, or replacement order)
→ CAPA → closure.

Controls:
- the invoice must belong to the customer; the truck must be on that invoice; the batch is taken from the truck when recorded;
- the person who logged or investigated cannot decide; accepted quantity cannot exceed the claimed quantity;
- a credit-note settlement must reference a credit note issued on the complaint's invoice; a replacement must reference a sales order
  for the same customer;
- a quality complaint closes only when every CAPA is verified effective, or with a written reason why no CAPA is needed;
- CAPA effectiveness is verified by someone other than the action owner.
"""
from accounts.delegation import authorize, can_approve
import json
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .services import require_site
from .gst import next_number
from .models import Complaint, CapaAction, AuditEvent, COMPLAINT_SLA_DAYS, CUSTOMER_KINDS


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied



def scope(queryset, user):
    """Invoice-backed cases follow site access; unlinked cases stay with their maker/owner."""
    if not user.is_active:
        return queryset.none()
    if user.is_superuser:
        return queryset
    return queryset.filter(
        Q(invoice__site__siteaccess__user=user) |
        (Q(invoice__isnull=True) & (Q(created_by=user) | Q(owner=user)))
    ).distinct()


def capa_scope(queryset, user):
    if not user.is_active:
        return queryset.none()
    if user.is_superuser:
        return queryset
    return queryset.filter(
        Q(complaint__in=scope(Complaint.objects.all(), user)) |
        (Q(complaint__isnull=True) & (Q(created_by=user) | Q(owner=user)))
    ).distinct()


def _require_case(user, complaint):
    if not scope(Complaint.objects.all(), user).filter(pk=complaint.pk).exists():
        raise PermissionDenied


def _require_capa(user, capa):
    if not capa_scope(CapaAction.objects.all(), user).filter(pk=capa.pk).exists():
        raise PermissionDenied



def complaint_makers(complaint):
    from accounts.work_handoff import former_owners
    makers = {complaint.created_by_id, complaint.owner_id}
    makers.update(AuditEvent.objects.filter(reference=complaint.number, action='Complaint investigated').values_list('actor_id', flat=True))
    makers.update(former_owners('complaint', complaint.pk))
    for detail in AuditEvent.objects.filter(reference=complaint.number,action='Complaint owner changed').values_list('detail',flat=True):
        try:makers.add(json.loads(detail).get('previous'))
        except (ValueError,TypeError,AttributeError):pass
    return makers - {None}


def capa_makers(capa):
    from accounts.work_handoff import former_owners
    makers = {capa.owner_id,capa.created_by_id}
    makers.update(AuditEvent.objects.filter(reference__in=[f'CAPA-{capa.pk:05d}',str(capa)], action='CAPA implemented').values_list('actor_id', flat=True))
    makers.update(former_owners('capa', capa.pk))
    return makers - {None}



def case_site(case):return case.invoice.site if case.invoice_id else None


def can_decide(user,case,close=False):
    return (user.has_perm('erp.view_complaint') and case.status==('decided' if close else 'investigating')
        and user.pk not in complaint_makers(case) and scope(Complaint.objects.filter(pk=case.pk),user).exists()
        and can_approve(user,'decide_complaint',case_site(case),makers=complaint_makers(case)))


def can_verify(user,capa):
    site=case_site(capa.complaint) if capa.complaint_id else None
    return (user.has_perm('erp.view_complaint') and user.has_perm('erp.view_capaaction') and capa.status=='done'
        and user.pk not in capa_makers(capa) and capa_scope(CapaAction.objects.filter(pk=capa.pk),user).exists()
        and can_approve(user,'verify_capaaction',site,makers=capa_makers(capa)))


def reviewable(qs,user,capa=False,close=False):
    from accounts.delegation import approvable
    permission='verify_capaaction' if capa else 'decide_complaint'
    scoped=capa_scope(qs,user) if capa else scope(qs,user)
    candidates=approvable(scoped,user,permission,site_field='complaint__invoice__site' if capa else 'invoice__site',
        maker_fields=('owner_id',) if capa else ('created_by_id','owner_id'))
    ids=[obj.pk for obj in candidates if (can_verify(user,obj) if capa else can_decide(user,obj,close))]
    return qs.filter(pk__in=ids)


def _log(user, c, action, detail=''):
    AuditEvent.objects.create(actor=user, site=c.invoice.site if c.invoice_id else None, action=action, reference=c.number, detail=detail[:250])


@transaction.atomic
def log(user, partner, category, description, invoice=None, dispatch=None, product=None, quantity=0, received_on=None, site_location='', owner=None):
    _need(user, 'add_complaint')
    if invoice:
        require_site(user, invoice.site)
    if dispatch:
        require_site(user, dispatch.site)
    problems = []
    if partner.kind not in CUSTOMER_KINDS:
        problems.append('Complaints are logged for customers.')
    if category not in COMPLAINT_SLA_DAYS:
        problems.append('Choose the complaint type.')
    if not (description or '').strip():
        problems.append('Describe the complaint.')
    if invoice and invoice.partner_id != partner.pk:
        problems.append(f'Invoice {invoice.number} is not for {partner.name}.')
    if dispatch and (not invoice or getattr(dispatch, 'invoice', None) != invoice):
        problems.append('The truck must be the one on the selected invoice.')
    if product and invoice and not invoice.lines.filter(product=product).exists():
        problems.append(f'{product.name} is not on invoice {invoice.number}.')
    received_on = received_on or timezone.localdate()
    if received_on > timezone.localdate():
        problems.append('Received date cannot be in the future.')
    if problems:
        raise ValidationError(problems)
    batch = None
    if dispatch:
        lines = dispatch.lines.exclude(batch=None)
        if product:
            lines = lines.filter(order_line__product=product)
        batch = lines.values_list('batch', flat=True).first()
    c = Complaint.objects.create(number=next_number('CMP', invoice.site if invoice else None), partner=partner, invoice=invoice, dispatch=dispatch, batch_id=batch,
                                 product=product, category=category, description=description.strip(), quantity=quantity or 0, received_on=received_on,
                                 due_on=received_on + timedelta(days=COMPLAINT_SLA_DAYS[category]), site_location=(site_location or '')[:150],
                                 owner=owner, created_by=user)
    _log(user, c, 'Complaint logged', f'{partner.name} · {c.get_category_display()}' + (f' · {invoice.number}' if invoice else '') + (f' · batch {c.batch}' if batch else ''))
    return c


def _lock(user, pk):
    c = Complaint.objects.select_for_update().select_related('partner', 'invoice').get(pk=pk)
    _require_case(user, c)
    return c


@transaction.atomic
def investigate(user, pk, findings, owner=None):
    _need(user, 'change_complaint')
    c = _lock(user, pk)
    if c.status not in ('open', 'investigating'):
        raise ValidationError('Findings can be recorded before the decision only.')
    if not (findings or '').strip():
        raise ValidationError('Write the findings (site visit, test results, transporter statement…).')
    previous_owner=c.owner_id
    c.findings, c.status = findings.strip(), 'investigating'
    if owner: c.owner = owner
    elif not c.owner: c.owner = user
    c.save()
    if previous_owner and previous_owner!=c.owner_id:
        _log(user,c,'Complaint owner changed',json.dumps(dict(previous=previous_owner,recipient=c.owner_id)))
    _log(user, c, 'Complaint investigated', findings[:200])
    return c


@transaction.atomic
def decide(user, pk, decision, accepted_quantity, resolution, note):
    _need(user, 'view_complaint')
    c = _lock(user, pk)
    if c.status != 'investigating':
        raise ValidationError('Record the investigation findings first.')
    if user.pk in complaint_makers(c):
        raise ValidationError('The person who logged or investigated the complaint cannot decide it.')
    authorize(user,'decide_complaint',case_site(c),makers=complaint_makers(c),reference=c.number)
    note = (note or '').strip()
    if decision not in ('accept', 'partial', 'reject'):
        raise ValidationError('Choose the decision.')
    if not note:
        raise ValidationError('Explain the decision to the customer.')
    try:
        accepted_quantity=Decimal(str(accepted_quantity or 0))
        if decision!='reject' and (not accepted_quantity.is_finite() or accepted_quantity<0):raise ValueError
    except (ValueError,InvalidOperation,TypeError):
        raise ValidationError('Accepted quantity must be a finite, non-negative number.') from None
    if decision == 'reject':
        accepted_quantity, resolution = 0, 'none'
    elif c.quantity and accepted_quantity > c.quantity:
        raise ValidationError('Accepted quantity cannot exceed the quantity claimed.')
    if decision != 'reject' and resolution not in ('credit', 'replace', 'none'):
        raise ValidationError('Choose how it will be settled.')
    if resolution == 'credit' and not c.invoice_id:
        raise ValidationError('A credit note needs the complaint to be linked to an invoice.')
    c.decision, c.accepted_quantity, c.resolution, c.decision_note = decision, accepted_quantity, resolution, note[:300]
    c.decided_by, c.decided_at, c.status = user, timezone.now(), 'decided'
    c.save()
    _log(user, c, 'Complaint decided', f'{c.get_decision_display()} · {c.get_resolution_display() or ""} · {note[:150]}')
    return c


@transaction.atomic
def settle(user, pk, credit_note=None, order=None):
    _need(user, 'change_complaint')
    c = _lock(user, pk)
    if order:
        require_site(user, order.site)
    if c.status != 'decided':
        raise ValidationError('Settle a decided complaint.')
    if c.resolution == 'credit':
        if not credit_note or credit_note.invoice_id != c.invoice_id:
            raise ValidationError(f'Choose a credit note issued on invoice {c.invoice.number}.')
        if credit_note.complaints.exclude(pk=c.pk).exists():
            raise ValidationError(f'{credit_note.number} already settles another complaint.')
        c.credit_note = credit_note
    elif c.resolution == 'replace':
        if not order or order.kind != 'Sale' or order.partner_id != c.partner_id:
            raise ValidationError(f'Choose a sales order for {c.partner.name}.')
        c.replacement_order = order
    else:
        raise ValidationError('Nothing to settle for this decision.')
    c.save()
    _log(user, c, 'Complaint settled', credit_note.number if credit_note else order.number)
    return c


def closure_problems(c):
    out = []
    if c.status != 'decided':
        out.append('Decide the complaint first.')
    if c.resolution == 'credit' and not c.credit_note_id:
        out.append('Link the credit note given to the customer.')
    if c.resolution == 'replace' and not c.replacement_order_id:
        out.append('Link the replacement order.')
    if c.category == 'quality' and c.decision != 'reject':
        capas = list(c.capas.all())
        if capas and any(x.status != 'effective' for x in capas):
            out.append('Every CAPA must be verified effective.')
        if not capas and not c.capa_waiver:
            out.append('Add a CAPA, or record why none is needed.')
    return out


@transaction.atomic
def close(user, pk, waiver=''):
    _need(user, 'view_complaint')
    c = _lock(user, pk)
    if user.pk in complaint_makers(c):raise ValidationError('The complaint creator, owner or investigator cannot approve closure of their own work.')
    authorize(user,'decide_complaint',case_site(c),makers=complaint_makers(c),reference=c.number+':close')
    if waiver.strip() and not c.capas.exists():
        c.capa_waiver = waiver.strip()[:200]
    problems = closure_problems(c)
    if problems:
        raise ValidationError(problems)
    c.status, c.closed_by, c.closed_at = 'closed', user, timezone.now()
    c.save()
    _log(user, c, 'Complaint closed', f'{"within" if c.closed_at.date() <= c.due_on else "after"} SLA ({c.due_on:%d %b})')
    return c


# ---------- CAPA ----------

@transaction.atomic
def add_capa(user, complaint, title, root_cause, corrective, owner, due_on, containment='', preventive=''):
    _need(user, 'add_capaaction')
    if complaint:
        complaint = _lock(user, complaint.pk)
    if complaint and complaint.status == 'closed':
        raise ValidationError('The complaint is already closed.')
    if not all((s or '').strip() for s in (title, root_cause, corrective)):
        raise ValidationError('Title, root cause and corrective action are required.')
    if due_on < timezone.localdate():
        raise ValidationError('Due date cannot be in the past.')
    x = CapaAction.objects.create(complaint=complaint, title=title.strip()[:150], root_cause=root_cause.strip(), corrective=corrective.strip(), owner=owner, due_on=due_on,
                                  containment=(containment or '').strip(), preventive=(preventive or '').strip(), created_by=user)
    AuditEvent.objects.create(actor=user, action='CAPA opened', reference=f'CAPA-{x.pk:05d}', detail=(complaint.number + ' · ' if complaint else '') + f'owner {owner} · due {due_on}')
    return x


@transaction.atomic
def implement(user, pk, note):
    x = CapaAction.objects.select_for_update().get(pk=pk)
    _require_capa(user, x)
    if user.pk != x.owner_id and not user.has_perm('erp.change_capaaction'):
        raise PermissionDenied
    if x.status not in ('open', 'reopened'):
        raise ValidationError('Already implemented.')
    if not (note or '').strip():
        raise ValidationError('Describe what was done.')
    x.status, x.implementation_note, x.implemented_at = 'done', note.strip()[:300], timezone.now()
    x.save()
    AuditEvent.objects.create(actor=user, action='CAPA implemented', reference=f'CAPA-{x.pk:05d}', detail=note[:200])
    return x


@transaction.atomic
def verify(user, pk, effective, note):
    _need(user, 'view_complaint')
    _need(user, 'view_capaaction')
    x = CapaAction.objects.select_for_update().get(pk=pk)
    _require_capa(user, x)
    if x.status != 'done':
        raise ValidationError('Verify after the action is implemented.')
    if user.pk in capa_makers(x):
        raise ValidationError('The action owner cannot verify their own CAPA; creators and implementers also require an independent checker.')
    authorize(user,'verify_capaaction',case_site(x.complaint) if x.complaint_id else None,makers=capa_makers(x),reference=f'CAPA-{x.pk:05d}')
    if not (note or '').strip():
        raise ValidationError('Record the evidence of effectiveness (e.g. no repeat in 3 months, test results).')
    x.status, x.verified_by, x.verified_at, x.effectiveness_note = ('effective' if effective else 'reopened'), user, timezone.now(), note.strip()[:300]
    x.save()
    AuditEvent.objects.create(actor=user, action='CAPA verified' if effective else 'CAPA reopened', reference=f'CAPA-{x.pk:05d}', detail=note[:200])
    return x
