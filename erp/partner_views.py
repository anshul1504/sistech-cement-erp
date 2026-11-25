"""Dealers & suppliers: list, detail (credit, orders, KYC), create/edit, activate, delete-if-unused, KYC documents."""
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.http import content_disposition_header
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from accounts.roles import order_permission
from .forms import PartnerForm, PartnerDocumentForm
from .crm_forms import PROTECTED_CUSTOMER_FIELDS
from .models import Partner, PartnerDocument, Order, AuditEvent, Influencer, PARTNER_KINDS, CUSTOMER_KINDS
from .services import audit, permitted

KIND_ICONS = {'Dealer': 'handshake', 'Distributor': 'truck', 'Retailer': 'building', 'Institutional': 'factory', 'Supplier': 'boxes', 'Transporter': 'truck'}


def credit_position(partner):
    """Exposure = ledger balance (invoices − receipts − credit notes) + approved / dispatched orders not yet invoiced, all incl. GST."""
    from .ledger import exposure, overdue
    exp = exposure(partner)
    late = overdue(partner)
    limit = partner.credit_limit or Decimal('0')
    total = exp['total']
    used = int(max(0, min(100, (total / limit * 100)))) if limit else (100 if total > 0 else 0)
    return {'limit': limit, 'exposure': total, 'balance': exp['balance'], 'pipeline': exp['pipeline'], 'available': max(limit - total, Decimal('0')),
            'used_pct': used, 'overdue': sum((i.outstanding for i in late), Decimal('0')), 'overdue_count': len(late),
            'state': 'danger' if late or (limit and total >= limit) or (not limit and total > 0) else 'warn' if used >= 80 else 'ok'}


def _can_read_documents(user, partner):
    if not partner.approved_application_id:
        return user.has_perm('erp.view_partner')
    from .crm import lead_scope
    from .models import DealerApplication
    return user.has_perm('erp.view_dealerdocument') and lead_scope(DealerApplication.objects.all(), user, 'lead').filter(pk=partner.approved_application_id).exists()


def is_used(partner):
    return Order.objects.filter(partner=partner).exists() or partner.downline.exists() or partner.crm_leads.exists() or partner.dealer_applications.exists() or partner.territory_assignments.exists() or partner.sales_projects.exists()


@login_required
def partners(request):
    permitted(request.user, 'view_partner')
    group, kind, status = request.GET.get('group', ''), request.GET.get('kind', ''), request.GET.get('status', 'active')
    q = request.GET.get('q', '').strip()
    qs = Partner.objects.annotate(order_count=Count('order')).select_related('parent')
    if group == 'customers': qs = qs.filter(kind__in=CUSTOMER_KINDS)
    elif group == 'vendors': qs = qs.exclude(kind__in=CUSTOMER_KINDS)
    people_group = request.GET.get('people_group', '')
    from .network import LEVELS
    level = next((lv for lv in LEVELS if lv[2][0] == people_group), None)
    if level:
        qs = qs.filter(kind__in=level[2])
    upline, upline_obj = request.GET.get('upline', ''), None
    if upline == 'none':
        qs = qs.filter(parent__isnull=True)
    elif upline.isdigit():
        upline_obj = Partner.objects.filter(pk=upline).first()
        qs = qs.filter(parent_id=upline)
    if kind: qs = qs.filter(kind=kind)
    if status == 'active': qs = qs.filter(active=True)
    elif status == 'inactive': qs = qs.filter(active=False)
    if q: qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q) | Q(territory__icontains=q) | Q(city__icontains=q) | Q(gstin__icontains=q) | Q(contact_person__icontains=q) | Q(phone__icontains=q))
    active = Partner.objects.filter(active=True)
    tabs = [('', 'All', active.count()), ('customers', 'Customers', active.filter(kind__in=CUSTOMER_KINDS).count()),
            ('vendors', 'Suppliers & transporters', active.exclude(kind__in=CUSTOMER_KINDS).count())]
    category_label = level[1] if level else 'Dealers & suppliers'
    if upline_obj:
        category_label = f'Under {upline_obj.name}'
    elif upline == 'none' and level:
        category_label = f'{level[1]} · no upline yet'
    return render(request, 'erp/partners.html', {
        'title': category_label, 'list_title': category_label, 'partners': qs.order_by('name')[:300], 'tabs': tabs, 'kinds': PARTNER_KINDS,
        'people_group': people_group, 'upline': upline, 'upline_obj': upline_obj, 'filters': {'group': group, 'kind': kind, 'status': status, 'q': q}, 'can_add': request.user.has_perm('erp.add_partner')})


