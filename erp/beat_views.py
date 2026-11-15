"""Territory ownership, daily plans, field outcomes and coverage reports."""
from accounts.delegation import can_approve
import csv
from datetime import timedelta
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from . import beats, beat_cover
from .crm import sites_for
from .crm_views import _safe
from .models import SalesTerritory,DealerTerritoryAssignment,BeatPlan,BeatStop,AuditEvent
from .beat_forms import TerritoryForm,AssignmentForm,PlanForm,StopForm,OutcomeForm
from .services import permitted


def _plan(request,pk):
    return get_object_or_404(beats.scope(BeatPlan.objects.select_related('site','owner','created_by','approved_by'),request.user),pk=pk)


def _stop(request,pk):
    return get_object_or_404(beats.scope(BeatStop.objects.select_related('plan__site','plan__owner','assignment__partner','assignment__territory','recorded_by','collection_followup'),request.user,'plan'),pk=pk)


def _day(raw,default):
    if not raw:return default
    return forms.DateField().clean(raw)


def _error(request,error):messages.error(request,' '.join(error.messages))


@login_required
def territories(request):
    permitted(request.user,'view_salesterritory')
    nodes=SalesTerritory.objects.filter(site__in=sites_for(request.user)).select_related('site','parent','parent__parent')
    for node in nodes:node.branch_active=node.site.active and all(n.active for n in beats.path(node))
    assignments=beats.scope(DealerTerritoryAssignment.objects.select_related('site','territory','owner','partner','outgoing_transfer','incoming_transfer'),request.user)
    today=timezone.localdate()
    if request.GET.get('history')!='1':assignments=assignments.filter(superseded_at__isnull=True).filter(Q(ends_on__isnull=True)|Q(ends_on__gte=today))
    q=request.GET.get('q','').strip()
    if q:assignments=assignments.filter(Q(partner__name__icontains=q)|Q(partner__code__icontains=q)|Q(territory__name__icontains=q))
    return render(request,'erp/territories.html',dict(title='Territories & dealer ownership',nodes=nodes,
        page=Paginator(assignments,25).get_page(request.GET.get('page')),q=q,history=request.GET.get('history')=='1'))


@login_required
def master_form(request,kind):
    permitted(request.user,'manage_salesterritory')
    if kind not in ['territory','assignment']:raise Http404
    Form=TerritoryForm if kind=='territory' else AssignmentForm
    form=Form(request.POST or None,user=request.user)
    if request.method=='POST' and form.is_valid():
        try:
            beats.create_territory(request.user,form.cleaned_data) if kind=='territory' else beats.assign(request.user,form.cleaned_data)
        except ValidationError as e:form.add_error(None,e)
        else:
            messages.success(request,'Territory saved.' if kind=='territory' else 'Dated dealer assignment saved; previous history is unchanged.')
            return redirect('territories')
    return render(request,'erp/beat_form.html',dict(title='New territory' if kind=='territory' else 'Assign dealer ownership',form=form,back='territories',
        description='State → district → area within one site. Names/codes stay fixed; archive unused branches.' if kind=='territory' else 'Choose an area, salesperson and inclusive dates. A dealer cannot have overlapping ownership at one site.'))


@login_required
@require_POST
def territory_action(request,kind,pk):
    permitted(request.user,'manage_salesterritory')
    note=request.POST.get('note','')
    try:
        if kind=='status':
            get_object_or_404(SalesTerritory,pk=pk,site__in=sites_for(request.user));beats.toggle_territory(request.user,pk,note)
        elif kind=='end':
            get_object_or_404(DealerTerritoryAssignment,pk=pk,site__in=sites_for(request.user));beats.end_assignment(request.user,pk,_day(request.POST.get('on'),None),note)
        else:raise Http404
        messages.success(request,'Territory/ownership change recorded with its history.')
    except ValidationError as e:_error(request,e)
    return redirect('territories')


