import csv
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction, IntegrityError
from django.db.models import Q, F
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin
from .models import CUSTOMER_KINDS, Site, Product, Partner, Order, StockBalance, StockMovement, Production, QualityTest, AuditEvent, Organization, OrganizationDocument, DOCUMENT_TYPES
from .forms import SiteForm, ProductForm, PartnerForm, OrderForm, Lines, ProductionForm, QualityForm, OpeningForm, OrganizationForm, OrganizationDocumentForm
from .services import scope, permitted, audit, move_stock, transition_order, transition_production
from accounts.roles import order_permission

def sites(user):
    qs = Site.objects.filter(active=True)
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)

def table(request, title, subtitle, headers, rows, create=None, filters=False, create_label='+ New record'):
    return render(request, 'erp/list.html', {'title': title, 'subtitle': subtitle, 'headers': headers, 'page': Paginator(rows, 15).get_page(request.GET.get('page')),
                                             'create': create, 'filters': filters, 'create_label': create_label})

@login_required
@require_POST
def order_action(request, pk, action):
    get_object_or_404(scope(Order.objects.all(), request.user), pk=pk)
    P = request.POST
    try:
        if action == 'revise':
            from .sales import revise
            new = revise(request.user, pk)
            messages.success(request, f'Revision {new.revision} opened as draft {new.number}. Make the changes and submit it for approval.')
            return redirect('order_detail', pk=new.pk)
        from datetime import date
        until = date.fromisoformat(P['override_until']) if P.get('override_until') else None
        transition_order(request.user, pk, action, reason=P.get('reason', ''), override_reason=P.get('override_reason', ''), override_until=until,
                         backorder_reason=P.get('backorder_reason', ''), confirm_duplicate=P.get('confirm_duplicate') == '1')
        messages.success(request, {'submit': 'Submitted for approval.', 'approve': 'Order approved.', 'cancel': 'Order cancelled.', 'close': 'Order short-closed.',
                                   'hold': 'Order put on hold — no truck can be planned until it is released.', 'release': 'Order released from hold.',
                                   'post': 'Stock posted.'}.get(action, 'Order workflow updated.'))
    except ValueError:
        messages.error(request, 'Enter the override expiry date correctly.')
    except ValidationError as e: messages.error(request, ' '.join(e.messages))
    return redirect('order_detail', pk=pk)

@login_required
def opening(request):
    permitted(request.user, 'post_opening')
    form = OpeningForm(request.POST or None)
    form.fields['site'].queryset = sites(request.user)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic():
                obj = move_stock(request.user, d['site'], d['product'], d['quantity'], 'opening:' + d['reference'], 'Opening', d['notes'], unit_cost=d.get('unit_cost'))
                audit(request.user, 'Opening stock posted', obj, d['notes'])
            messages.success(request, 'Opening stock posted.')
            return redirect('stock_card', site_pk=d['site'].pk, product_pk=d['product'].pk)
        except IntegrityError: form.add_error('reference', 'This reference has already been posted.')
    return render(request, 'erp/form.html', {'title': 'Post opening stock', 'form': form})

@login_required
def quality_new(request):
    permitted(request.user, 'add_qualitytest')
    form = QualityForm(request.POST or None)
    form.fields['production'].queryset = scope(Production.objects.filter(status='Quality hold'), request.user)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            batch = Production.objects.select_for_update().get(pk=form.cleaned_data['production'].pk)
            if batch.status != 'Quality hold':
                form.add_error('production', 'Batch is no longer on quality hold.')
            else:
                obj = form.save(commit=False)
                obj.created_by, obj.site = request.user, batch.site
                obj.save()
                audit(request.user, 'Quality test recorded', obj, f'{batch.batch}: {obj.disposition}')
                return redirect('production_detail', pk=batch.pk)
    return render(request, 'erp/form.html', {'title': 'Record quality test', 'form': form})