@login_required
def partner_detail(request, pk):
    permitted(request.user, 'view_partner')
    partner = get_object_or_404(Partner, pk=pk)
    kind = 'Sale' if partner.is_customer else 'Purchase'
    can_orders = order_permission(request.user, kind, 'view')
    orders = Order.objects.filter(partner=partner).prefetch_related('lines').select_related('site').order_by('-created_at')
    if not request.user.is_superuser:
        orders = orders.filter(site__siteaccess__user=request.user)
    summary = {s: 0 for s in ['Draft', 'Submitted', 'Approved', 'Completed', 'Cancelled']}
    value = Decimal('0')
    for o in orders:
        summary[o.status] = summary.get(o.status, 0) + 1
        if o.status != 'Cancelled':
            value += o.total
    network = None
    if partner.is_customer and request.user.has_perm('erp.view_influencer'):
        people = Influencer.objects.filter(active=True).filter(Q(primary_dealer=partner) | Q(dealers=partner)).distinct()
        network = {'people': people.order_by('-monthly_cement_bags', 'name')[:6], 'total': people.count(),
                   'masons': people.filter(kind='Mason').count(), 'contractors': people.filter(kind='Contractor').count(),
                   'bags': Influencer.objects.filter(pk__in=people.values('pk')).aggregate(b=Sum('monthly_cement_bags'))['b'] or 0,
                   'can_add': request.user.has_perm('erp.add_influencer')}
    from . import network as net
    can_change = request.user.has_perm('erp.change_partner')
    channel = net.network_card(request.user, partner) if (net.allowed_parents(partner.kind) or partner.kind == 'CF' or partner.downline.exists()) else None
    if channel is not None:
        channel['choices'] = net.parent_choices(partner) if can_change and channel['allowed'] else []
    return render(request, 'erp/partner_detail.html', {
        'title': partner.name, 'network': network, 'channel': channel, 'partner': partner, 'icon': KIND_ICONS.get(partner.kind, 'handshake'),
        'credit': credit_position(partner) if partner.is_customer else None,
        'orders': orders[:8] if can_orders else [], 'can_orders': can_orders, 'summary': summary, 'order_value': value,
        'documents': partner.documents.defer('file') if _can_read_documents(request.user, partner) else [], 'can_documents': request.user.has_perm('erp.change_partner') and not partner.approved_application_id, 'used': is_used(partner),
        'can_change': request.user.has_perm('erp.change_partner'),
        'can_create_order': order_permission(request.user, kind, 'create'), 'order_kind': kind,
        'history': AuditEvent.objects.filter(reference__in=[str(partner), f'partner:{partner.pk}']).select_related('actor').order_by('-created_at')[:6]})


@login_required
def partner_form(request, pk=None):
    permitted(request.user, 'change_partner' if pk else 'add_partner')
    partner = get_object_or_404(Partner, pk=pk) if pk else None
    initial = {'kind': request.GET.get('kind')} if not pk and request.GET.get('kind') in dict(PARTNER_KINDS) else None
    form = PartnerForm(request.POST or None, instance=partner, initial=initial)
    if not partner:
        form.fields['kind'].choices = [('', 'Select partner type')] + [(k, v) for k, v in PARTNER_KINDS if k not in CUSTOMER_KINDS]
    elif partner.is_customer:
        for name in PROTECTED_CUSTOMER_FIELDS + ['active']:
            form.fields[name].disabled = True
            form.fields[name].help_text = 'Use a reviewed CRM amendment to change approved customer details.' if name != 'active' else 'Use the audited status action on the customer page.'
    else:
        form.fields['kind'].choices = [(k, v) for k, v in PARTNER_KINDS if k not in CUSTOMER_KINDS]
    if partner and is_used(partner):
        form.fields['kind'].disabled = True  # type decides which orders use it; lock once used
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved = form.save()
            from .network import clear_if_invalid
            clear_if_invalid(request.user, saved)
            AuditEvent.objects.create(actor=request.user, action='Master updated' if pk else 'Master created', reference=f'partner:{saved.pk}', detail=f'Partner {saved.code}')
        messages.success(request, f'{saved.name} {"updated" if pk else "added"}.')
        return redirect('partner_detail', pk=saved.pk)
    return render(request, 'erp/partner_form.html', {'title': 'Edit partner' if pk else 'Add partner', 'form': form, 'partner': partner})


@login_required
@require_POST
def partner_status(request, pk):
    permitted(request.user, 'change_partner')
    partner = get_object_or_404(Partner, pk=pk)
    partner.active = not partner.active
    partner.save(update_fields=['active', 'updated_at'])
    AuditEvent.objects.create(actor=request.user, action='Partner activated' if partner.active else 'Partner deactivated', reference=f'partner:{partner.pk}', detail=partner.code)
    messages.success(request, f'{partner.name} is {"active again" if partner.active else "now inactive — it no longer appears on new orders"}.')
    return redirect('partner_detail', pk=partner.pk)


