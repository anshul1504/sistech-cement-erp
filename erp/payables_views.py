"""Supplier invoice entry and three-way match, exception decisions, debit notes / supplier returns and supplier balances."""
from accounts.delegation import can_approve
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum, Count
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import payables as AP
from .models import SupplierInvoice, DebitNote, GoodsReceipt, Partner, AuditEvent, DEBIT_REASONS, GRNLine
from .services import permitted, scope


def _dec(value, default=None):
    text = str(value or '').replace(',', '').strip()
    if not text:
        return default
    return Decimal(text)


@login_required
def supplier_invoices(request):
    permitted(request.user, 'view_supplierinvoice')
    qs = scope(SupplierInvoice.objects.select_related('supplier', 'site'), request.user)
    status, q = request.GET.get('status', ''), request.GET.get('q', '').strip()
    if status: qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(invoice_number__icontains=q) | Q(number__icontains=q) | Q(supplier__name__icontains=q))
    base = scope(SupplierInvoice.objects.all(), request.user)
    today = timezone.localdate()
    # Suppliers with received material not yet billed
    pending = scope(GRNLine.objects.filter(grn__status='posted'), request.user, 'grn__site').values('grn__supplier', 'grn__supplier__name').annotate(n=Count('grn', distinct=True)).order_by('grn__supplier__name')
    to_bill = []
    for row in pending:
        s = Partner(pk=row['grn__supplier'], name=row['grn__supplier__name'])
        lines = [line for line in AP.billable_lines(s) if request.user.is_superuser or line.grn.site.siteaccess_set.filter(user=request.user).exists()]
        if lines:
            to_bill.append({'pk': s.pk, 'name': s.name, 'lines': len(lines), 'value': sum((l.open * l.po_line.rate for l in lines), Decimal('0'))})
    suppliers = []
    # Consolidated balances include cross-site receipts/payments and are finance-only.
    supplier_qs = Partner.objects.filter(kind='Supplier').order_by('name') if request.user.has_perm('erp.access_company_finance') else Partner.objects.none()
    from .models import PaymentAllocation
    for s in supplier_qs:
        b = AP.balance(s)
        due = Decimal('0')
        for invoice in SupplierInvoice.objects.filter(supplier=s, status__in=['matched', 'approved'], due_date__lt=today):
            paid = PaymentAllocation.objects.filter(supplier_invoice=invoice, payment__status='paid').aggregate(t=Sum('amount'))['t'] or 0
            due += max(invoice.payable - paid, 0)
        if b['balance'] or due:
            suppliers.append({'s': s, **b, 'overdue': due})
    return render(request, 'erp/supplier_invoices.html', {
        'title': 'Supplier invoices', 'invoices': qs[:200], 'status': status, 'q': q, 'to_bill': to_bill, 'suppliers': suppliers,
        'exceptions': base.filter(status='exception').count(),
        'payable': base.filter(status__in=['matched', 'approved']).aggregate(p=Sum('payable'))['p'] or 0,
        'debits': scope(DebitNote.objects.exclude(supplier_invoice__decision='matched'), request.user).aggregate(t=Sum('total'))['t'] or 0,
        'can_add': request.user.has_perm('erp.add_supplierinvoice'), 'statuses': SupplierInvoice._meta.get_field('status').choices})


@login_required
def sinv_new(request, supplier_pk):
    permitted(request.user, 'add_supplierinvoice')
    supplier = get_object_or_404(Partner, pk=supplier_pk, kind='Supplier')
    lines = [l for l in AP.billable_lines(supplier) if request.user.is_superuser or l.grn.site.siteaccess_set.filter(user=request.user).exists()]
    P = request.POST
    for l in lines:
        l.f = {'qty': P.get(f'qty_{l.pk}', ''), 'rate': P.get(f'rate_{l.pk}', l.po_line.rate), 'gst': P.get(f'gst_{l.pk}', l.po_line.gst_rate)}
    errors = []
    if request.method == 'POST':
        rows = {}
        try:
            for l in lines:
                qty = _dec(l.f['qty'])
                if qty:
                    rows[l] = (qty, _dec(l.f['rate']), _dec(l.f['gst']))
            header = {'invoice_number': P.get('invoice_number', ''), 'supplier_gstin': P.get('supplier_gstin', ''),
                      'billed_taxable': _dec(P.get('billed_taxable')), 'billed_tax': _dec(P.get('billed_tax')), 'billed_total': _dec(P.get('billed_total'))}
        except InvalidOperation:
            errors.append('Enter numbers only in quantities and amounts.')
        try:
            d = P.get('invoice_date')
            invoice_date = date.fromisoformat(d) if d else None
        except ValueError:
            invoice_date = None
            errors.append('Enter a valid invoice date.')
        if not errors:
            header['invoice_date'] = invoice_date
            try:
                inv = AP.create_invoice(request.user, supplier, header, rows, P.get('notes', ''))
            except ValidationError as e:
                errors += e.messages
            else:
                if inv.status == 'matched':
                    messages.success(request, f'{inv.invoice_number} matched with PO and GRN — ₹{AP.inr(inv.payable)} payable by {inv.due_date:%d %b %Y}.')
                else:
                    messages.warning(request, f'{inv.invoice_number} does not match ({len(inv.issues)} issue{"s" if len(inv.issues) > 1 else ""}). It is in the exception queue.')
                return redirect('sinv_detail', pk=inv.pk)
    return render(request, 'erp/sinv_new.html', {'title': f'Supplier invoice · {supplier.name}', 'supplier': supplier, 'lines': lines, 'errors': errors, 'post': P,
                                                 'today': timezone.localdate().isoformat()})


