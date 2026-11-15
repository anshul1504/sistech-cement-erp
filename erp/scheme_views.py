"""Permissioned scheme rules and non-posting calculation evidence."""
from accounts.delegation import can_approve
import csv
import json
from uuid import uuid4
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from . import schemes
from .crm import sites_for
from .crm_views import _safe
from .models import SchemeVersion, SchemeEvaluation, AuditEvent
from .scheme_forms import SchemeForm, VersionForm, Slabs, EvaluationForm
from .services import permitted


def _version(request, pk):
    return get_object_or_404(schemes.scope(SchemeVersion.objects.select_related('scheme__site', 'product', 'partner', 'created_by', 'submitted_by', 'decided_by'), request.user, 'scheme'), pk=pk)


def _evaluation(request, pk):
    return get_object_or_404(schemes.scope(SchemeEvaluation.objects.select_related('version__scheme__site', 'created_by'), request.user, 'version__scheme'), pk=pk)


@login_required
def index(request):
    permitted(request.user, 'view_incentivescheme')
    qs = schemes.scope(SchemeVersion.objects.select_related('scheme__site', 'product', 'partner'), request.user, 'scheme')
    q, status, site = request.GET.get('q', '').strip(), request.GET.get('status', ''), request.GET.get('site', '')
    if q: qs = qs.filter(Q(scheme__code__icontains=q) | Q(scheme__name__icontains=q))
    if status in ['draft', 'submitted', 'approved', 'rejected']: qs = qs.filter(status=status)
    if site.isdigit(): qs = qs.filter(scheme__site_id=site)
    evaluations = schemes.scope(SchemeEvaluation.objects.select_related('version__scheme', 'created_by'), request.user, 'version__scheme')[:10] if request.user.has_perm('erp.view_schemeevaluation') else []
    return render(request, 'erp/schemes.html', dict(title='Schemes & incentives', page=Paginator(qs.order_by('-created_at', '-pk'), 25).get_page(request.GET.get('page')),
        q=q, status=status, site=site, sites=sites_for(request.user), evaluations=evaluations))


@login_required
def edit(request, pk=None):
    permitted(request.user, 'change_schemeversion' if pk else 'add_incentivescheme')
    version = _version(request, pk) if pk else None
    if version and version.status != 'draft':
        messages.error(request, 'These rules are locked. Prepare a new version to change them.')
        return redirect('scheme_detail', pk=pk)
    header = SchemeForm(request.POST or None, prefix='scheme', user=request.user) if not version else None
    form = VersionForm(request.POST or None, instance=version)
    initial = list(version.slabs.values('minimum_sales', 'percentage')) if version else [dict(minimum_sales='0', percentage='1')]
    slabs = Slabs(request.POST or None, prefix='slabs', initial=initial)
    if request.method == 'POST':
        valid = [form.is_valid(), slabs.is_valid(), header.is_valid() if header else True]
        if all(valid):
            rows = [{k: row.cleaned_data[k] for k in ['minimum_sales', 'percentage']} for row in slabs if row.cleaned_data and not row.cleaned_data.get('DELETE')]
            try:
                version = schemes.save_rules(request.user, header.cleaned_data if header else {}, form.cleaned_data, rows, pk)
            except ValidationError as e:
                form.add_error(None, e)
            else:
                messages.success(request, 'Draft rules saved. Independent approval is required before calculations.')
                return redirect('scheme_detail', pk=version.pk)
    return render(request, 'erp/scheme_form.html', dict(title='Edit scheme draft' if pk else 'New incentive scheme', version=version, header=header, form=form, slabs=slabs))


@login_required
def detail(request, pk):
    permitted(request.user, 'view_incentivescheme')
    version = _version(request, pk)
    today = timezone.localdate()
    form = EvaluationForm(initial=dict(starts_on=version.starts_on, ends_on=min(version.ends_on, today), submission_key=uuid4()))
    can_decide = can_approve(request.user, 'approve_schemeversion', version.scheme.site, makers=[version.created_by_id, version.updated_by_id, version.submitted_by_id, *version.contributors.values_list('pk', flat=True)]) and version.status == 'submitted' and request.user.pk not in [version.created_by_id, version.updated_by_id, version.submitted_by_id] and not version.contributors.filter(pk=request.user.pk).exists()
    return render(request, 'erp/scheme_detail.html', dict(title=str(version), version=version, form=form, rules=schemes.rule_snapshot(version),
        versions=version.scheme.versions.all(), can_decide=can_decide,
        can_revise=request.user.has_perm('erp.add_incentivescheme') and not version.scheme.versions.filter(status__in=['draft','submitted']).exists(),
        evaluations=version.evaluations.select_related('created_by')[:10] if request.user.has_perm('erp.view_schemeevaluation') else [],
        history=AuditEvent.objects.filter(reference=f'scheme:{version.scheme_id}', site=version.scheme.site).select_related('actor').order_by('-created_at')[:20]))