@login_required
@require_POST
def partner_delete(request, pk):
    permitted(request.user, 'change_partner')
    if not request.user.is_superuser:
        raise PermissionDenied
    partner = get_object_or_404(Partner, pk=pk)
    if is_used(partner):
        messages.error(request, f'{partner.name} has retained business references, so it cannot be deleted. Deactivate it instead.')
        return redirect('partner_detail', pk=partner.pk)
    name = partner.name
    AuditEvent.objects.create(actor=request.user, action='Partner deleted', reference=f'partner:{partner.pk}', detail=partner.code)
    partner.delete()
    messages.success(request, f'{name} deleted.')
    return redirect('partners')


# ---------- KYC documents ----------

@login_required
def partner_document_new(request, pk):
    permitted(request.user, 'change_partner')
    partner = get_object_or_404(Partner, pk=pk)
    if partner.approved_application_id:
        messages.error(request, 'KYC changes for an onboarded customer require a reviewed CRM amendment. Retained evidence is available in Dealer CRM.')
        return redirect('partner_detail', pk=partner.pk)
    form = PartnerDocumentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        doc = form.save(commit=False)
        doc.partner, doc.uploaded_by = partner, request.user
        doc.save()
        AuditEvent.objects.create(actor=request.user, action='Partner document uploaded', reference=f'partner:{partner.pk}', detail=f'{doc.get_doc_type_display()} · {doc.file_name}')
        messages.success(request, f'{doc.get_doc_type_display()} added for {partner.name}.')
        return redirect('partner_detail', pk=partner.pk)
    return render(request, 'erp/partner_document_form.html', {'title': 'Add KYC document', 'form': form, 'partner': partner})


@login_required
@xframe_options_sameorigin
def partner_document_file(request, pk, doc_pk):
    permitted(request.user, 'view_partner')
    doc = get_object_or_404(PartnerDocument.objects.select_related('partner'), pk=doc_pk, partner_id=pk)
    if not _can_read_documents(request.user, doc.partner):
        raise PermissionDenied
    AuditEvent.objects.create(actor=request.user, action='Partner KYC downloaded', reference=f'partner:{pk}', detail=f'{doc.get_doc_type_display()} · {doc.file_name}')
    response = HttpResponse(bytes(doc.file), content_type=doc.content_type)
    response['Content-Disposition'] = content_disposition_header(bool(request.GET.get('download')), doc.file_name)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def partner_document_delete(request, pk, doc_pk):
    permitted(request.user, 'change_partner')
    doc = get_object_or_404(PartnerDocument.objects.defer('file'), pk=doc_pk, partner_id=pk)
    if doc.partner.approved_application_id:
        messages.error(request, 'Approved evidence is retained. Replace or withdraw it in a CRM amendment.')
        return redirect('partner_detail', pk=pk)
    label = doc.get_doc_type_display()
    AuditEvent.objects.create(actor=request.user, action='Partner document deleted', reference=f'partner:{pk}', detail=f'{label} · {doc.file_name}')
    doc.delete()
    messages.success(request, f'{label} removed.')
    return redirect('partner_detail', pk=pk)


@login_required
def people(request):
    """Sistech's channel in order — C&F, promoter/agent/transporter, dealer, retailer, mason — with the network tree."""
    if not (request.user.has_perm('erp.view_partner') or request.user.has_perm('erp.view_influencer')):
        raise PermissionDenied
    from django.urls import reverse
    from . import network as net
    can_partners, can_masons = request.user.has_perm('erp.view_partner'), request.user.has_perm('erp.view_influencer')
    levels = []
    for i, row in enumerate(net.level_summary(), 1):
        if row['key'] == 'mason':
            if not can_masons:
                continue
            row['url'], row['unlinked_url'] = reverse('influencers') + '?group=Mason', reverse('influencers') + '?dealer=none'
            row['hint'] = 'not linked to a dealer or retailer'
        else:
            if not can_partners:
                continue
            row['url'] = reverse('partners') + '?people_group=' + row['first']
            row['unlinked_url'] = row['url'] + '&upline=none'
            row['hint'] = {'agent': 'not under a C&F', 'dealer': 'deal with the company directly', 'retailer': 'not under a dealer'}.get(row['key'], '')
        row['n'] = i
        levels.append(row)
    q = request.GET.get('q', '').strip()[:60]
    tree = net.tree(q) if can_partners else None
    return render(request, 'erp/people.html', {'title': 'People', 'levels': levels, 'tree': tree, 'q': q})


@login_required
@require_POST
def partner_upline(request, pk):
    """Set or clear a partner's upline (C&F / promoter / dealer above it). Rules in erp.network.set_parent."""
    from django.core.exceptions import ValidationError
    from . import network as net
    permitted(request.user, 'change_partner')
    partner = get_object_or_404(Partner, pk=pk)
    raw = request.POST.get('parent', '')
    parent = get_object_or_404(Partner, pk=raw) if raw.isdigit() else None
    try:
        net.set_parent(request.user, partner, parent, request.POST.get('note', ''))
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    else:
        messages.success(request, f'{partner.name} now sits under {parent.name}.' if parent else f'{partner.name} now works directly with the company.')
    return redirect('partner_detail', pk=pk)