@login_required
def plans(request):
    permitted(request.user,'view_beatplan')
    today=timezone.localdate()
    qs=beats.scope(BeatPlan.objects.select_related('site','owner').prefetch_related('stops'),request.user)
    status=request.GET.get('status','')
    if status=='covers':qs=qs.filter(pk__in=beat_cover.reviewable(request.user).values('plan_id'))
    elif status=='due':qs=qs.filter(status='approved',on__lte=today,stops__status='planned').distinct()
    elif status in ['draft','submitted','approved','finished','cancelled']:qs=qs.filter(status=status)
    try:
        start=_day(request.GET.get('from'),today-timedelta(days=7));end=_day(request.GET.get('to'),today+timedelta(days=14))
        if start>end:raise ValidationError('The end date must be on/after the start date.')
    except ValidationError as e:_error(request,e);start,end=today-timedelta(days=7),today+timedelta(days=14)
    if status not in ['due','covers'] and (status!='submitted' or request.GET.get('from') or request.GET.get('to')):qs=qs.filter(on__range=(start,end))
    q=request.GET.get('q','').strip()
    if q:qs=qs.filter(Q(number__icontains=q)|Q(title__icontains=q)|Q(owner__username__icontains=q))
    return render(request,'erp/beat_plans.html',dict(title='Beat & visit plans',page=Paginator(qs,25).get_page(request.GET.get('page')),start=start,end=end,q=q,status=status))


@login_required
def plan_form(request,pk=None):
    permitted(request.user,'change_beatplan' if pk else 'add_beatplan')
    plan=_plan(request,pk) if pk else None
    if plan:
        beats._editor(request.user,plan)
        if plan.status!='draft':messages.error(request,'Only draft plans can be edited.');return redirect('beat_detail',pk=pk)
    form=PlanForm(request.POST or None,instance=plan,user=request.user)
    if request.method=='POST' and form.is_valid():
        try:plan=beats.save_plan(request.user,form.cleaned_data,pk)
        except ValidationError as e:form.add_error(None,e)
        else:return redirect('beat_detail',pk=plan.pk)
    return render(request,'erp/beat_form.html',dict(title='Edit route draft' if pk else 'New daily beat plan',form=form,back='beat_plans',
        description='One live plan per site, owner and day. Add the customer stops after saving the draft.'))


@login_required
def detail(request,pk):
    permitted(request.user,'view_beatplan');plan=_plan(request,pk);u=request.user
    editor=plan.owner_id==u.pk or u.has_perm('erp.manage_salesterritory')
    executor=editor or beat_cover.executes(u,plan)
    can_decide=can_approve(u, 'approve_beatplan', plan.site, makers=[plan.owner_id, plan.created_by_id, plan.submitted_by_id, *plan.contributors.values_list('pk', flat=True)]) and plan.status=='submitted' and u.pk not in [plan.owner_id,plan.created_by_id,plan.submitted_by_id] and not plan.contributors.filter(pk=u.pk).exists()
    stops=list(plan.stops.select_related('assignment__partner','assignment__territory__parent__parent','assignment__site','assignment__owner','recorded_by','followed_by','collection_followup'))
    for stop in stops:
        stop.customer=stop.snapshot or beats._stop_snapshot(stop)
        stop.can_record=executor and plan.status=='approved' and stop.status=='planned' and plan.on<=timezone.localdate() and u.has_perm('erp.record_beatstop')
        stop.can_publish=(stop.status=='completed' and stop.outcome=='promise' and not stop.collection_followup_id and u.pk not in [plan.owner_id,stop.recorded_by_id]
            and all(u.has_perm('erp.'+p) for p in ['publish_beatpromise','add_receipt','access_company_finance']))
    return render(request,'erp/beat_detail.html',dict(title=plan.number,plan=plan,stops=stops,stop_form=StopForm(plan=plan),can_decide=can_decide,
        can_edit=editor and plan.status=='draft' and u.has_perm('erp.change_beatplan'),can_submit=editor and plan.status=='draft' and u.has_perm('erp.submit_beatplan'),
        can_cancel=editor and u.has_perm('erp.change_beatplan') and plan.status not in ['finished','cancelled'] and all(s.status in ['planned','reassigned'] for s in stops),
        can_follow=executor and u.has_perm('erp.record_beatstop'),reviews=plan.reviews.select_related('actor'),covers=plan.execution_covers.select_related('recipient'),
        can_cover=u.has_perm('erp.manage_salesterritory') and u.has_perm('erp.view_team_beats') and plan.status in ['approved','finished'],
        history=AuditEvent.objects.filter(reference=plan.number,site=plan.site).select_related('actor').order_by('-created_at')[:30]))