@login_required
def audit_list(request):
    permitted(request.user, 'view_auditevent')
    qs = AuditEvent.objects.select_related('actor', 'site')
    if not request.user.is_superuser: qs = scope(qs, request.user)
    return table(request, 'Audit trail', 'Read-only history of workflow actions and master changes.', ['When', 'User', 'Action', 'Reference', 'Detail'], [{'cells': [e.created_at, e.actor, e.action, e.reference, e.detail]} for e in qs])

@login_required
def reports(request):
    permitted(request.user, 'view_stockmovement')
    movements = scope(StockMovement.objects.select_related('product', 'site', 'created_by'), request.user)
    if request.GET.get('export') == 'csv':
        permitted(request.user, 'export_stockmovement')
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="stock-ledger.csv"'
        writer = csv.writer(response)
        writer.writerow(['Date (Asia/Kolkata)', 'Site', 'SKU', 'Product', 'Quantity', 'UOM', 'Type', 'Source'])
        from django.utils.timezone import localtime
        def safe(value):
            value = str(value)
            return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
        for m in movements:
            writer.writerow([safe(v) for v in [localtime(m.created_at).isoformat(), m.site.code, m.product.code, m.product.name, m.quantity, m.product.unit, m.kind, m.source]])
        AuditEvent.objects.create(actor=request.user, action='Stock ledger exported', reference='CSV', detail='All permitted sites')
        return response
    return table(request, 'Stock movement report', 'Source-linked posted movements. Export contains only your permitted sites.', ['Document', 'Site', 'Product', 'Quantity', 'UOM', 'Type', 'Source'], [{'cells': [m.number, m.site.code, m.product.name, m.quantity, m.product.unit, m.kind, m.source]} for m in movements], '/reports/?export=csv' if request.user.has_perm('erp.export_stockmovement') else None, False, '↓ Export CSV')


@login_required
def organization(request):
    """Organisation overview: every detail on one read-only page."""
    _, can_edit = organization_access(request.user)
    org = Organization.current()
    locations = {'plants': Site.objects.filter(active=True, kind='Plant').count(),
                 'depots': Site.objects.filter(active=True).exclude(kind='Plant').count()}
    return render(request, 'erp/organization.html', {'title': 'Organisation setup', 'org': org, 'can_edit': can_edit,
                                                      'locations': locations, 'counts': document_counts()})


@login_required
def organization_edit(request):
    """Set up or edit the organisation (step tabs, one save). Needs erp.change_organization."""
    _require_edit(request.user)
    org = Organization.current()
    form = OrganizationForm(request.POST or None, request.FILES or None, instance=org)
    if request.method == 'POST' and form.is_valid():
        changed = list(form.changed_data)
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        AuditEvent.objects.create(actor=request.user, action='Organisation updated', reference='organisation', detail='Changed: ' + (', '.join(changed) or 'nothing'))
        messages.success(request, 'Organisation details saved. The new branding is now shown to everyone.')
        return redirect('organization')
    return render(request, 'erp/organization_form.html', {'title': 'Edit organisation', 'form': form, 'org': org, 'counts': document_counts()})


def brand_asset(request, kind):
    """Serve the uploaded logo/icon. Public (needed on the sign-in page); images only."""
    if kind not in ('logo', 'icon'):
        raise Http404
    org = Organization.objects.filter(pk=1).first()
    data = getattr(org, kind, None) if org else None
    if not data:
        raise Http404
    response = HttpResponse(bytes(data), content_type=getattr(org, f'{kind}_type') or 'application/octet-stream')
    response['Cache-Control'] = 'public, max-age=31536000, immutable' if request.GET.get('v') else 'public, max-age=300'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response['Content-Disposition'] = f'inline; filename="organisation-{kind}"'
    return response


def organization_access(user):
    """(can_view, can_edit) for Organisation setup and its documents."""
    from django.core.exceptions import PermissionDenied
    can_edit = user.has_perm('erp.change_organization')
    if not (can_edit or user.has_perm('erp.view_organization')):
        raise PermissionDenied
    return True, can_edit


