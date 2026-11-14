"""Dealer CRM screens: pipeline, evidence, independent reviews and performance."""
import csv
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from . import crm
from .crm_forms import LeadForm, ActivityForm, DealerProfileForm, DealerUploadForm, PolicyForm, TargetForm, PROFILE_FIELDS
from .models import (CRMLead, CRMPolicy, DealerApplication, DealerDocument, SalesTarget, Partner, AuditEvent, LEAD_STAGES, CUSTOMER_KINDS,
                     APPLICATION_STATES, PARTNER_DOCUMENT_TYPES, DealerReview, Invoice, CreditNote, Settlement)
from .services import permitted


def _lead(request, pk):
    return get_object_or_404(crm.lead_scope(CRMLead.objects.select_related('site', 'owner', 'partner'), request.user), pk=pk)


def _app(request, pk):
    return get_object_or_404(crm.lead_scope(DealerApplication.objects.select_related('site', 'lead__owner', 'partner', 'created_by', 'submitted_by', 'kyc_by', 'credit_by', 'approved_by'), request.user, 'lead'), pk=pk)


def _profile_rows(profile):
    form = DealerProfileForm(initial=profile)
    return [dict(label=form.fields[k].label, value=dict(form.fields[k].choices).get(profile.get(k), profile.get(k, '')) if k in ['state', 'kind'] else profile.get(k, ''), money=k in ['credit_limit', 'security_deposit']) for k in PROFILE_FIELDS]


def _error(request, error):
    messages.error(request, ' '.join(error.messages))


def _safe(value):
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


def _csv(request, filename, header, rows, action):
    permitted(request.user, 'export_crmlead')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = content_disposition_header(True, filename)
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe(value) for value in row])
    AuditEvent.objects.create(actor=request.user, action=action, reference='CRM CSV', detail='Exported currently scoped and filtered records')
    return response


@login_required
def leads(request):
    permitted(request.user, 'view_crmlead')
    base = crm.lead_scope(CRMLead.objects.select_related('site', 'owner', 'partner'), request.user)
    qs = base
    q, stage, due = request.GET.get('q', '').strip(), request.GET.get('stage', ''), request.GET.get('due', '')
    site, owner = request.GET.get('site', ''), request.GET.get('owner', '')
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(business_name__icontains=q) | Q(mobile__icontains=q) | Q(district__icontains=q) | Q(territory__icontains=q))
    if stage in dict(LEAD_STAGES): qs = qs.filter(stage=stage)
    if site.isdigit(): qs = qs.filter(site_id=site)
    if owner.isdigit(): qs = qs.filter(owner_id=owner)
    today = timezone.localdate()
    if due == 'overdue': qs = qs.filter(next_follow_up__lt=today).exclude(stage__in=['won', 'lost'])
    elif due == 'today': qs = qs.filter(next_follow_up=today).exclude(stage__in=['won', 'lost'])
    if request.GET.get('export') == 'csv':
        return _csv(request, f'crm-pipeline-{today}.csv', ['Lead', 'Business', 'Contact', 'Mobile', 'District', 'Territory', 'Site', 'Owner', 'Stage', 'Follow-up', 'Estimated opportunity'],
            ([l.number, l.business_name, l.contact_name, l.mobile, l.district, l.territory, l.site.code, l.owner.get_username(), l.get_stage_display(), l.next_follow_up or '', l.estimated_value] for l in qs), 'CRM pipeline exported')
    counts = dict(base.values_list('stage').annotate(n=Count('pk')))
    return render(request, 'erp/crm_leads.html', dict(title='Dealer CRM', page=Paginator(qs, 30).get_page(request.GET.get('page')), q=q, stage=stage, due=due, site=site, owner=owner,
        sites=crm.sites_for(request.user), owners=crm.owners_for(request.user) if request.user.has_perm('erp.view_team_crm') else [request.user],
        stages=[dict(key=k, label=label, count=counts.get(k, 0)) for k, label in LEAD_STAGES],
        open_count=base.exclude(stage__in=['won', 'lost']).count(), overdue=base.filter(next_follow_up__lt=today).exclude(stage__in=['won', 'lost']).count(),
        converted=base.filter(stage='won').count(), opportunity=sum((l.estimated_value for l in base.exclude(stage__in=['won', 'lost'])), Decimal(0)),
        can_add=request.user.has_perm('erp.add_crmlead'), can_export=request.user.has_perm('erp.export_crmlead')))