@login_required
def sinv_detail(request, pk):
    permitted(request.user, 'view_supplierinvoice')
    inv = get_object_or_404(scope(SupplierInvoice.objects.select_related('supplier', 'site', 'created_by', 'decided_by'), request.user), pk=pk)
    lines = inv.lines.select_related('grn_line__grn__order', 'grn_line__product')
    return render(request, 'erp/sinv_detail.html', {
        'title': f'Supplier invoice {inv.invoice_number}', 'inv': inv, 'lines': lines, 'debits': inv.debit_notes.all(),
        'history': AuditEvent.objects.filter(reference=inv.number).select_related('actor').order_by('-created_at')[:10],
        'can_decide': inv.status == 'exception' and can_approve(request.user, 'approve_supplierinvoice', inv.site, makers=(inv.created_by_id,)) and inv.created_by_id != request.user.pk,
        'matched_total': inv.matched_taxable + inv.matched_tax})


@login_required
@require_POST
def sinv_decide(request, pk, decision):
    get_object_or_404(scope(SupplierInvoice.objects.all(), request.user), pk=pk)
    try:
        inv = AP.decide(request.user, pk, decision, request.POST.get('note', ''))
        messages.success(request, f'{inv.invoice_number}: {inv.get_decision_display().lower()} — payable ₹{AP.inr(inv.payable)}.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('sinv_detail', pk=pk)


@login_required
def debit_notes(request):
    permitted(request.user, 'view_debitnote')
    qs = scope(DebitNote.objects.select_related('supplier', 'site', 'grn', 'supplier_invoice'), request.user)
    q = request.GET.get('q', '').strip()
    if q: qs = qs.filter(Q(number__icontains=q) | Q(supplier__name__icontains=q) | Q(grn__number__icontains=q))
    return render(request, 'erp/debit_notes.html', {'title': 'Debit notes', 'notes': qs[:200], 'q': q})


@login_required
def dn_new(request, grn_pk):
    permitted(request.user, 'add_debitnote')
    grn = get_object_or_404(scope(GoodsReceipt.objects.select_related('supplier', 'site', 'order'), request.user), pk=grn_pk)
    lines = list(grn.lines.select_related('product', 'po_line'))
    P = request.POST
    for l in lines:
        l.returnable = l.usable - AP.returned_qty(l)
        l.billed = AP.billed_qty(l)
        l.qty_in = P.get(f'qty_{l.pk}', '')
    errors = []
    if request.method == 'POST':
        try:
            qty = {l: _dec(l.qty_in) for l in lines}
        except InvalidOperation:
            qty = {}
            errors.append('Enter numbers only.')
        if not errors:
            try:
                dn = AP.create_debit_note(request.user, grn, P.get('reason', ''), P.get('remarks', ''), qty, P.get('vehicle', ''))
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{dn.number} raised for ₹{AP.inr(dn.total)}.' + (' Stock reduced.' if dn.reason == 'return' else ''))
                return redirect('dn_detail', pk=dn.pk)
    return render(request, 'erp/dn_new.html', {'title': f'Debit note · {grn.number}', 'grn': grn, 'lines': lines, 'errors': errors, 'post': P, 'reasons': DEBIT_REASONS})


@login_required
def dn_detail(request, pk):
    permitted(request.user, 'view_debitnote')
    dn = get_object_or_404(scope(DebitNote.objects.select_related('supplier', 'site', 'grn', 'supplier_invoice', 'created_by'), request.user), pk=pk)
    return render(request, 'erp/dn_detail.html', {'title': dn.number, 'dn': dn, 'lines': dn.lines.select_related('product', 'grn_line')})