def document_counts():
    docs = list(OrganizationDocument.objects.only('valid_until'))
    states = [d.status[0] for d in docs]
    return {'total': len(docs), 'expired': states.count('expired'), 'expiring': states.count('expiring')}


def _require_edit(user):
    from django.core.exceptions import PermissionDenied
    _, can_edit = organization_access(user)
    if not can_edit:
        raise PermissionDenied


@login_required
def organization_documents(request):
    _, can_edit = organization_access(request.user)
    kind, status = request.GET.get('type', ''), request.GET.get('status', '')
    q = request.GET.get('q', '').strip()
    docs = OrganizationDocument.objects.defer('file').select_related('site', 'uploaded_by')
    if kind:
        docs = docs.filter(doc_type=kind)
    if q:
        docs = docs.filter(Q(title__icontains=q) | Q(number__icontains=q) | Q(file_name__icontains=q) | Q(notes__icontains=q))
    rows = [d for d in docs if not status or d.status[0] == status]
    return render(request, 'erp/organization_documents.html', {
        'title': 'Company documents', 'can_edit': can_edit, 'documents': rows, 'counts': document_counts(),
        'document_types': DOCUMENT_TYPES, 'selected_type': kind, 'selected_status': status, 'query': q})


@login_required
def organization_document_form(request, pk=None):
    """Create (upload) or edit a company document. On edit the file is optional (replace)."""
    _require_edit(request.user)
    doc = get_object_or_404(OrganizationDocument, pk=pk) if pk else None
    initial = {'site': request.GET.get('site')} if not pk and request.GET.get('site', '').isdigit() else None
    form = OrganizationDocumentForm(request.POST or None, request.FILES or None, instance=doc, initial=initial)
    if request.method == 'POST' and form.is_valid():
        replaced = bool(form.cleaned_data.get('upload'))
        saved = form.save(commit=False)
        if not pk:
            saved.uploaded_by = request.user
        saved.save()
        action = 'Organisation document updated' if pk else 'Organisation document uploaded'
        detail = f'{saved.display_title} · changed: {", ".join(form.changed_data) or "nothing"}' if pk else f'{saved.display_title} · {saved.file_name} ({saved.size} bytes)'
        AuditEvent.objects.create(actor=request.user, site=saved.site, action=action, reference=f'orgdoc:{saved.pk}', detail=detail + (' · file replaced' if pk and replaced else ''))
        messages.success(request, f'“{saved.display_title}” {"updated" if pk else "uploaded"}.')
        return redirect('organization_document', pk=saved.pk)
    return render(request, 'erp/organization_document_form.html', {
        'title': 'Edit document' if pk else 'Upload document', 'form': form, 'doc': doc, 'counts': document_counts()})


@login_required
def organization_document(request, pk):
    _, can_edit = organization_access(request.user)
    doc = get_object_or_404(OrganizationDocument.objects.defer('file').select_related('site', 'uploaded_by'), pk=pk)
    history = AuditEvent.objects.filter(reference=f'orgdoc:{doc.pk}').select_related('actor')[:10]
    return render(request, 'erp/organization_document.html', {
        'title': doc.display_title, 'doc': doc, 'can_edit': can_edit, 'history': history, 'counts': document_counts()})


@login_required
@xframe_options_sameorigin
def organization_document_file(request, pk):
    organization_access(request.user)
    doc = get_object_or_404(OrganizationDocument, pk=pk)
    response = HttpResponse(bytes(doc.file), content_type=doc.content_type)
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="{doc.file_name}"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_POST
def organization_document_delete(request, pk):
    _require_edit(request.user)
    doc = get_object_or_404(OrganizationDocument.objects.defer('file'), pk=pk)
    title = doc.display_title
    AuditEvent.objects.create(actor=request.user, site=doc.site, action='Organisation document deleted', reference=f'orgdoc:{doc.pk}', detail=f'{title} · {doc.file_name}')
    doc.delete()
    messages.success(request, f'“{title}” deleted.')
    return redirect('organization_documents')