@login_required
def lead_form(request, pk=None):
    permitted(request.user, 'change_crmlead' if pk else 'add_crmlead')
    lead = _lead(request, pk) if pk else None
    form = LeadForm(request.POST or None, instance=lead, user=request.user)
    if request.method == 'POST' and form.is_valid():
        try:
            lead = crm.save_lead(request.user, form.cleaned_data, pk)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, 'Lead saved with its owner and originating site.')
            return redirect('crm_lead_detail', pk=lead.pk)
    return render(request, 'erp/crm_form.html', dict(title='Edit lead' if pk else 'New lead', form=form, lead=lead, back='crm_leads', submit='Save lead',
        description='Capture the prospect, owner and next follow-up. Duplicate mobile numbers at the same site are blocked.'))


@login_required
def lead_detail(request, pk):
    permitted(request.user, 'view_crmlead')
    lead = _lead(request, pk)
    open_app = lead.applications.filter(status__in=crm.OPEN_APPLICATIONS).first()
    return render(request, 'erp/crm_lead_detail.html', dict(title=lead.business_name, lead=lead, activity_form=ActivityForm(),
        activities=lead.activities.select_related('created_by'), applications=lead.applications.select_related('created_by', 'approved_by'), open_app=open_app,
        stages=[(key, label) for key, label in LEAD_STAGES if key != 'won'],
        can_edit=request.user.has_perm('erp.change_crmlead') and lead.stage not in ['won', 'lost'],
        can_stage=request.user.has_perm('erp.change_crmlead') and lead.stage != 'won' and not open_app,
        can_activity=request.user.has_perm('erp.add_crmactivity') and lead.stage not in ['won', 'lost'],
        can_apply=request.user.has_perm('erp.add_dealerapplication') and lead.stage != 'lost' and not open_app,
        can_link=request.user.has_perm('erp.change_crmlead') and request.user.has_perm('erp.view_partner') and lead.stage not in ['won', 'lost'] and not open_app,
        customers=Partner.objects.filter(active=True, kind__in=CUSTOMER_KINDS).order_by('name') if request.user.has_perm('erp.view_partner') else [],
        history=AuditEvent.objects.filter(reference=lead.number).select_related('actor').order_by('-created_at')[:30]))


@login_required
@require_POST
def lead_action(request, pk, action):
    _lead(request, pk)
    try:
        if action == 'stage':
            crm.change_stage(request.user, pk, request.POST.get('stage'), request.POST.get('reason', ''))
            messages.success(request, 'Lead stage updated.')
        elif action == 'activity':
            permitted(request.user, 'add_crmactivity')
            form = ActivityForm(request.POST)
            if not form.is_valid():
                raise ValidationError([f'{k}: {m}' for k, values in form.errors.items() for m in values])
            crm.record_activity(request.user, pk, form.cleaned_data)
            messages.success(request, 'Activity and next follow-up recorded.')
        elif action == 'apply':
            app = crm.start_application(request.user, pk)
            messages.success(request, 'Application prepared. Complete the profile and KYC evidence before submission.')
            return redirect('crm_application_detail', pk=app.pk)
        elif action == 'link':
            partner = get_object_or_404(Partner, pk=request.POST.get('partner') or 0)
            crm.link_existing(request.user, pk, partner, request.POST.get('reason', ''))
            messages.success(request, 'Existing active customer linked; no new credit or master was created.')
        else:
            raise Http404
    except ValidationError as e:
        _error(request, e)
    return redirect('crm_lead_detail', pk=pk)


@login_required
def applications(request):
    permitted(request.user, 'view_dealerapplication')
    qs = crm.lead_scope(DealerApplication.objects.select_related('lead__owner', 'site', 'created_by', 'partner'), request.user, 'lead')
    status, q = request.GET.get('status', ''), request.GET.get('q', '').strip()
    if status in dict(APPLICATION_STATES): qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(number__icontains=q) | Q(lead__business_name__icontains=q))
    return render(request, 'erp/crm_applications.html', dict(title='Dealer onboarding', page=Paginator(qs, 30).get_page(request.GET.get('page')), status=status, q=q, statuses=APPLICATION_STATES))


