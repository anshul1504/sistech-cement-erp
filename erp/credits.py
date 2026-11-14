"""Credit note posting shared by the credit-note screen and returns (RMA).

picked: [(invoice_line, returned_qty, taxable_value)]. damaged: {invoice_line.pk: qty} — the part of a returned quantity that
comes back as damaged stock (from RMA inspection); the rest returns to available stock. Stock is valued at the original
dispatch cost (return_valuation). Posts the GL voucher, settles the invoice first and allocates any balance.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .gst import next_number, line_tax, money
from .ledger import allocate
from .models import CreditNote, CreditNoteLine, Settlement, AuditEvent
from .services import move_stock, permitted, require_site


@transaction.atomic
def create_credit_note(user, inv, reason, picked, notes='', key=None, fingerprint='', damaged=None, return_request=None):
    permitted(user, 'add_creditnote')
    from .finance_access import require_company_finance
    require_company_finance(user)
    from . import gl
    gl.lock_finance()
    from .models import Invoice, Partner, CREDIT_REASONS, ReturnRequest
    header = Invoice.objects.get(pk=inv.pk)
    require_site(user, header.site)
    Partner.objects.select_for_update().get(pk=header.partner_id)
    inv = Invoice.objects.select_for_update().get(pk=header.pk)
    gl.check_open(timezone.localdate())
    if key:
        from .submissions import replay
        existing = replay(CreditNote, user, key, fingerprint)
        if existing:
            if existing.invoice_id != inv.pk:
                raise ValidationError('The form belongs to another invoice.')
            return existing
    from .finance_views import return_valuation, credit_room
    from .returns import quantity
    damaged = damaged or {}
    if reason not in dict(CREDIT_REASONS) or not picked:
        raise ValidationError('Choose a valid reason and at least one credit line.')
    r = None
    if return_request:
        r = ReturnRequest.objects.select_for_update().get(pk=return_request.pk)
        if r.invoice_id != inv.pk or r.status != 'inspected' or r.credit_note_id or user.pk in [r.created_by_id, r.inspected_by_id]:
            raise ValidationError('An independently inspected return is required for this credit.')
        expected = {line.invoice_line_id: (line.good + line.damaged, line.damaged) for line in r.lines.all() if line.good + line.damaged > 0}
        if reason != 'return' or {l.pk for l, _, _ in picked} != set(expected):
            raise ValidationError('Credit lines must match the accepted return inspection.')
    elif damaged:
        raise ValidationError('Damaged stock quantities require an approved return inspection.')
    cleaned, seen = [], set()
    from django import forms
    amount_field = forms.DecimalField(max_digits=16, decimal_places=2, min_value=0)
    for original, raw_qty, raw_value in picked:
        if original.pk in seen:
            raise ValidationError('Each invoice line may be credited only once per note.')
        seen.add(original.pk)
        line = inv.lines.select_related('product').filter(pk=original.pk).first()
        if not line:
            raise ValidationError('Each credit line must belong to the invoice.')
        qty, value = quantity(raw_qty), amount_field.clean(raw_value)
        bad = quantity(damaged.get(line.pk, 0))
        room_value, room_qty = credit_room(line, exclude_return=r)
        if qty > room_qty or value > room_value or bad > qty or (reason != 'return' and qty):
            raise ValidationError('Credit exceeds the remaining invoice value/quantity or reserved returns.')
        if r and ((qty, bad) != expected[line.pk] or value != min(money(qty * line.rate), room_value)):
            raise ValidationError('Credit must match the accepted inspection and remaining taxable room.')
        if not qty and not value:
            raise ValidationError('Credit lines require a quantity or taxable value.')
        cleaned.append((line, qty, value))
    if set(damaged) - seen:
        raise ValidationError('Damaged quantities must belong to selected credit lines.')
    picked = cleaned
    cn = CreditNote(invoice=inv, partner=inv.partner, reason=reason, notes=(notes or '')[:200], created_by=user,
                    returns_stock=reason == 'return' and any(q for _, q, _ in picked), taxable=0, cgst=0, sgst=0, igst=0, round_off=0, total=0)
    cn.number = next_number('CN', inv.site)
    cn.submission_key, cn.submission_fingerprint = key, fingerprint
    cn.save()
    for l, qty, value in picked:
        c, s, i = line_tax(value, l.gst_rate, inv.interstate)
        CreditNoteLine.objects.create(credit_note=cn, invoice_line=l, quantity=qty, taxable=value, gst_rate=l.gst_rate, cgst=c, sgst=s, igst=i)
        cn.taxable += value; cn.cgst += c; cn.sgst += s; cn.igst += i
        if qty and cn.returns_stock:
            bad = min(Decimal(damaged.get(l.pk, 0)), qty)
            for part, status, suffix in ((qty - bad, 'available', ''), (bad, 'damaged', ':damaged')):
                if part > 0:
                    cost, stock_value = return_valuation(inv, l, part)
                    move_stock(user, inv.site, l.product, part, f'cn:{cn.pk}:line:{l.pk}{suffix}', 'Return', f'{cn.number} against {inv.number}',
                               status=status, unit_cost=cost, value=stock_value)
    exact = cn.taxable + cn.cgst + cn.sgst + cn.igst
    cn.total = exact.quantize(Decimal('1'))
    cn.round_off = cn.total - exact
    cn.save()
    from .gl import post_credit_note
    post_credit_note(user, cn)
    # A credit note first reduces its own invoice; anything left settles other invoices or stays as credit.
    own = min(cn.total, inv.outstanding)
    if own > 0:
        Settlement.objects.create(invoice=inv, credit_note=cn, amount=own)
    allocate(inv.partner)
    AuditEvent.objects.create(actor=user, site=inv.site, action='Credit note posted', reference=cn.number, detail=f'{inv.number} · {cn.get_reason_display()} · ₹{cn.total}')
    return cn
