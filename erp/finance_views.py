"""Receivables: receipts (with cheque bounce), GST credit notes (with goods return), customer ledger and outstanding / ageing."""
import csv
import re
from uuid import uuid4
from .gst import inr
from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .gst import next_number, line_tax, money, in_words, qtxt
from .ledger import allocate, balance, open_invoices, ageing, ledger_rows, exposure, credit_unapplied, AGE_BUCKETS
from .models import (Receipt, CreditNote, CreditNoteLine, Invoice, InvoiceLine, Partner, Settlement, AuditEvent, StockMovement, Organization,
                     RECEIPT_MODES, CREDIT_REASONS, CASH_LIMIT, CUSTOMER_KINDS, INDIAN_STATES)
from .services import permitted, scope, move_stock
from .finance_access import require_company_finance
from .submissions import metadata, replay
from . import gl


def _styled(form):
    for f in form.fields.values():
        f.widget.attrs.setdefault('class', 'form-select' if isinstance(f.widget, forms.Select) else 'form-check-input' if isinstance(f.widget, forms.CheckboxInput) else 'form-control')
    return form


# ---------- Receipts ----------

class ReceiptForm(forms.ModelForm):
    submission_key = forms.UUIDField(required=False, initial=uuid4, widget=forms.HiddenInput)
    class Meta:
        model = Receipt
        fields = ['partner', 'date', 'amount', 'mode', 'reference', 'instrument_date', 'drawn_on', 'deposited_to', 'notes']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'}), 'instrument_date': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['partner'].queryset = Partner.objects.filter(kind__in=CUSTOMER_KINDS).order_by('name')
        self.fields['partner'].empty_label = 'Choose customer'
        self.fields['amount'].widget.attrs.update(step='0.01', min='0')
        _styled(self)

    def clean(self):
        data = super().clean()
        mode, ref, amount, on = data.get('mode'), (data.get('reference') or '').strip(), data.get('amount'), data.get('date')
        if on and on > timezone.localdate():
            self.add_error('date', 'Receipt date cannot be in the future.')
        if mode in ('neft', 'upi', 'cheque', 'dd') and not ref:
            self.add_error('reference', 'Enter the UTR / transaction ID or cheque number.')
        if mode in ('cheque', 'dd') and not data.get('instrument_date'):
            self.add_error('instrument_date', 'Enter the cheque / DD date.')
        if mode == 'cash' and amount and data.get('partner') and on:
            same_day = Receipt.objects.filter(partner=data['partner'], date=on, mode='cash', status='posted').aggregate(t=Sum('amount'))['t'] or 0
            if amount + same_day >= CASH_LIMIT:
                self.add_error('amount', f'Cash of ₹2,00,000 or more from one person in a day is not allowed (Income-tax s.269ST). Already received in cash today: ₹{inr(same_day, 0)}.')
        if ref and data.get('partner') and Receipt.objects.filter(partner=data['partner'], reference__iexact=ref, mode=mode).exclude(status='bounced').exists():
            self.add_error('reference', 'This reference is already recorded for this customer — possible duplicate.')
        data['reference'] = ref
        return data


@login_required
def receipts(request):
    require_company_finance(request.user)
    permitted(request.user, 'view_receipt')
    qs = Receipt.objects.select_related('partner')
    q, mode, status = request.GET.get('q', '').strip(), request.GET.get('mode', ''), request.GET.get('status', '')
    if q: qs = qs.filter(Q(number__icontains=q) | Q(partner__name__icontains=q) | Q(reference__icontains=q))
    if mode in dict(RECEIPT_MODES): qs = qs.filter(mode=mode)
    if status in ('posted', 'bounced'): qs = qs.filter(status=status)
    today = timezone.localdate()
    month = Receipt.objects.filter(status='posted', date__year=today.year, date__month=today.month)
    stats = {'month': month.aggregate(t=Sum('amount'))['t'] or 0, 'month_n': month.count(),
             'advance': sum((r.unallocated for r in Receipt.objects.filter(status='posted').prefetch_related('settlements')), Decimal('0')),
             'bounced': Receipt.objects.filter(status='bounced').count()}
    return render(request, 'erp/receipts.html', {'title': 'Receipts', 'page': Paginator(qs, 50).get_page(request.GET.get('page')), 'stats': stats,
                                                 'filters': {'q': q, 'mode': mode, 'status': status}, 'modes': RECEIPT_MODES,
                                                 'can_add': request.user.has_perm('erp.add_receipt')})