@login_required
def application_detail(request, pk):
    permitted(request.user, 'view_dealerapplication')
    app = _app(request, pk)
    form = DealerProfileForm(initial=app.profile)
    independent = True
    try:
        crm._independent(app, request.user)
    except ValidationError:
        independent = False
    lane = {'submitted': 'review_dealerkyc', 'kyc': 'review_dealercredit', 'credit': 'approve_dealerapplication'}.get(app.status)
    can_review = independent and lane and request.user.has_perm('erp.' + lane)
    if app.status == 'kyc' and app.kyc_by_id == request.user.pk: can_review = False
    if app.status == 'credit' and app.credit_by_id == request.user.pk: can_review = False
    credit_position = None
    if app.partner:
        from .partner_views import credit_position as position
        credit_position = position(app.partner)
    policy = app.submission_snapshot.get('policy') or dict(required_documents=CRMPolicy.objects.get_or_create(pk=1)[0].required_documents)
    required = set(policy['required_documents'])
    if app.profile.get('gstin'): required.add('gst_certificate')
    if app.profile.get('bank_account_number'): required.add('cancelled_cheque')
    present = set(crm.current_documents(app).values_list('doc_type', flat=True))
    return render(request, 'erp/crm_application_detail.html', dict(title=app.number, app=app, profile_rows=_profile_rows(app.profile),
        documents=app.documents.defer('file').select_related('uploaded_by'), reviews=app.reviews.select_related('actor'), upload_form=DealerUploadForm(),
        required_items=[dict(label=dict(PARTNER_DOCUMENT_TYPES).get(k, k), present=k in present) for k in sorted(required)], credit_position=credit_position,
        can_edit=app.status in ['draft', 'changes'] and request.user.has_perm('erp.change_dealerapplication'),
        can_submit=app.status in ['draft', 'changes'] and request.user.has_perm('erp.submit_dealerapplication'), can_review=can_review,
        can_download=request.user.has_perm('erp.view_dealerdocument')))


@login_required
def application_edit(request, pk):
    permitted(request.user, 'change_dealerapplication')
    app = _app(request, pk)
    form = DealerProfileForm(request.POST or None, initial=app.profile)
    if request.method == 'POST' and form.is_valid():
        try:
            crm.save_application(request.user, pk, form.cleaned_data)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, 'Application profile saved. Submission freezes the reviewed fields and evidence.')
            return redirect('crm_application_detail', pk=pk)
    return render(request, 'erp/crm_profile_form.html', dict(title='Dealer profile & proposed terms', app=app, form=form))


@login_required
@require_POST
def application_action(request, pk, action):
    _app(request, pk)
    try:
        note = request.POST.get('note', '')
        if action == 'upload':
            crm.upload_document(request.user, pk, request.POST, request.FILES)
            messages.success(request, 'New KYC evidence version saved; earlier versions are retained.')
        elif action == 'withdraw_document':
            doc = get_object_or_404(DealerDocument, pk=request.POST.get('document') or 0, application_id=pk)
            crm.withdraw_document(request.user, pk, doc.pk, note)
            messages.success(request, 'Evidence withdrawn; its original file remains in the history.')
        elif action == 'submit':
            crm.submit_application(request.user, pk, note)
            messages.success(request, 'Submitted for independent KYC review.')
        elif action == 'withdraw':
            crm.withdraw_application(request.user, pk, note)
            messages.success(request, 'Application withdrawn with its evidence retained.')
        elif action in ['kyc', 'credit', 'approve', 'changes', 'reject']:
            app = crm.decide_application(request.user, pk, action, note, request.POST.get('credit'), request.POST.get('terms'))
            messages.success(request, 'Dealer activated / amendment applied.' if app.status == 'approved' else 'Review decision recorded.')
        else:
            raise Http404
    except ValidationError as e:
        _error(request, e)
    return redirect('crm_application_detail', pk=pk)


@login_required
def document_file(request, pk):
    permitted(request.user, 'view_dealerdocument')
    doc = get_object_or_404(crm.lead_scope(DealerDocument.objects.select_related('application'), request.user, 'application__lead'), pk=pk)
    response = HttpResponse(bytes(doc.file), content_type=doc.content_type)
    response['Content-Disposition'] = content_disposition_header(True, doc.file_name)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    AuditEvent.objects.create(actor=request.user, site=doc.application.site, action='Dealer KYC downloaded', reference=doc.application.number,
                              detail=f'{doc.get_doc_type_display()} version {doc.version}')
    return response


