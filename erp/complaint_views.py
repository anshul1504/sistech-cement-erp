"""Complaints and CAPA screens."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import complaints as CM
from .models import Complaint, CapaAction, Partner, Invoice, Dispatch, Product, CreditNote, Order, AuditEvent, COMPLAINT_CATEGORIES, CUSTOMER_KINDS
from .services import permitted


def _users():
    return get_user_model().objects.filter(is_active=True).order_by('first_name', 'username')


@login_required
def complaints(request):
    permitted(request.user, 'view_complaint')
    qs = Complaint.objects.select_related('partner', 'invoice', 'owner')
    status, q = request.GET.get('status', ''), request.GET.get('q', '').strip()
    if status == 'overdue': qs = qs.exclude(status='closed').filter(due_on__lt=timezone.localdate())
    elif status: qs = qs.filter(status=status)
    else: qs = qs.exclude(status='closed')
    if q: qs = qs.filter(Q(number__icontains=q) | Q(partner__name__icontains=q) | Q(invoice__number__icontains=q))
    today = timezone.localdate()
    open_qs = Complaint.objects.exclude(status='closed')
    return render(request, 'erp/complaints.html', {
        'title': 'Complaints & CAPA', 'rows': qs[:200], 'status': status, 'q': q,
        'kpi': {'open': open_qs.count(), 'overdue': open_qs.filter(due_on__lt=today).count(), 'quality': open_qs.filter(category='quality').count(),
                'capa_overdue': CapaAction.objects.filter(status__in=['open', 'reopened'], due_on__lt=today).count()},
        'capas': CapaAction.objects.exclude(status='effective').select_related('complaint', 'owner')[:50], 'can_add': request.user.has_perm('erp.add_complaint')})


@login_required
def complaint_new(request):
    permitted(request.user, 'add_complaint')
    P, G = request.POST, request.GET
    customers = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).order_by('name')
    partner = customers.filter(pk=P.get('partner') or G.get('partner') or 0).first()
    invoices = Invoice.objects.filter(partner=partner).order_by('-date')[:100] if partner else []
    invoice = Invoice.objects.filter(pk=P.get('invoice') or G.get('invoice') or 0, partner=partner).first() if partner else None
    dispatches = Dispatch.objects.filter(invoice=invoice).select_related('vehicle') if invoice else []
    products = Product.objects.filter(pk__in=invoice.lines.values('product')) if invoice else Product.objects.filter(active=True, category__in=['Cement', 'TMT'])
    errors = []
    if request.method == 'POST' and P.get('save') == '1':
        try:
            if not partner:
                raise ValidationError('Choose the customer.')
            c = CM.log(request.user, partner, P.get('category', ''), P.get('description', ''), invoice,
                       Dispatch.objects.filter(pk=P.get('dispatch') or 0).first(), Product.objects.filter(pk=P.get('product') or 0).first(),
                       Decimal(P.get('quantity') or 0), date.fromisoformat(P['received_on']) if P.get('received_on') else None, P.get('site_location', ''),
                       _users().filter(pk=P.get('owner') or 0).first())
        except (ValueError, InvalidOperation):
            errors.append('Enter the quantity and date correctly.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'{c.number} logged. Resolve by {c.due_on:%d %b %Y}.')
            return redirect('complaint_detail', pk=c.pk)
    return render(request, 'erp/complaint_new.html', {'title': 'Log complaint', 'customers': customers, 'partner': partner, 'invoices': invoices, 'invoice': invoice,
                                                      'dispatches': dispatches, 'products': products, 'categories': COMPLAINT_CATEGORIES, 'users': _users(),
                                                      'errors': errors, 'post': P, 'today': timezone.localdate().isoformat()})


@login_required
def complaint_detail(request, pk):
    permitted(request.user, 'view_complaint')
    c = get_object_or_404(Complaint.objects.select_related('partner', 'invoice', 'dispatch__vehicle', 'batch', 'product', 'owner', 'decided_by', 'credit_note',
                                                          'replacement_order', 'created_by', 'closed_by'), pk=pk)
    u = request.user
    return render(request, 'erp/complaint_detail.html', {
        'title': c.number, 'c': c, 'capas': c.capas.select_related('owner', 'verified_by'), 'users': _users(), 'problems': CM.closure_problems(c) if c.status == 'decided' else [],
        'credit_notes': CreditNote.objects.filter(invoice=c.invoice) if c.invoice_id else [], 'orders': Order.objects.filter(kind='Sale', partner=c.partner).order_by('-created_at')[:20],
        'history': AuditEvent.objects.filter(reference=c.number).select_related('actor').order_by('-created_at')[:15], 'today': timezone.localdate().isoformat(),
        'can': {'investigate': c.status in ('open', 'investigating') and u.has_perm('erp.change_complaint'),
                'decide': c.status == 'investigating' and u.has_perm('erp.decide_complaint') and u.pk not in (c.created_by_id, c.owner_id),
                'settle': c.status == 'decided' and c.resolution in ('credit', 'replace') and u.has_perm('erp.change_complaint'),
                'close': c.status == 'decided' and u.has_perm('erp.decide_complaint'), 'capa': c.status != 'closed' and u.has_perm('erp.add_capaaction'),
                'verify': u.has_perm('erp.verify_capaaction')}})


@login_required
@require_POST
def complaint_action(request, pk, action):
    P, u = request.POST, request.user
    get_object_or_404(Complaint, pk=pk)
    try:
        if action == 'investigate':
            CM.investigate(u, pk, P.get('findings', ''), _users().filter(pk=P.get('owner') or 0).first()); msg = 'Findings saved.'
        elif action == 'decide':
            CM.decide(u, pk, P.get('decision', ''), Decimal(P.get('accepted_quantity') or 0), P.get('resolution', ''), P.get('note', '')); msg = 'Decision recorded.'
        elif action == 'settle':
            CM.settle(u, pk, CreditNote.objects.filter(pk=P.get('credit_note') or 0).first(), Order.objects.filter(pk=P.get('order') or 0).first()); msg = 'Settlement linked.'
        elif action == 'close':
            CM.close(u, pk, P.get('waiver', '')); msg = 'Complaint closed.'
        elif action == 'capa':
            CM.add_capa(u, Complaint.objects.get(pk=pk), P.get('title', ''), P.get('root_cause', ''), P.get('corrective', ''), _users().get(pk=P.get('owner')),
                        date.fromisoformat(P.get('due_on')), P.get('containment', ''), P.get('preventive', '')); msg = 'CAPA added.'
        elif action in ('implement', 'effective', 'reopen'):
            x = int(P.get('capa') or 0)
            if action == 'implement': CM.implement(u, x, P.get('note', ''))
            else: CM.verify(u, x, action == 'effective', P.get('note', ''))
            msg = 'CAPA updated.'
        else:
            raise Http404
        messages.success(request, msg)
    except (ValueError, InvalidOperation, get_user_model().DoesNotExist):
        messages.error(request, 'Fill in the owner, dates and quantities correctly.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('complaint_detail', pk=pk)
