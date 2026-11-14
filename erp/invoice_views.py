"""GST tax invoices: post from a dispatched sales order, register with filters, printable A4 invoice, GSTR-1 style CSV."""
import csv
import re
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from accounts.roles import order_permission
from .gst import next_number, summarise, in_words
from .models import Invoice, InvoiceLine, Order, Organization, Partner, Site, AuditEvent, INDIAN_STATES
from .services import scope, permitted


class TransportForm(forms.Form):
    vehicle_number = forms.CharField(label='Vehicle no.', max_length=20, required=False, widget=forms.TextInput(attrs={'placeholder': 'e.g. MP04 GA 1234', 'style': 'text-transform:uppercase'}))
    transporter = forms.CharField(max_length=120, required=False)
    lr_number = forms.CharField(label='LR / GR no.', max_length=30, required=False)
    eway_bill = forms.CharField(label='E-way bill no.', max_length=16, required=False, help_text='12 digits. Required when the consignment value is above ₹50,000.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs['class'] = 'form-control'

    def clean_vehicle_number(self):
        v = re.sub(r'[\s-]', '', self.cleaned_data['vehicle_number'].upper())
        if v and not re.fullmatch(r'[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}', v):
            raise forms.ValidationError('Enter a valid vehicle number, e.g. MP04GA1234.')
        return v

    def clean_eway_bill(self):
        v = self.cleaned_data['eway_bill'].replace(' ', '')
        if v and not re.fullmatch(r'\d{12}', v):
            raise forms.ValidationError('E-way bill number has 12 digits.')
        return v


def invoice_blockers(order):
    """Reasons a GST invoice cannot be posted yet (empty list = ready)."""
    org = Organization.objects.defer('logo', 'icon').filter(pk=1).first()
    out = []
    if order.kind != 'Sale': out.append('Only sales orders are invoiced.')
    if order.status != 'Completed': out.append('Dispatch and post the order first.')
    if hasattr(order, 'invoice'): out.append(f'Already invoiced as {order.invoice.number}.')
    if not (order.site.gstin or (org and org.gstin)): out.append(f'Set the GSTIN for {order.site.code} (Masters → Plants & depots) or the company (Organisation setup).')
    if not order.seller_state: out.append(f'Set the state of {order.site.code}.')
    if not order.supply_state: out.append(f'Set the state of {order.partner.name} or the place of supply on the order.')
    if any(l.gst_rate is None for l in order.lines.all()): out.append('A line has no GST rate.')
    return out


@transaction.atomic
def post_invoice(user, order_pk, transport):
    order = Order.objects.select_for_update().select_related('site', 'partner').get(pk=order_pk)
    problems = invoice_blockers(order)
    if problems:
        raise ValidationError(problems)
    org = Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization()
    lines = list(order.lines.select_related('product'))
    tax = summarise(lines, order.is_interstate)
    today = timezone.localdate()
    site, partner = order.site, order.partner
    seller_address = site.full_address or org.full_address
    inv = Invoice.objects.create(
        number=next_number('INV', site, today), date=today, order=order, site=site, partner=partner,
        seller_name=org.legal_name, seller_gstin=site.gstin or org.gstin, seller_state=order.seller_state, seller_address=seller_address,
        buyer_name=partner.name, buyer_gstin=partner.gstin, buyer_state=partner.state, buyer_address=partner.full_address,
        ship_to=order.ship_to or partner.shipping_address or '', place_of_supply=order.supply_state, interstate=order.is_interstate,
        taxable=tax['taxable'], cgst=tax['cgst'], sgst=tax['sgst'], igst=tax['igst'], round_off=tax['round_off'], grand_total=tax['grand_total'],
        due_date=today + timedelta(days=partner.payment_terms_days or 0), created_by=user, **transport)
    for row in tax['rows']:
        l = row['line']
        InvoiceLine.objects.create(invoice=inv, product=l.product, description=l.product.name, hsn=l.hsn, unit=l.product.unit, quantity=l.quantity, rate=l.rate,
                                   taxable=row['taxable'], gst_rate=l.gst_rate, cgst=row['cgst'], sgst=row['sgst'], igst=row['igst'], total=row['total'])
    AuditEvent.objects.create(actor=user, site=site, action='Invoice posted', reference=inv.number, detail=f'{order.number} · ₹{inv.grand_total}')
    AuditEvent.objects.create(actor=user, site=site, action='Order invoiced', reference=order.number, detail=inv.number)
    return inv


@login_required
def invoice_new(request, pk):
    permitted(request.user, 'post_invoice')
    order = get_object_or_404(scope(Order.objects.select_related('site', 'partner'), request.user), pk=pk)
    blockers = invoice_blockers(order)
    if hasattr(order, 'invoice'):
        return redirect('invoice_detail', pk=order.invoice.pk)
    form = TransportForm(request.POST or None)
    lines = list(order.lines.select_related('product'))
    tax = summarise(lines, order.is_interstate)
    if request.method == 'POST' and not blockers and form.is_valid():
        try:
            inv = post_invoice(request.user, order.pk, form.cleaned_data)
        except ValidationError as e:
            form.add_error(None, ' '.join(e.messages))
        else:
            messages.success(request, f'Tax invoice {inv.number} posted for ₹{inv.grand_total:,.0f}.')
            return redirect('invoice_detail', pk=inv.pk)
    return render(request, 'erp/invoice_new.html', {'title': f'Invoice {order.number}', 'order': order, 'form': form, 'blockers': blockers, 'tax': tax})


def _register(request):
    qs = scope(Invoice.objects.select_related('partner', 'site'), request.user)
    f = {k: request.GET.get(k, '').strip() for k in ['q', 'site', 'type', 'from', 'to']}
    if f['q']: qs = qs.filter(Q(number__icontains=f['q']) | Q(buyer_name__icontains=f['q']) | Q(buyer_gstin__icontains=f['q']) | Q(order__number__icontains=f['q']))
    if f['site'].isdigit(): qs = qs.filter(site_id=f['site'])
    if f['type'] == 'b2b': qs = qs.exclude(buyer_gstin='')
    elif f['type'] == 'b2c': qs = qs.filter(buyer_gstin='')
    elif f['type'] == 'overdue': qs = qs.filter(due_date__lt=timezone.localdate())
    for key, lookup in [('from', 'date__gte'), ('to', 'date__lte')]:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', f[key]):
            qs = qs.filter(**{lookup: f[key]})
    return qs, f


@login_required
def invoices(request):
    permitted(request.user, 'view_invoice')
    qs, f = _register(request)
    if request.GET.get('export') == 'csv':
        return export_register(request, qs)
    totals = qs.aggregate(n=Count('id'), taxable=Sum('taxable'), cgst=Sum('cgst'), sgst=Sum('sgst'), igst=Sum('igst'), total=Sum('grand_total'))
    totals['gst'] = sum((totals[k] or 0) for k in ['cgst', 'sgst', 'igst'])
    params = request.GET.copy(); params.pop('page', None)
    from django.core.paginator import Paginator
    return render(request, 'erp/invoices.html', {'title': 'Tax invoices', 'page': Paginator(qs, 50).get_page(request.GET.get('page')), 'totals': totals, 'filters': f,
                                                 'sites': Site.objects.filter(active=True), 'querystring': params.urlencode(),
                                                 'can_export': request.user.has_perm('erp.export_invoice'),
                                                 'ready': scope(Order.objects.filter(kind='Sale', status='Completed', invoice__isnull=True), request.user).select_related('partner')[:20]
                                                 if request.user.has_perm('erp.post_invoice') else []})


def export_register(request, qs):
    """One row per invoice × GST rate — the shape GSTR-1 (B2B / B2CS) needs."""
    permitted(request.user, 'export_invoice')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="invoice-register-{timezone.localdate():%Y%m%d}.csv"'
    response.write('﻿')
    w = csv.writer(response)
    w.writerow(['Invoice no', 'Invoice date', 'Buyer GSTIN', 'Buyer name', 'Type', 'Place of supply', 'Invoice value', 'GST rate', 'Taxable value', 'IGST', 'CGST', 'SGST', 'HSN', 'Seller GSTIN'])
    states = dict(INDIAN_STATES)
    for inv in qs.prefetch_related('lines'):
        by_rate = {}
        for l in inv.lines.all():
            r = by_rate.setdefault((l.gst_rate, l.hsn), [0, 0, 0, 0])
            r[0] += l.taxable; r[1] += l.igst; r[2] += l.cgst; r[3] += l.sgst
        for (rate, hsn), (taxable, igst, cgst, sgst) in by_rate.items():
            w.writerow([inv.number, inv.date.strftime('%d-%m-%Y'), inv.buyer_gstin, inv.buyer_name, 'B2B' if inv.buyer_gstin else 'B2C',
                        f'{inv.place_of_supply}-{states.get(inv.place_of_supply, "")}', inv.grand_total, rate, taxable, igst, cgst, sgst, hsn, inv.seller_gstin])
    AuditEvent.objects.create(actor=request.user, action='Invoice register exported', reference='CSV', detail=f'{qs.count()} invoices')
    return response


@login_required
def invoice_detail(request, pk):
    permitted(request.user, 'view_invoice')
    inv = get_object_or_404(scope(Invoice.objects.select_related('order', 'site', 'partner', 'created_by'), request.user), pk=pk)
    lines = list(inv.lines.all())
    hsn = {}
    for l in lines:
        h = hsn.setdefault((l.hsn, l.gst_rate), {'hsn': l.hsn, 'rate': l.gst_rate, 'taxable': 0, 'cgst': 0, 'sgst': 0, 'igst': 0})
        for k in ['taxable', 'cgst', 'sgst', 'igst']:
            h[k] += getattr(l, k)
    copy = request.GET.get('copy', 'Original for recipient')
    if copy not in ('Original for recipient', 'Duplicate for transporter', 'Triplicate for supplier'):
        copy = 'Original for recipient'
    org = Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization()
    return render(request, 'erp/invoice_detail.html', {
        'title': f'Invoice {inv.number}', 'inv': inv, 'lines': lines, 'hsn': sorted(hsn.values(), key=lambda h: (h['hsn'], h['rate'])), 'company': org,
        'words': in_words(inv.grand_total), 'tax_words': in_words(inv.tax), 'copy': copy, 'states': dict(INDIAN_STATES),
        'can_order': order_permission(request.user, 'Sale', 'view'), 'overdue': inv.due_date < timezone.localdate()})