@login_required
@require_POST
def plan_action(request,pk,action):
    _plan(request,pk)
    try:
        note=request.POST.get('note','')
        if action=='stop':
            permitted(request.user,'change_beatplan')
            form=StopForm(request.POST,plan=_plan(request,pk))
            if not form.is_valid():raise beats._errors(form)
            beats.add_stop(request.user,pk,form.cleaned_data)
        elif action=='remove':
            stop=get_object_or_404(BeatStop,pk=request.POST.get('stop') if request.POST.get('stop','').isascii() and request.POST.get('stop','').isdecimal() else 0,plan_id=pk)
            beats.remove_stop(request.user,pk,stop.pk)
        elif action=='submit':beats.submit_plan(request.user,pk,note)
        elif action in ['approve','changes']:beats.decide_plan(request.user,pk,action=='approve',note)
        elif action=='cancel':beats.cancel_plan(request.user,pk,note)
        else:raise Http404
        messages.success(request,'Route action recorded.')
    except ValidationError as e:_error(request,e)
    return redirect('beat_detail',pk=pk)


@login_required
def outcome(request,pk):
    permitted(request.user,'record_beatstop');stop=_stop(request,pk);beats._executor(request.user,stop.plan)
    if stop.status!='planned' or stop.plan.status!='approved' or stop.plan.on>timezone.localdate():
        messages.error(request,'Only an outstanding approved visit due today or earlier can be recorded.');return redirect('beat_detail',pk=stop.plan_id)
    form=OutcomeForm(request.POST or None,user=request.user,stop=stop)
    if request.method=='POST' and form.is_valid():
        try:beats.record_stop(request.user,pk,form.cleaned_data)
        except ValidationError as e:form.add_error(None,e)
        else:messages.success(request,'Visit outcome retained. Field promises need finance review.');return redirect('beat_detail',pk=stop.plan_id)
    return render(request,'erp/beat_form.html',dict(title='Record visit outcome',form=form,back='beat_plans',
        description=f'{stop.assignment.partner.name} · {stop.plan.number}. Record the actual visit and next action; outcome links do not change source orders or complaints.'))


@login_required
@require_POST
def stop_action(request,pk,action):
    stop=_stop(request,pk)
    try:
        note=request.POST.get('note','')
        if action=='missed':beats.record_stop(request.user,pk,dict(note=note),missed=True)
        elif action=='followup':beats.close_followup(request.user,pk,note)
        elif action=='promise':beats.publish_promise(request.user,pk,note)
        else:raise Http404
        messages.success(request,'Field action recorded.')
    except ValidationError as e:_error(request,e)
    return redirect('beat_detail',pk=stop.plan_id)


@login_required
def report(request):
    permitted(request.user,'view_beatplan');today=timezone.localdate()
    try:
        start=_day(request.GET.get('from'),today.replace(day=1));end=_day(request.GET.get('to'),today)
        rows=beats.coverage(request.user,start,end)
    except ValidationError as e:
        _error(request,e);start,end=today.replace(day=1),today;rows=beats.coverage(request.user,start,end)
    followups=beats.scope(BeatStop.objects.select_related('plan','assignment__partner'),request.user,'plan').filter(status='completed',next_action_on__lte=today,followed_at__isnull=True)
    if request.GET.get('export')=='csv':
        permitted(request.user,'export_beatplan')
        response=HttpResponse(content_type='text/csv; charset=utf-8');response['Content-Disposition']=content_disposition_header(True,f'beat-coverage-{start}-{end}.csv');response['Cache-Control']='private, no-store'
        response.write('\ufeff');writer=csv.writer(response);writer.writerow(['Site','Owner','Area','Assigned dealers','Visited dealers','Planned stops','Completed','Missed','Overdue pending','Unvisited assigned dealers'])
        for r in rows:writer.writerow([_safe(v) for v in [r['site'].code,r['owner'].get_username(),r['area'],r['assigned_count'],r['visited_count'],r['planned'],r['completed'],r['missed'],r['overdue'],'; '.join(p.code for p in r['gaps'])]])
        AuditEvent.objects.create(actor=request.user,action='Beat coverage exported',reference='Field coverage',detail=f'{start} to {end}; scoped owner/site rows')
        return response
    promises=[]
    if all(request.user.has_perm('erp.'+p) for p in ['publish_beatpromise','add_receipt','access_company_finance']):
        promises=beats.scope(BeatStop.objects.select_related('plan','assignment__partner'),request.user,'plan').filter(status='completed',outcome='promise',collection_followup__isnull=True).exclude(plan__owner=request.user).exclude(recorded_by=request.user)[:100]
    return render(request,'erp/beat_report.html',dict(title='Field coverage & follow-ups',start=start,end=end,rows=rows,followups=followups[:100],promises=promises))
