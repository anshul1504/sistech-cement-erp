import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render

from . import approval_work as AW, work_handoff as H
from .forms import style_fields
from .models import ApprovalRouting, WorkHandoffRun
from .security import access_allowed
from .delegation import POLICIES


class HandoffForm(forms.Form):
    source_user=forms.ModelChoiceField(label='Current owner',queryset=get_user_model().objects.order_by('username'))
    recipient=forms.ModelChoiceField(queryset=get_user_model().objects.filter(is_active=True).order_by('username'))
    reason=forms.CharField(max_length=250,widget=forms.Textarea(attrs={'rows':2}))

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        style_fields(self)

    def clean(self):
        data=super().clean()
        if data.get('recipient') and not access_allowed(data['recipient']):
            self.add_error('recipient','Choose an active user with unexpired access.')
        if data.get('source_user') and data.get('source_user')==data.get('recipient'):
            self.add_error('recipient','Choose a different recipient.')
        return data


class AssignmentForm(forms.Form):
    recipient=forms.ModelChoiceField(queryset=get_user_model().objects.filter(is_active=True).order_by('username'))
    reason=forms.CharField(max_length=250,widget=forms.Textarea(attrs={'rows':2}))

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        style_fields(self)


def admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied


@login_required
def board(request):
    admin(request)
    kind=request.GET.get('kind','sales_order')
    if kind not in AW.CATALOG:
        kind='sales_order'
    workflow=AW.CATALOG[kind]
    page=Paginator(AW.pending(kind).order_by('-pk'),30).get_page(request.GET.get('page'))
    rows=[]
    for obj in page:
        task=AW.source(kind,obj.pk)
        if task is None:
            continue
        routing=ApprovalRouting.objects.filter(kind=kind,object_id=obj.pk).select_related('assignee').first()
        eligible,problem=AW.eligible(routing.assignee,task) if routing else (False,'No accountable reviewer assigned; the qualified role pool remains available.')
        rows.append(dict(task=task,url=AW.source_url(task),routing=routing,eligible=eligible,problem=problem))
    return render(request,'accounts/approval_board.html',dict(title='Approval routing',kind=kind,
        kinds=[(k,POLICIES[v.permission][0]+' · '+k.replace('_',' ')) for k,v in AW.CATALOG.items()],
        rows=rows,page=page,unavailable=H.unavailable_backlog(),runs=WorkHandoffRun.objects.select_related('source_user','recipient','actor')[:30]))


@login_required
def assignment(request,kind,pk):
    admin(request)
    task=AW.source(kind,pk)
    if task is None:
        from django.http import Http404
        raise Http404
    form=AssignmentForm(request.POST or None)
    key=request.POST.get('request_key') or str(uuid.uuid4())
    if request.method=='POST' and form.is_valid():
        try:
            run=H.assign_approval(request.user,kind,pk,form.cleaned_data['recipient'],form.cleaned_data['reason'],key)
        except ValidationError as error:
            form.add_error(None,error)
        else:
            messages.success(request,'Accountable reviewer assigned. Approval authority and independent review are still required.')
            return redirect('work_handoff_detail',pk=run.pk)
    return render(request,'accounts/approval_assign.html',dict(title='Assign approval reviewer',form=form,task=task,
        source_url=AW.source_url(task),request_key=key))


@login_required
def handoff(request):
    admin(request)
    initial={}
    source_id=request.GET.get('source_user','')
    if source_id.isdecimal() and len(source_id)<=18:
        source_user=get_user_model().objects.filter(pk=source_id).first()
        if source_user:
            profile=getattr(source_user,'profile',None)
            initial=dict(source_user=source_user,recipient=profile.work_backup if profile else None)
    form=HandoffForm(request.POST or None,initial=initial)
    rows=None
    stamp=''
    key=request.POST.get('request_key') or str(uuid.uuid4())
    if request.method=='POST' and form.is_valid():
        values=form.cleaned_data
        if request.POST.get('action')=='transfer':
            if not request.POST.get('preview_digest'):
                form.add_error(None,'Review a preview before transferring work.')
            else:
                try:
                    run=H.handoff(request.user,values['source_user'],values['recipient'],values['reason'],key,
                                  request.POST['preview_digest'])
                except ValidationError as error:
                    form.add_error(None,error)
                else:
                    messages.success(request,f'{run.transferred} work item(s) transferred; {run.blocked} need review.')
                    return redirect('work_handoff_detail',pk=run.pk)
        if not form.errors:
            rows,stamp=H.preview(values['source_user'],values['recipient'])
    return render(request,'accounts/work_handoff.html',dict(title='Pending work handoff',form=form,rows=rows,
        preview_digest=stamp,request_key=key))


@login_required
def handoff_detail(request,pk):
    admin(request)
    run=get_object_or_404(WorkHandoffRun.objects.select_related('source_user','recipient','actor'),pk=pk)
    return render(request,'accounts/work_handoff_detail.html',dict(title=str(run),run=run,
        items=run.items.select_related('site','source_user','recipient')))


@login_required
def inbox(request):
    return render(request,'accounts/approval_inbox.html',dict(title='Assigned approvals',rows=H.assigned_pending(request.user)))
