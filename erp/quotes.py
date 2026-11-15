"""Quotations (SRS §5.2, FR-SLS-002): price offer with GST, discount approval, revisions and conversion to a sales order.

Controls:
- the list price comes from the price list; an offered rate below it needs approval by a different user before the quotation
  can be sent (price exception, SRS approval matrix);
- a sent quotation is never edited: any change is a new revision linked to the previous one, which is marked revised;
- an expired quotation cannot be accepted; only an accepted quotation for a registered customer converts, once, into a draft
  sales order that still goes through the normal credit / approval workflow.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .services import require_site
from .gst import next_number, summarise, inr, qtxt, price_for
from .models import Quotation, QuotationLine, Order, OrderLine, AuditEvent, CUSTOMER_KINDS

ZERO = Decimal('0')


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, q, action, detail=''):
    AuditEvent.objects.create(actor=user, site=q.site, action=action, reference=q.number, detail=detail[:250])


def totals(q):
    return summarise(list(q.lines.select_related('product')), q.interstate)


@transaction.atomic
def save(user, data, lines, q=None):
    """data: site, partner, lead, date, valid_until, place_of_supply, freight_terms, delivery_terms, payment_terms, notes.
    lines: [(product, qty, rate or None)]."""
    _need(user, 'change_quotation' if q else 'add_quotation')
    require_site(user, data.get('site'))
    if q:
        q = _lock(user, q.pk)
    if data.get('lead'):
        require_site(user, data['lead'].site)
    problems = []
    if q and q.status != 'draft':
        problems.append('Only drafts can be changed — prepare a revision instead.')
    partner, lead = data.get('partner'), data.get('lead')
    if bool(partner) == bool(lead):
        problems.append('Choose either a customer or a CRM prospect.')
    if partner and partner.kind not in CUSTOMER_KINDS:
        problems.append('Quotations are for customers.')
    if lead and lead.partner_id:
        partner, lead = lead.partner, None  # the prospect is already a customer
    if not data.get('valid_until') or data['valid_until'] < data.get('date', timezone.localdate()):
        problems.append('Valid-until cannot be before the quotation date.')
    if not data.get('place_of_supply'):
        problems.append('Choose the place of supply (state).')
    clean, seen = [], set()
    for product, qty, rate in lines:
        qty = Decimal(qty or 0)
        if qty <= 0:
            continue
        if product.pk in seen:
            problems.append(f'{product.name} is listed twice.')
        seen.add(product.pk)
        if product.gst_rate is None:
            problems.append(f'{product.name} has no GST rate.')
        rule = price_for(product, data['site'], partner, qty)
        rate = Decimal(rate) if rate not in (None, '') else (rule.rate if rule else None)
        if rate is None or rate <= 0:
            problems.append(f'{product.name}: no list price — enter the offered rate.')
        clean.append((product, qty, rate, rule.rate if rule else None))
    if not clean:
        problems.append('Add at least one item.')
    if problems:
        raise ValidationError(problems)
    fields = {k: data.get(k, '') for k in ('date', 'valid_until', 'place_of_supply', 'freight_terms', 'delivery_terms', 'payment_terms', 'notes')}
    if q:
        for k, v in fields.items():
            setattr(q, k, v)
        q.site, q.partner, q.lead = data['site'], partner, lead
        q.save()
        q.lines.all().delete()
    else:
        q = Quotation.objects.create(number=next_number('QT', data['site']), site=data['site'], partner=partner, lead=lead, created_by=user, **fields)
    for product, qty, rate, listed in clean:
        QuotationLine.objects.create(quotation=q, product=product, quantity=qty, rate=rate, list_rate=listed, gst_rate=product.gst_rate, hsn=product.hsn)
    _log(user, q, 'Quotation saved', f'rev {q.revision} · {q.customer_name} · ₹{inr(totals(q)["grand_total"])}')
    return q


def _lock(user, pk):
    q = Quotation.objects.select_for_update().select_related('site', 'partner', 'lead').get(pk=pk)
    require_site(user, q.site)
    return q


@transaction.atomic
def submit(user, pk):
    _need(user, 'change_quotation')
    q = _lock(user, pk)
    if q.status != 'draft':
        raise ValidationError('Only drafts can be submitted.')
    below = [l for l in q.lines.select_related('product') if l.below_list]
    q.status = 'approval' if below else 'ready'
    q.save(update_fields=['status'])
    _log(user, q, 'Quotation submitted', ('Discount approval needed: ' + ', '.join(f'{l.product.code} {l.discount_pct}% below list' for l in below)) if below else 'At list price')
    return q


@transaction.atomic
def approve(user, pk, ok, note=''):
    _need(user, 'approve_quotation')
    q = _lock(user, pk)
    if q.status != 'approval':
        raise ValidationError('This quotation is not waiting for price approval.')
    if q.created_by_id == user.pk:
        raise ValidationError('The person who prepared the quotation cannot approve its discount (maker-checker).')
    note = (note or '').strip()
    if not ok and not note:
        raise ValidationError('Give the reason for sending it back.')
    q.status, q.approved_by, q.approval_note = ('ready' if ok else 'draft'), (user if ok else None), note[:200]
    q.save()
    _log(user, q, 'Quotation discount approved' if ok else 'Quotation sent back', note)
    return q


@transaction.atomic
def mark_sent(user, pk):
    _need(user, 'change_quotation')
    q = _lock(user, pk)
    if q.status != 'ready':
        raise ValidationError('Only approved / ready quotations can be sent.')
    q.status = 'sent'
    q.save(update_fields=['status'])
    _log(user, q, 'Quotation sent', q.customer_name)
    return q


@transaction.atomic
def outcome(user, pk, accepted, note=''):
    _need(user, 'change_quotation')
    q = _lock(user, pk)
    if q.status != 'sent':
        raise ValidationError('Record the outcome of a sent quotation.')
    if accepted and q.valid_until < timezone.localdate():
        q.status = 'expired'
        q.save(update_fields=['status'])
        raise ValidationError(f'This quotation expired on {q.valid_until:%d %b %Y}. Prepare a revision with fresh prices.')
    q.status, q.outcome_note = ('accepted' if accepted else 'rejected'), (note or '')[:200]
    q.save()
    _log(user, q, 'Quotation accepted' if accepted else 'Quotation declined', note)
    return q


@transaction.atomic
def revise(user, pk):
    _need(user, 'add_quotation')
    old = _lock(user, pk)
    if old.status in ('draft', 'converted', 'revised'):
        raise ValidationError('This quotation cannot be revised.')
    if hasattr(old, 'next_revision'):
        raise ValidationError('A revision already exists.')
    today = timezone.localdate()
    new = Quotation.objects.create(number=old.number, revision=old.revision + 1, previous=old, site=old.site, partner=old.partner, lead=old.lead, date=today,
                                   valid_until=max(old.valid_until, today + timedelta(days=7)), place_of_supply=old.place_of_supply, freight_terms=old.freight_terms,
                                   delivery_terms=old.delivery_terms, payment_terms=old.payment_terms, notes=old.notes, created_by=user)
    for l in old.lines.all():
        QuotationLine.objects.create(quotation=new, product=l.product, quantity=l.quantity, rate=l.rate, list_rate=l.list_rate, gst_rate=l.gst_rate, hsn=l.hsn)
    old.status = 'revised'
    old.save(update_fields=['status'])
    _log(user, new, 'Quotation revised', f'rev {old.revision} → rev {new.revision}')
    return new


@transaction.atomic
def convert(user, pk):
    if not user.has_perm('erp.create_sales_order'):
        raise PermissionDenied
    q = _lock(user, pk)
    if q.status != 'accepted':
        raise ValidationError('Only accepted quotations become sales orders.')
    partner = q.partner or (q.lead.partner if q.lead_id and q.lead.partner_id else None)
    if not partner:
        raise ValidationError('The prospect is not a customer yet — complete dealer onboarding first.')
    order = Order.objects.create(number=next_number('SO', q.site), kind='Sale', site=q.site, partner=partner, created_by=user, place_of_supply=q.place_of_supply,
                                 freight_terms=q.freight_terms, customer_ref=f'{q.number} rev {q.revision}'[:40], due_date=timezone.localdate() + timedelta(days=7),
                                 notes=f'From quotation {q.number} rev {q.revision}.' + (f' {q.delivery_terms}' if q.delivery_terms else ''))
    for l in q.lines.all():
        OrderLine.objects.create(order=order, product=l.product, quantity=l.quantity, rate=l.rate, list_rate=l.list_rate, gst_rate=l.gst_rate, hsn=l.hsn)
    q.status, q.order, q.partner = 'converted', order, partner
    q.save()
    _log(user, q, 'Quotation converted', order.number)
    AuditEvent.objects.create(actor=user, site=q.site, action='Order created', reference=order.number, detail=f'From quotation {q.number} rev {q.revision}')
    return order