@login_required
@transaction.atomic
def receipt_new(request):
    require_company_finance(request.user)
    permitted(request.user, 'add_receipt')
    initial = {}
    if request.GET.get('partner', '').isdigit():
        initial['partner'] = request.GET['partner']
    form = ReceiptForm(request.POST or None, initial=initial)
    if request.method == 'POST':
        gl.lock_finance()
        try:
            key, fingerprint = metadata(request)
            # Serialize retry/reference checks and allocations for the customer.
            raw_partner = request.POST.get('partner', '')
            if raw_partner.isdigit():
                Partner.objects.select_for_update().filter(pk=raw_partner).first()
            existing = replay(Receipt, request.user, key, fingerprint)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            if existing:
                messages.info(request, f'{existing.number} was already posted; the original receipt is shown.')
                return redirect('receipt_detail', pk=existing.pk)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        r = form.save(commit=False)
                        r.number, r.created_by = next_number('RCT'), request.user
                        r.submission_key, r.submission_fingerprint = key, fingerprint
                        r.save()
                        from .gl import post_receipt
                        post_receipt(request.user, r)
                        allocate(r.partner)
                        AuditEvent.objects.create(actor=request.user, action='Receipt posted', reference=r.number, detail=f'{r.partner.name} · ₹{r.amount} · {r.get_mode_display()}')
                except ValidationError as e:
                    form.add_error(None, e)
                else:
                    r.refresh_from_db()
                    messages.success(request, f'{r.number}: ₹{inr(r.amount, 2)} received — ₹{inr(r.allocated, 2)} adjusted against invoices' + (f', ₹{inr(r.unallocated, 2)} kept as advance.' if r.unallocated else '.'))
                    return redirect('receipt_detail', pk=r.pk)
    pk = initial.get('partner') or request.POST.get('partner') or ''
    partner = form.fields['partner'].queryset.filter(pk=pk).first() if str(pk).isdigit() else None
    bal = balance(partner) if partner else None
    return render(request, 'erp/receipt_form.html', {'title': 'Record receipt', 'form': form, 'partner': partner, 'open': open_invoices(partner) if partner else [],
                                                     'balance': bal, 'advance': -bal if bal is not None and bal < 0 else 0})


@login_required
def receipt_detail(request, pk):
    require_company_finance(request.user)
    permitted(request.user, 'view_receipt')
    r = get_object_or_404(Receipt.objects.select_related('partner', 'created_by'), pk=pk)
    return render(request, 'erp/receipt_detail.html', {'title': r.number, 'r': r, 'settlements': r.settlements.select_related('invoice'),
                                                       'words': in_words(r.amount), 'can_bounce': request.user.has_perm('erp.bounce_receipt')})


@login_required
@require_POST
def receipt_bounce(request, pk):
    require_company_finance(request.user)
    """Cheque / DD returned unpaid: undo its settlements, show it as a debit on the ledger, re-settle what is left."""
    permitted(request.user, 'bounce_receipt')
    try:
        with transaction.atomic():
            gl.lock_finance()
            r = get_object_or_404(Receipt.objects.select_for_update(), pk=pk)
            if r.mode not in ('cheque', 'dd') or r.status != 'posted':
                messages.error(request, 'Only a posted cheque or DD can be marked as bounced.')
                return redirect('receipt_detail', pk=pk)
            reason = (request.POST.get('reason') or 'Returned unpaid').strip()[:120]
            r.settlements.all().delete()
            r.status, r.bounced_on, r.bounce_reason = 'bounced', timezone.localdate(), reason
            r.save(update_fields=['status', 'bounced_on', 'bounce_reason'])
            from .gl import reverse_source
            reverse_source(request.user, f'rct:{r.pk}', reason=f'Cheque bounced: {reason}')
            allocate(r.partner)
            AuditEvent.objects.create(actor=request.user, action='Cheque bounced', reference=r.number, detail=f'{r.partner.name} · ₹{r.amount} · {reason}')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
        return redirect('receipt_detail', pk=pk)
    messages.warning(request, f'{r.number} marked bounced. ₹{inr(r.amount, 2)} is outstanding again for {r.partner.name}.')
    return redirect('receipt_detail', pk=pk)


# ---------- Credit notes ----------

def credit_room(line, exclude_return=None):
    """How much taxable value and quantity can still be credited on one invoice line."""
    done = line.credit_lines.aggregate(t=Sum('taxable'), q=Sum('quantity'))
    from .returns import reserved
    return max(line.taxable - (done['t'] or 0), Decimal(0)), max(line.quantity - (done['q'] or 0) - reserved(line, exclude_return), Decimal(0))


