"""Procurement screens: purchase requests, reorder suggestions, RFQ + quote comparison + award, direct (single-source) PO."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models import Q, Count
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import procurement as P
from .forms import PurchaseRequestForm, PRLines, QuoteForm
from .models import PurchaseRequest, PurchaseRequestLine, RFQ, SupplierQuote, Partner, Product, Site, AuditEvent, Order
from .services import permitted, scope


def _sites(user):
    qs = Site.objects.filter(active=True)
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


def _dec(value):
    try:
        return Decimal(str(value).replace(',', '').strip()) if str(value).strip() else None
    except InvalidOperation:
        return 'bad'


def _date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# ---------- Purchase requests ----------

@login_required
def purchase_requests(request):
    permitted(request.user, 'view_purchaserequest')
    qs = scope(PurchaseRequest.objects.select_related('site', 'created_by').prefetch_related('lines__product'), request.user)
    status = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()
    if status: qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(number__icontains=q) | Q(purpose__icontains=q) | Q(lines__product__name__icontains=q)).distinct()
    base = scope(PurchaseRequest.objects.all(), request.user)
    sites = list(_sites(request.user))
    return render(request, 'erp/purchase_requests.html', {
        'title': 'Purchase requests', 'requests': qs[:200], 'status': status, 'q': q, 'statuses': PurchaseRequest._meta.get_field('status').choices,
        'waiting': base.filter(status='submitted').count(), 'to_buy': base.filter(status='approved').count(),
        'urgent': base.filter(status__in=['submitted', 'approved'], priority='urgent').count(),
        'reorder_count': len(P.reorder_suggestions(sites)), 'can_add': request.user.has_perm('erp.add_purchaserequest')})


@login_required
def pr_form(request, pk=None):
    pr = get_object_or_404(scope(PurchaseRequest.objects.all(), request.user), pk=pk) if pk else None
    permitted(request.user, 'change_purchaserequest' if pr else 'add_purchaserequest')
    if pr and pr.status != 'draft':
        messages.error(request, 'Only draft requests can be edited.')
        return redirect('pr_detail', pk=pr.pk)
    obj = pr or PurchaseRequest(needed_by=timezone.localdate() + timedelta(days=7))
    form = PurchaseRequestForm(request.POST or None, instance=obj)
    form.fields['site'].queryset = _sites(request.user)
    lines = PRLines(request.POST or None, instance=obj, prefix='lines')
    errors = []
    if request.method == 'POST' and form.is_valid() and lines.is_valid():
        rows = [(f.cleaned_data['product'], f.cleaned_data['quantity'], f.cleaned_data.get('estimated_rate'), f.cleaned_data.get('specification'))
                for f in lines.forms if f.cleaned_data and not f.cleaned_data.get('DELETE')]
        d = form.cleaned_data
        try:
            saved = P.save_request(request.user, d['site'], d['needed_by'], d['source'], d['priority'], d['purpose'], rows, pr=pr)
        except ValidationError as e:
            errors = e.messages
        else:
            messages.success(request, f'{saved.number} saved as draft. Submit it for approval.')
            return redirect('pr_detail', pk=saved.pk)
    units = {p.pk: p.unit for p in Product.objects.filter(active=True)}
    return render(request, 'erp/pr_form.html', {'title': f'Edit {pr.number}' if pr else 'New purchase request', 'form': form, 'formset': lines, 'pr': pr,
                                                'errors': errors, 'units': units})


@login_required
def pr_detail(request, pk):
    permitted(request.user, 'view_purchaserequest')
    pr = get_object_or_404(scope(PurchaseRequest.objects.select_related('site', 'created_by', 'decided_by'), request.user), pk=pk)
    lines = list(pr.lines.select_related('product'))
    for l in lines:
        l.stock = P.stock_at(pr.site, l.product)
        l.ordered = l.ordered_quantity()
        l.pending = l.pending_quantity
    orders = Order.objects.filter(lines__pr_line__request=pr).distinct().select_related('partner')
    rfqs = pr.rfqs.annotate(n=Count('quotes')).order_by('-created_at')
    u = request.user
    pending = any(l.pending > 0 for l in lines)
    return render(request, 'erp/pr_detail.html', {
        'title': pr.number, 'pr': pr, 'lines': lines, 'orders': orders, 'rfqs': rfqs,
        'history': AuditEvent.objects.filter(reference=pr.number).select_related('actor').order_by('-created_at')[:10],
        'can_edit': pr.status == 'draft' and u.has_perm('erp.change_purchaserequest'),
        'can_submit': pr.status == 'draft' and u.has_perm('erp.add_purchaserequest'),
        'can_decide': pr.status == 'submitted' and u.has_perm('erp.approve_purchaserequest') and pr.created_by_id != u.pk,
        'can_cancel': pr.status in ('draft', 'submitted', 'approved') and u.has_perm('erp.add_purchaserequest'),
        'can_rfq': pr.status == 'approved' and pending and u.has_perm('erp.add_rfq') and not pr.rfqs.filter(status='open').exists(),
        'can_direct': pr.status == 'approved' and pending and u.has_perm('erp.create_purchase_order'),
        'estimated': pr.estimated_value})


@login_required
@require_POST
def pr_action(request, pk, action):
    get_object_or_404(scope(PurchaseRequest.objects.all(), request.user), pk=pk)
    try:
        pr = P.request_action(request.user, pk, action, request.POST.get('note', ''))
        messages.success(request, f'{pr.number}: {pr.get_status_display().lower()}.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('pr_detail', pk=pk)


@login_required
def reorder(request):
    permitted(request.user, 'view_purchaserequest')
    sites = list(_sites(request.user))
    rows = P.reorder_suggestions(sites)
    if request.method == 'POST':
        permitted(request.user, 'add_purchaserequest')
        picked = {}
        problems = []
        for r in rows:
            if request.POST.get('pick_' + r['key']) != 'on':
                continue
            qty = _dec(request.POST.get('qty_' + r['key']))
            if qty in (None, 'bad') or qty <= 0:
                problems.append(f'{r["product"].name} at {r["site"].code}: enter a quantity.')
                continue
            picked.setdefault(r['site'], []).append((r['product'], qty, None, f'Reorder: stock {P.qtxt(r["stock"])} below level {P.qtxt(r["level"])}'))
        if not picked and not problems:
            problems.append('Tick at least one item.')
        if not problems:
            made = []
            try:
                for site, items in picked.items():
                    made.append(P.save_request(request.user, site, timezone.localdate() + timedelta(days=7), 'reorder', 'normal',
                                               'Stock below reorder level', items))
            except ValidationError as e:
                problems += e.messages
            else:
                messages.success(request, f'{len(made)} draft request{"s" if len(made) > 1 else ""} raised: {", ".join(p.number for p in made)}. Check and submit.')
                return redirect('pr_detail', pk=made[0].pk) if len(made) == 1 else redirect(reverse('purchase_requests') + '?status=draft')
        for p in problems:
            messages.error(request, p)
    return render(request, 'erp/reorder.html', {'title': 'Reorder suggestions', 'rows': rows, 'can_add': request.user.has_perm('erp.add_purchaserequest')})


@login_required
def pr_direct(request, pk):
    pr = get_object_or_404(scope(PurchaseRequest.objects.select_related('site'), request.user), pk=pk)
    if not request.user.has_perm('erp.create_purchase_order'):
        raise PermissionDenied
    lines = [l for l in pr.lines.select_related('product') if l.pending_quantity > 0]
    suppliers = Partner.objects.filter(kind='Supplier', active=True).order_by('name')
    for l in lines:
        l.qty_in = request.POST.get(f'qty_{l.pk}', P.qtxt(l.pending_quantity))
        l.rate_in = request.POST.get(f'rate_{l.pk}', l.estimated_rate or '')
    errors = []
    if request.method == 'POST':
        supplier = suppliers.filter(pk=request.POST.get('supplier') or 0).first()
        qty, rates = {}, {}
        for l in lines:
            q, r = _dec(request.POST.get(f'qty_{l.pk}')), _dec(request.POST.get(f'rate_{l.pk}'))
            if 'bad' in (q, r):
                errors.append(f'{l.product.name}: enter numbers only.')
            qty[l], rates[l] = (q if q != 'bad' else None), (r if r != 'bad' else None)
        if not supplier:
            errors.append('Choose the supplier.')
        if not errors:
            try:
                order = P.direct_po(request.user, pr, supplier, request.POST.get('reason', ''), qty, rates)
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{order.number} drafted for {supplier.name}. Check it and submit for approval.')
                return redirect('order_detail', pk=order.pk)
    return render(request, 'erp/pr_direct.html', {'title': f'Direct PO · {pr.number}', 'pr': pr, 'lines': lines, 'suppliers': suppliers, 'errors': errors, 'post': request.POST})


# ---------- RFQ ----------

@login_required
def rfqs(request):
    permitted(request.user, 'view_rfq')
    qs = scope(RFQ.objects.select_related('request', 'site', 'created_by').annotate(n_quotes=Count('quotes', distinct=True), n_invited=Count('suppliers', distinct=True)), request.user)
    status = request.GET.get('status', '')
    if status: qs = qs.filter(status=status)
    today = timezone.localdate()
    base = scope(RFQ.objects.all(), request.user)
    return render(request, 'erp/rfqs.html', {'title': 'RFQs & quotes', 'rfqs': qs[:200], 'status': status, 'today': today,
                                             'open': base.filter(status='open').count(), 'due': base.filter(status='open', quotes_due__lte=today).count(),
                                             'awarded': base.filter(status='awarded').count()})


@login_required
def rfq_new(request, pk):
    permitted(request.user, 'add_rfq')
    pr = get_object_or_404(scope(PurchaseRequest.objects.select_related('site'), request.user), pk=pk)
    lines = [l for l in pr.lines.select_related('product') if l.pending_quantity > 0]
    suppliers = list(Partner.objects.filter(kind='Supplier', active=True).order_by('name'))
    for s in suppliers:
        s.quality = P.supplier_quality(s)
    for l in lines:
        l.qty_in = request.POST.get(f'qty_{l.pk}', P.qtxt(l.pending_quantity))
    errors = []
    if request.method == 'POST':
        picked = [s for s in suppliers if str(s.pk) in request.POST.getlist('suppliers')]
        qty = {}
        for l in lines:
            q = _dec(request.POST.get(f'qty_{l.pk}'))
            if q == 'bad':
                errors.append(f'{l.product.name}: enter a number.')
            else:
                qty[l] = q
        if not errors:
            try:
                rfq = P.create_rfq(request.user, pr, picked, _date(request.POST.get('quotes_due')), request.POST.get('terms', ''), qty)
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{rfq.number} created. Record each supplier\'s quote as it comes in.')
                return redirect('rfq_detail', pk=rfq.pk)
    return render(request, 'erp/rfq_new.html', {'title': f'RFQ · {pr.number}', 'pr': pr, 'lines': lines, 'suppliers': suppliers, 'errors': errors, 'post': request.POST,
                                                'picked': request.POST.getlist('suppliers'), 'due': request.POST.get('quotes_due') or (timezone.localdate() + timedelta(days=3)).isoformat()})


@login_required
def rfq_detail(request, pk):
    permitted(request.user, 'view_rfq')
    rfq = get_object_or_404(scope(RFQ.objects.select_related('request', 'site', 'created_by'), request.user), pk=pk)
    cmp = P.compare(rfq)
    quoted = {q.supplier_id for q in cmp['quotes']}
    waiting = [s for s in rfq.suppliers.order_by('name') if s.pk not in quoted]
    orders = Order.objects.filter(quote__rfq=rfq).select_related('partner')
    u = request.user
    return render(request, 'erp/rfq_detail.html', {
        'title': rfq.number, 'rfq': rfq, 'cmp': cmp, 'waiting': waiting, 'orders': orders,
        'history': AuditEvent.objects.filter(reference=rfq.number).select_related('actor').order_by('-created_at')[:10],
        'can_quote': rfq.status == 'open' and u.has_perm('erp.add_rfq'),
        'can_award': rfq.status == 'open' and u.has_perm('erp.award_rfq') and any(q.valid for q in cmp['quotes']),
        'can_cancel': rfq.status == 'open' and u.has_perm('erp.add_rfq')})


@login_required
def quote_form(request, pk, supplier_pk):
    permitted(request.user, 'add_rfq')
    rfq = get_object_or_404(scope(RFQ.objects.select_related('request', 'site'), request.user), pk=pk)
    supplier = get_object_or_404(rfq.suppliers, pk=supplier_pk)
    existing = SupplierQuote.objects.filter(rfq=rfq, supplier=supplier).first()
    form = QuoteForm(request.POST or None, instance=existing or SupplierQuote(rfq=rfq, supplier=supplier, payment_terms_days=supplier.payment_terms_days,
                                                                            valid_until=timezone.localdate() + timedelta(days=15)))
    lines = list(rfq.lines.select_related('product'))
    old = {ql.rfq_line_id: ql for ql in existing.lines.all()} if existing else {}
    for l in lines:
        l.rate_in = request.POST.get(f'rate_{l.pk}', old[l.pk].rate if l.pk in old else '')
        l.gst_in = request.POST.get(f'gst_{l.pk}', old[l.pk].gst_rate if l.pk in old else (l.product.gst_rate if l.product.gst_rate is not None else 18))
    errors = []
    if request.method == 'POST' and form.is_valid():
        rates = {}
        for l in lines:
            r, g = _dec(l.rate_in), _dec(l.gst_in)
            if 'bad' in (r, g):
                errors.append(f'{l.product.name}: enter numbers only.')
            rates[l] = (r if r != 'bad' else None, g if g != 'bad' else None)
        if not errors:
            data = {k: form.cleaned_data[k] for k in ['reference', 'quote_date', 'valid_until', 'freight', 'lead_time_days', 'payment_terms_days', 'notes']}
            data['freight'] = data['freight'] or Decimal('0')
            try:
                P.save_quote(request.user, rfq, supplier, data, rates)
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f"{supplier.name}'s quote saved.")
                return redirect('rfq_detail', pk=rfq.pk)
    return render(request, 'erp/quote_form.html', {'title': f'Quote · {supplier.name}', 'rfq': rfq, 'supplier': supplier, 'form': form, 'lines': lines, 'errors': errors,
                                                   'existing': existing})


@login_required
@require_POST
def rfq_award(request, pk):
    get_object_or_404(scope(RFQ.objects.all(), request.user), pk=pk)
    tol = _dec(request.POST.get('tolerance'))
    try:
        if tol == 'bad' or (tol is not None and not (0 <= tol <= 10)):
            raise ValidationError('Over-receipt tolerance must be between 0 and 10%.')
        order = P.award(request.user, pk, request.POST.get('quote') or 0, request.POST.get('reason', ''), tol or Decimal('0'), request.POST.get('delivery_terms', ''))
    except SupplierQuote.DoesNotExist:
        messages.error(request, 'Choose the quote to award.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, f'Awarded to {order.partner.name}. {order.number} is a draft — submit it for approval.')
        return redirect('order_detail', pk=order.pk)
    return redirect('rfq_detail', pk=pk)


@login_required
@require_POST
def rfq_cancel(request, pk):
    get_object_or_404(scope(RFQ.objects.all(), request.user), pk=pk)
    try:
        P.cancel_rfq(request.user, pk, request.POST.get('note', ''))
        messages.success(request, 'RFQ cancelled.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('rfq_detail', pk=pk)
