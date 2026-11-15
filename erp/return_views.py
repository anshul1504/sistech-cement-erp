"""Customer returns (RMA) screens."""
from accounts.delegation import can_approve
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4
from django.core.paginator import Paginator

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from . import returns as R
from .models import ReturnRequest, Invoice, Complaint, AuditEvent, RETURN_REASONS, RETURN_STATUSES
from .services import permitted, scope


@login_required
def returns_list(request):
    permitted(request.user, 'view_returnrequest')
    qs = scope(ReturnRequest.objects.select_related('partner', 'invoice', 'site'), request.user)
    status, q = request.GET.get('status', 'open'), request.GET.get('q', '').strip()
    base = qs
    if status == 'open': qs = qs.exclude(status__in=['credited', 'closed', 'rejected', 'cancelled'])
    elif status == 'arrival': qs = qs.filter(status__in=['approved', 'transit'])
    elif status != 'all': qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(number__icontains=q) | Q(partner__name__icontains=q) | Q(invoice__number__icontains=q))
    kpi = {k: base.filter(status=k).count() for k in ('requested', 'approved', 'transit', 'inspected')}
    return render(request, 'erp/returns.html', {'title': 'Customer returns', 'rows': Paginator(qs, 30).get_page(request.GET.get('page')), 'status': status, 'q': q, 'kpi': kpi, 'statuses': RETURN_STATUSES,
                                                'can_add': request.user.has_perm('erp.add_returnrequest')})


@login_required
def return_new(request):
    permitted(request.user, 'add_returnrequest')
    P, G = request.POST, request.GET
    invoice_text = (P.get('invoice') or G.get('invoice') or '').strip()
    base = scope(Invoice.objects.select_related('partner', 'site'), request.user)
    inv = base.filter(number=invoice_text).first() if invoice_text else base.filter(pk=int(G['invoice_id'])).first() if G.get('invoice_id', '').isdecimal() else None
    errors, rows = [], []
    if inv:
        rows = [{'line': l, 'room': R.returnable(l), 'qty': P.get(f'qty_{l.pk}', '')} for l in inv.lines.select_related('product')]
    elif G.get('invoice') or P.get('invoice'):
        errors.append('No invoice with that number at your plants.')
    complaints = Complaint.objects.filter(partner=inv.partner, invoice=inv).exclude(status='closed') if inv and request.user.has_perm('erp.view_complaint') else []
    if request.method == 'POST' and inv and P.get('save') == '1':
        try:
            raw_complaint=P.get('complaint', '')
            complaint=None
            if raw_complaint:
                if not raw_complaint.isdecimal(): raise ValidationError('Choose a valid complaint.')
                complaint=Complaint.objects.filter(pk=raw_complaint, partner=inv.partner, invoice=inv).first()
                if not complaint: raise ValidationError('Choose a complaint from your assigned sites.')
            qtys = {r['line']: R.quantity(r['qty'] or 0) for r in rows}
            ret = R.request_return(request.user, inv, P.get('reason', ''), P.get('description', ''), qtys,
                                   complaint, P.get('submission_key'))
        except InvalidOperation:
            errors.append('Enter quantities as numbers.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'{ret.number} raised. A different user approves it before pickup.')
            return redirect('return_detail', pk=ret.pk)
    return render(request, 'erp/return_new.html', {'title': 'New customer return', 'inv': inv, 'rows': rows, 'errors': errors, 'post': P, 'reasons': RETURN_REASONS,
                                                   'submission_key': P.get('submission_key') or uuid4(), 'complaints': complaints, 'invoice_text': (inv.number if inv else P.get('invoice') or G.get('invoice') or '')})


@login_required
def return_detail(request, pk):
    permitted(request.user, 'view_returnrequest')
    r = get_object_or_404(scope(ReturnRequest.objects.select_related('partner', 'invoice', 'site', 'complaint', 'credit_note', 'created_by', 'decided_by', 'inspected_by'), request.user), pk=pk)
    u = request.user
    lines = list(r.lines.select_related('invoice_line__product'))
    for l in lines:
        from .finance_views import credit_room
        from .gst import money
        l.value = min(money((l.good + l.damaged) * l.invoice_line.rate), credit_room(l.invoice_line, exclude_return=r)[0]) if not r.credit_note_id else r.credit_note.lines.filter(invoice_line=l.invoice_line).values_list('taxable', flat=True).first() or Decimal(0)
    return render(request, 'erp/return_detail.html', {
        'title': r.number, 'r': r, 'lines': lines, 'credit_value': sum((l.value for l in lines), Decimal('0')),
        'history': AuditEvent.objects.filter(reference=r.number).select_related('actor').order_by('-created_at')[:12],
        'can': {'decide': r.status == 'requested' and can_approve(u, 'approve_returnrequest', r.site, makers=(r.created_by_id,)) and r.created_by_id != u.pk,
                'pickup': r.status == 'approved' and u.has_perm('erp.inspect_returnrequest'),
                'inspect': r.status in ('approved', 'transit') and u.has_perm('erp.inspect_returnrequest'),
                'credit': r.status == 'inspected' and u.has_perm('erp.add_creditnote') and u.has_perm('erp.access_company_finance') and u.pk not in [r.created_by_id, r.inspected_by_id],
                'cancel': r.status in ('requested', 'approved') and ((u.pk == r.created_by_id and u.has_perm('erp.add_returnrequest')) or can_approve(u, 'approve_returnrequest', r.site, makers=(r.created_by_id,)))}})


@login_required
@require_POST
def return_action(request, pk, action):
    get_object_or_404(scope(ReturnRequest.objects.all(), request.user), pk=pk)
    P, u = request.POST, request.user
    try:
        if action in ('approve', 'reject'):
            R.decide(u, pk, action == 'approve', P.get('note', '')); msg = 'Decision recorded.'
        elif action == 'pickup':
            permitted(u, 'inspect_returnrequest')
            R.pickup(u, pk, date.fromisoformat(P['pickup_on']) if P.get('pickup_on') else None, P.get('vehicle', ''), P.get('lr', '')); msg = 'Pickup recorded.'
        elif action == 'inspect':
            permitted(u, 'inspect_returnrequest')
            r = ReturnRequest.objects.get(pk=pk)
            res = {l.pk: (Decimal(P.get(f'received_{l.pk}') or 0), Decimal(P.get(f'good_{l.pk}') or 0), Decimal(P.get(f'damaged_{l.pk}') or 0), P.get(f'note_{l.pk}', ''))
                   for l in r.lines.all()}
            result = R.inspect(u, pk, res); msg = 'Inspection recorded. No goods accepted; return closed without stock or credit.' if result.status == 'closed' else 'Inspection recorded. Accepted goods remain in quarantine until finance posts credit.'
        elif action == 'credit':
            r = R.credit(u, pk); msg = f'Credit note {r.credit_note.number} posted; stock added back.'
        elif action == 'cancel':
            R.cancel(u, pk, P.get('reason', '')); msg = 'Return cancelled.'
        else:
            raise Http404
        messages.success(request, msg)
    except (ValueError, InvalidOperation):
        messages.error(request, 'Enter dates and quantities correctly.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('return_detail', pk=pk)