def return_valuation(invoice, line, quantity):
    """Allocate actual dispatch value; the final return restores the remaining paisa."""
    prefix = f'dispatch:{invoice.dispatch_id}:line:' if invoice.dispatch_id else f'order:{invoice.order_id}:line:'
    originals = StockMovement.objects.filter(site=invoice.site, product=line.product, kind='Dispatch', source__startswith=prefix)
    total_qty = sum((abs(m.quantity) for m in originals), Decimal(0))
    total_value = sum((abs(m.value) for m in originals), Decimal(0))
    if not total_qty:
        return None, None  # Legacy records without a dispatch-cost snapshot.
    product_qty = invoice.lines.filter(product=line.product).aggregate(t=Sum('quantity'))['t'] or 0
    budget = (total_value * product_qty / total_qty).quantize(Decimal('.01'))
    sources = [f'cn:{cn_id}:line:{line_id}{suffix}' for cn_id, line_id in CreditNoteLine.objects.filter(
        invoice_line__invoice=invoice, invoice_line__product=line.product, credit_note__returns_stock=True).values_list('credit_note_id', 'invoice_line_id')
        for suffix in ('', ':damaged')]
    returned = StockMovement.objects.filter(source__in=sources, kind='Return')
    done_qty = sum((m.quantity for m in returned), Decimal(0))
    done_value = sum((m.value for m in returned), Decimal(0))
    value = budget - done_value if done_qty + quantity == product_qty else (total_value * quantity / total_qty).quantize(Decimal('.01'))
    return total_value / total_qty, min(max(value, Decimal(0)), max(budget - done_value, Decimal(0)))


@login_required
@transaction.atomic
def credit_note_new(request, pk):
    permitted(request.user, 'add_creditnote')
    inv = get_object_or_404(scope(Invoice.objects.select_related('partner', 'site'), request.user), pk=pk)
    rows, errors = [], []
    key, fingerprint = None, ''
    if request.method == 'POST':
        gl.lock_finance()
        Partner.objects.select_for_update().get(pk=inv.partner_id)
        inv = Invoice.objects.select_for_update().get(pk=inv.pk)
        try:
            key, fingerprint = metadata(request)
            existing = replay(CreditNote, request.user, key, fingerprint)
            if existing:
                messages.info(request, f'{existing.number} was already posted; the original credit note is shown.')
                return redirect('credit_note_detail', pk=existing.pk)
        except ValidationError as e:
            errors += e.messages
    lines = list(inv.lines.select_related('product'))
    reason = request.POST.get('reason', 'rate')
    for l in lines:
        room_value, room_qty = credit_room(l)
        row = {'line': l, 'room_value': room_value, 'room_qty': room_qty, 'qty': request.POST.get(f'qty_{l.pk}', ''), 'value': request.POST.get(f'value_{l.pk}', '')}
        rows.append(row)
    if request.method == 'POST':
        if reason not in dict(CREDIT_REASONS):
            errors.append('Choose a reason.')
        picked = []
        for row in rows:
            l = row['line']
            try:
                qty = Decimal(row['qty'] or '0')
                value = money(row['value'] or '0')
                if not qty.is_finite() or not value.is_finite():
                    raise InvalidOperation
            except InvalidOperation:
                errors.append(f'{l.description}: enter numbers only.'); continue
            if reason == 'return' and qty:
                if qty < 0 or qty > row['room_qty']:
                    errors.append(f'{l.description}: returned quantity must be between 0 and {qtxt(row["room_qty"])} {l.unit}.'); continue
                value = value or money(qty * l.rate)
            elif qty:
                qty = Decimal('0')
            if value < 0 or value > row['room_value']:
                errors.append(f'{l.description}: credit must be between ₹0 and ₹{inr(row["room_value"], 2)} (taxable).'); continue
            if value:
                picked.append((l, qty, value))
        if not picked and not errors:
            errors.append('Enter a quantity or value on at least one line.')
        if not errors:
            try:
                from .credits import create_credit_note
                cn = create_credit_note(request.user, inv, reason, picked, request.POST.get('notes', ''), key, fingerprint)
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'Credit note {cn.number} for ₹{inr(cn.total, 0)} posted against {inv.number}.' + (' Returned stock added back.' if cn.returns_stock else ''))
                return redirect('credit_note_detail', pk=cn.pk)
    return render(request, 'erp/credit_note_form.html', {'title': f'Credit note · {inv.number}', 'inv': inv, 'rows': rows, 'errors': errors,
                                                         'submission_key': request.POST.get('submission_key') or uuid4(), 'reasons': CREDIT_REASONS, 'reason': reason, 'notes': request.POST.get('notes', '')})


@login_required
def credit_notes(request):
    permitted(request.user, 'view_creditnote')
    qs = scope(CreditNote.objects.select_related('partner', 'invoice'), request.user, 'invoice__site')
    q = request.GET.get('q', '').strip()
    if q: qs = qs.filter(Q(number__icontains=q) | Q(partner__name__icontains=q) | Q(invoice__number__icontains=q))
    return render(request, 'erp/credit_notes.html', {'title': 'Credit notes', 'page': Paginator(qs, 50).get_page(request.GET.get('page')), 'q': q,
                                                     'totals': qs.aggregate(t=Sum('total'), x=Sum('taxable'))})