@login_required
@require_POST
def action(request, pk, action):
    version = _version(request, pk)
    try:
        note=request.POST.get('note', '')
        if action=='submit': schemes.submit(request.user,pk,note)
        elif action in ['approve','reject']: schemes.decide(request.user,pk,action=='approve',note)
        elif action=='revise':
            draft=schemes.revise(request.user,version.scheme_id)
            return redirect('scheme_edit',pk=draft.pk)
        elif action=='calculate':
            permitted(request.user,'add_schemeevaluation')
            form=EvaluationForm(request.POST)
            if not form.is_valid():
                raise ValidationError([f'{k}: {m}' for k, values in form.errors.items() for m in values])
            result=schemes.calculate(request.user,pk,**form.cleaned_data)
            messages.success(request,'Calculation preview retained. No payout or accounting entry was created.')
            return redirect('scheme_evaluation',pk=result.pk)
        else:
            raise ValidationError('Unknown scheme action.')
        messages.success(request,'Scheme decision recorded.')
    except ValidationError as e:
        messages.error(request,' '.join(e.messages))
    return redirect('scheme_detail',pk=pk)


@login_required
def evaluation(request,pk):
    permitted(request.user,'view_schemeevaluation')
    obj=_evaluation(request,pk)
    rows=obj.snapshot.get('dealers',[])
    if request.GET.get('export')=='json':
        permitted(request.user,'export_schemeevaluation')
        response=HttpResponse(json.dumps(dict(calculation=str(obj),created_at=obj.created_at.isoformat(),source_digest=obj.source_digest,snapshot=obj.snapshot),indent=2),content_type='application/json')
        response['Content-Disposition']=content_disposition_header(True,f'scheme-evidence-{obj.pk}.json')
        response['Cache-Control']='private, no-store'
        response['X-Content-Type-Options']='nosniff'
        schemes._audit(request.user,obj.version.scheme,'Scheme evidence exported',f'{obj}; complete retained rule and source snapshot')
        return response
    if request.GET.get('export')=='csv':
        permitted(request.user,'export_schemeevaluation')
        response=HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition']=content_disposition_header(True,f'scheme-calculation-{obj.pk}.csv')
        response['Cache-Control']='private, no-store'
        response.write('\ufeff'); writer=csv.writer(response)
        writer.writerow(['Calculation','Scheme','Version','Invoice period from','Invoice period to','Observed on','Customer','Name','Net taxable sales','Eligible base','Slab minimum','Percentage','Preview incentive'])
        for r in rows:
            writer.writerow([_safe(x) for x in [str(obj),obj.snapshot['rules']['scheme'],obj.snapshot['rules']['revision'],obj.starts_on,obj.ends_on,obj.snapshot['observed_on'],r['code'],r['name'],r['taxable'],r['eligible'],r['threshold'],r['percentage'],r['incentive']]])
        schemes._audit(request.user,obj.version.scheme,'Scheme preview exported',f'{obj}; retained dealer summary, no payout')
        return response
    return render(request,'erp/scheme_evaluation.html',dict(title=str(obj),evaluation=obj,rows=Paginator(rows,20).get_page(request.GET.get('page'))))


@login_required
@require_POST
def check_sources(request,pk):
    obj=_evaluation(request,pk)
    try:
        current=schemes.check_sources(request.user,pk)
        if current: messages.success(request,'Current source documents and rules match this retained calculation.')
        else: messages.warning(request,'Source data has changed. This retained calculation is unchanged; generate a fresh preview before using the amounts.')
    except ValidationError as e:
        messages.error(request,' '.join(e.messages))
    return redirect('scheme_evaluation',pk=obj.pk)