@login_required
def policy(request):
    permitted(request.user, 'manage_crmpolicy')
    obj = CRMPolicy.objects.get_or_create(pk=1)[0]
    form = PolicyForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        crm.save_policy(request.user, form.cleaned_data['required_documents'], form.cleaned_data['require_gstin'])
        messages.success(request, 'Onboarding policy version updated. New submissions use the new policy; existing reviews retain their submitted policy.')
        return redirect('crm_policy')
    return render(request, 'erp/crm_form.html', dict(title='Dealer onboarding policy', form=form, back='crm_leads', submit='Save policy',
        description='Choose the company’s required evidence. GST certificates are also required when a GSTIN is supplied; cancelled cheques when bank details are supplied. These are manual reviews, not external registry verification.'))


@login_required
def performance(request):
    permitted(request.user, 'view_crmperformance')
    try:
        month = date.fromisoformat((request.GET.get('month') or timezone.localdate().strftime('%Y-%m')) + '-01')
    except ValueError:
        month = timezone.localdate().replace(day=1)
        messages.error(request, 'Choose a valid month.')
    rows = crm.performance(request.user, month)
    target = None
    if request.method == 'POST':
        permitted(request.user, 'manage_salestarget')
        try:
            raw_month = date.fromisoformat(request.POST.get('month', '')).replace(day=1)
            if request.POST.get('site', '').isdigit() and request.POST.get('owner', '').isdigit():
                target = SalesTarget.objects.filter(site__in=crm.sites_for(request.user), site_id=request.POST['site'], owner_id=request.POST['owner'], month=raw_month).first()
        except ValueError:
            pass
    form = TargetForm(request.POST or None, instance=target, user=request.user, initial=dict(month=month))
    if request.method == 'POST' and form.is_valid():
        try:
            target = crm.save_target(request.user, form.cleaned_data)
        except ValidationError as e:
            form.add_error(None, e)
        else:
            messages.success(request, 'Monthly target saved and audited.')
            return redirect(f'{request.path}?month={target.month:%Y-%m}')
    if request.GET.get('export') == 'csv':
        return _csv(request, f'crm-performance-{month:%Y-%m}.csv', ['Site', 'Salesperson', 'Sales target', 'Net taxable sales', 'Allocated collections', 'Current overdue', 'New dealers'],
            ([r['site'].code, r['owner'].get_username(), r['target'].sales_amount if r['target'] else '', r['sales'], r['collections'], r['overdue'], r['new_dealers']] for r in rows), 'CRM performance exported')
    return render(request, 'erp/crm_performance.html', dict(title='Sales targets & performance', month=month, rows=rows, form=form, can_manage=request.user.has_perm('erp.manage_salestarget'), can_export=request.user.has_perm('erp.export_crmlead')))


@login_required
def review_detail(request, pk):
    permitted(request.user, 'view_dealerapplication')
    review = get_object_or_404(crm.lead_scope(DealerReview.objects.select_related('application', 'actor'), request.user, 'application__lead'), pk=pk)
    ids = [d['id'] for d in review.snapshot.get('documents', [])]
    return render(request, 'erp/crm_review_detail.html', dict(title='Retained review evidence', review=review,
        profile_rows=_profile_rows(review.snapshot.get('profile', {})),
        documents=review.application.documents.filter(pk__in=ids).defer('file'), can_download=request.user.has_perm('erp.view_dealerdocument')))


@login_required
def performance_detail(request, site_pk, owner_pk):
    permitted(request.user, 'view_crmperformance')
    import calendar
    try:
        month = date.fromisoformat((request.GET.get('month') or timezone.localdate().strftime('%Y-%m')) + '-01')
    except ValueError:
        raise Http404
    row = next((r for r in crm.performance(request.user, month) if r['site'].pk == site_pk and r['owner'].pk == owner_pk), None)
    if row is None:
        raise Http404
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    invoices = Invoice.objects.filter(site_id=site_pk, order__created_by_id=owner_pk)
    activations = DealerApplication.objects.filter(site_id=site_pk, status='approved', base_partner_fingerprint='',
        approved_at__date__range=(month, end), submission_snapshot__owner=owner_pk).select_related('partner')
    return render(request, 'erp/crm_performance_detail.html', dict(title='Sales source records', month=month, row=row,
        invoices=invoices.filter(date__range=(month, end)).select_related('partner')[:100],
        credits=CreditNote.objects.filter(invoice__in=invoices, date__range=(month, end)).select_related('invoice')[:100],
        allocations=Settlement.objects.filter(invoice__in=invoices, receipt__date__range=(month, end), receipt__status='posted').select_related('invoice', 'receipt')[:100],
        activations=activations[:100]))