@login_required
def credit_note_detail(request, pk):
    permitted(request.user, 'view_creditnote')
    cn = get_object_or_404(scope(CreditNote.objects.select_related('invoice', 'partner', 'created_by', 'invoice__site'), request.user, 'invoice__site'), pk=pk)
    return render(request, 'erp/credit_note_detail.html', {'title': cn.number, 'cn': cn, 'inv': cn.invoice, 'lines': cn.lines.select_related('invoice_line'),
                                                           'company': Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization(),
                                                           'words': in_words(cn.total), 'states': dict(INDIAN_STATES), 'unapplied': credit_unapplied(cn)})


# ---------- Ledger & outstanding ----------

@login_required
def partner_ledger(request, pk, self_service=False):
    if self_service:
        from .models import DealerLedgerAccess
        access = get_object_or_404(DealerLedgerAccess, user=request.user, active=True, partner__active=True, partner__kind='Dealer')
        pk = access.partner_id
    else:
        require_company_finance(request.user)
        permitted(request.user, 'view_receipt')
    partner = get_object_or_404(Partner, pk=pk, kind__in=CUSTOMER_KINDS)
    def parse(value):
        try:
            return date.fromisoformat(value) if value else None
        except ValueError:
            messages.error(request, 'Choose a valid date for the ledger filter.')
            return None
    start, end = parse(request.GET.get('from')), parse(request.GET.get('to'))
    opening, rows = ledger_rows(partner, start, end)
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="ledger-{partner.code}.csv"'
        response.write('﻿')
        w = csv.writer(response)
        w.writerow(['Date', 'Type', 'Reference', 'Details', 'Debit', 'Credit', 'Balance'])
        w.writerow([start or '', 'Opening balance', '', '', '', '', opening])
        for r in rows:
            w.writerow([r['date'].strftime('%d-%m-%Y'), r['kind'], r['ref'], r['note'], r['debit'] or '', r['credit'] or '', r['balance']])
        return response
    invs = open_invoices(partner)
    from .partner_views import credit_position
    return render(request, 'erp/my_ledger.html' if self_service else 'erp/ledger.html', {'title': f'Ledger · {partner.name}', 'partner': partner, 'opening': opening, 'rows': rows,
                                               'closing': rows[-1]['balance'] if rows else opening, 'open': invs, 'age': ageing(invs), 'buckets': AGE_BUCKETS,
                                               'credit': credit_position(partner), 'filters': {'from': request.GET.get('from', ''), 'to': request.GET.get('to', '')},
                                               'debits': sum((r['debit'] for r in rows), Decimal('0')), 'credits': sum((r['credit'] for r in rows), Decimal('0')),
                                               'can_receive': request.user.has_perm('erp.add_receipt')})


@login_required
def outstanding(request):
    require_company_finance(request.user)
    permitted(request.user, 'view_receipt')
    invs = open_invoices()
    by_partner = {}
    for inv in invs:
        by_partner.setdefault(inv.partner, []).append(inv)
    rows = []
    for partner, items in by_partner.items():
        age = ageing(items)
        rows.append({'partner': partner, 'age': age, 'count': len(items), 'oldest': items[0], 'limit': partner.credit_limit})
    advances = []
    for partner in Partner.objects.filter(kind__in=CUSTOMER_KINDS):
        b = balance(partner)
        if b < 0:
            advances.append((partner, -b))
    sort = request.GET.get('sort', 'overdue')
    rows.sort(key=lambda r: -(r['age']['overdue'] if sort == 'overdue' else r['age']['total']))
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="outstanding-{timezone.localdate():%Y%m%d}.csv"'
        response.write('﻿')
        w = csv.writer(response)
        w.writerow(['Customer', 'Code', 'District', 'Invoices'] + [label for _, label in AGE_BUCKETS] + ['Total', 'Credit limit'])
        for r in rows:
            w.writerow([r['partner'].name, r['partner'].code, r['partner'].district, r['count']] + [r['age'][k] for k, _ in AGE_BUCKETS] + [r['age']['total'], r['limit']])
        return response
    total = ageing(invs)
    return render(request, 'erp/outstanding.html', {'title': 'Outstanding & ageing', 'rows': rows, 'total': total, 'buckets': AGE_BUCKETS, 'sort': sort,
                                                    'advances': advances, 'can_receive': request.user.has_perm('erp.add_receipt')})


@login_required
def my_ledger(request):
    return partner_ledger(request, None, self_service=True)
