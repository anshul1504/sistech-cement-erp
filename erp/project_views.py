"""Owner/site-scoped project pipeline, source links, evidence and forecast exports."""
import csv
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse,Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from . import projects
from .crm_views import _safe
from .models import SalesProject,ProjectLink,AuditEvent,PROJECT_STAGES,PROJECT_WEIGHTS
from .project_forms import ProjectForm,ActivityForm,LinkForm
from .services import permitted


def _project(request,pk):return get_object_or_404(projects.scope(SalesProject.objects.select_related('site','owner','partner','lead','referral__influencer'),request.user),pk=pk)


def _day(raw):return forms.DateField(required=False).clean(raw)


def _error(request,error):messages.error(request,' '.join(error.messages))


@login_required
def index(request):
    permitted(request.user,'view_salesproject')
    qs=projects.scope(SalesProject.objects.select_related('site','owner','partner'),request.user)
    q,stage,due=request.GET.get('q','').strip(),request.GET.get('stage',''),request.GET.get('due','')
    if q:qs=qs.filter(Q(number__icontains=q)|Q(name__icontains=q)|Q(location__icontains=q)|Q(district__icontains=q)|Q(partner__name__icontains=q)|Q(prospect_name__icontains=q))
    if stage in dict(PROJECT_STAGES):qs=qs.filter(stage=stage)
    today=timezone.localdate()
    if due=='due':qs=qs.filter(next_action_on__lte=today).exclude(stage__in=projects.CLOSED)
    elif due=='overdue':qs=qs.filter(next_action_on__lt=today).exclude(stage__in=projects.CLOSED)
    elif due=='today':qs=qs.filter(next_action_on=today).exclude(stage__in=projects.CLOSED)
    stages=projects.pipeline(request.user,qs)
    if request.GET.get('export')=='csv':
        permitted(request.user,'export_salesproject')
        response=HttpResponse(content_type='text/csv; charset=utf-8');response['Content-Disposition']=content_disposition_header(True,f'project-pipeline-{today}.csv');response['Cache-Control']='private, no-store'
        response.write('\ufeff');writer=csv.writer(response)
        writer.writerow(['Project','Name','Location','District','Buyer','Site','Owner','Stage','Estimated taxable value','Stage weight (%)','Expected cement bags','Expected TMT MT','Expected close','Next action'])
        for p in qs:writer.writerow([_safe(v) for v in [p.number,p.name,p.location,p.district,p.buyer_name,p.site.code,p.owner.get_username(),p.get_stage_display(),p.estimated_value,PROJECT_WEIGHTS[p.stage],p.cement_bags,p.tmt_mt,p.expected_close or '',p.next_action_on or '']])
        AuditEvent.objects.create(actor=request.user,action='Project pipeline exported',reference='Project CSV',detail='Current scoped and filtered estimates; no booked-sales assertion')
        return response
    return render(request,'erp/projects.html',dict(title='Project pipeline',page=Paginator(qs,25).get_page(request.GET.get('page')),q=q,stage=stage,due=due,stages=stages,
        open_value=sum(r['value'] for r in stages if r['key'] not in projects.CLOSED),weighted=sum(r['weighted'] for r in stages if r['key'] not in projects.CLOSED),
        overdue=qs.filter(next_action_on__lt=today).exclude(stage__in=projects.CLOSED).count()))


@login_required
def project_form(request,pk=None):
    permitted(request.user,'change_salesproject' if pk else 'add_salesproject')
    p=_project(request,pk) if pk else None
    if p:
        projects._editor(request.user,p)
        if p.stage in projects.CLOSED:messages.error(request,'A manager must reopen before editing a closed project.');return redirect('project_detail',pk=pk)
    form=ProjectForm(request.POST or None,instance=p,user=request.user)
    if request.method=='POST' and form.is_valid():
        try:p=projects.save(request.user,form.cleaned_data,pk)
        except ValidationError as e:form.add_error(None,e)
        else:messages.success(request,'Project saved with its owner, site and next action.');return redirect('project_detail',pk=p.pk)
    return render(request,'erp/project_form.html',dict(title='Edit project' if pk else 'New project opportunity',form=form))


@login_required
def detail(request,pk):
    permitted(request.user,'view_salesproject');p=_project(request,pk)
    links=list(p.links.select_related('quotation','order','created_by','unlinked_by'))
    warnings=[]
    if not p.partner_id and p.lead_id and p.lead.partner_id:warnings.append('The CRM prospect is now a customer. Edit the project to bind that approved customer before preparing new offers or linking orders.')
    active_orders={l.order_id for l in links if l.active and l.order_id}
    for row in links:row.attach_order_id=row.quotation.order_id if row.active and row.quotation_id and row.quotation.order_id not in active_orders else None
    if p.stage=='won' and not projects._valid_orders(p):warnings.append('The linked orders no longer satisfy win evidence. A manager should review and reopen this recorded outcome.')
    if p.stage in ['quoted','negotiation'] and not projects._valid_quotes(p):warnings.append('No currently valid sent/accepted offer remains. Refresh the quotation evidence or review the project stage.')
    editor=(p.owner_id==request.user.pk or request.user.has_perm('erp.manage_salesproject'))
    open_=p.stage not in projects.CLOSED
    return render(request,'erp/project_detail.html',dict(title=p.number,project=p,links=links,warnings=warnings,
        activities=p.activities.select_related('created_by'),events=p.events.select_related('actor'),activity_form=ActivityForm(prefix='activity',initial={'next_action_on':max(timezone.localdate(),p.next_action_on or timezone.localdate())}),link_form=LinkForm(user=request.user,project=p,prefix='source'),
        stages=PROJECT_STAGES,can_edit=editor and open_ and request.user.has_perm('erp.change_salesproject'),
        can_activity=editor and open_ and request.user.has_perm('erp.add_projectactivity'),can_reopen=not open_ and request.user.has_perm('erp.manage_salesproject'),
        invoice_summary=projects.invoice_summary(request.user,p)))


@login_required
@require_POST
def action(request,pk,action):
    p=_project(request,pk)
    try:
        note=request.POST.get('note','')
        if action=='activity':
            permitted(request.user,'add_projectactivity')
            form=ActivityForm(request.POST,prefix='activity')
            if not form.is_valid():raise projects._errors(form)
            projects.activity(request.user,pk,form.cleaned_data)
        elif action=='link':
            permitted(request.user,'change_salesproject')
            form=LinkForm(request.POST,user=request.user,project=p,prefix='source')
            if not form.is_valid():raise projects._errors(form)
            projects.link(request.user,pk,form.cleaned_data)
        elif action=='unlink':
            row=get_object_or_404(ProjectLink,pk=request.POST.get('link') if request.POST.get('link','').isascii() and request.POST.get('link','').isdecimal() else 0,project=p)
            projects.unlink(request.user,pk,row.pk,note)
        elif action=='stage':projects.change_stage(request.user,pk,request.POST.get('stage'),note,_day(request.POST.get('next_action_on')))
        elif action=='reopen':projects.reopen(request.user,pk,note,_day(request.POST.get('next_action_on')))
        else:raise Http404
        messages.success(request,'Project action recorded with its evidence.')
    except ValidationError as e:_error(request,e)
    return redirect('project_detail',pk=pk)
