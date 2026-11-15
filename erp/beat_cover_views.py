"""Scoped proposal and independent review screens for finite route execution cover."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from . import beats, beat_cover
from .beat_views import _plan
from .models import BeatExecutionCover
from .services import permitted


class CoverForm(forms.Form):
    recipient = forms.ModelChoiceField(queryset=None)
    ends_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type':'datetime-local'}), help_text='Local time; access ends automatically at this instant.')
    reason = forms.CharField(max_length=300, widget=forms.Textarea(attrs={'rows':3}))

    def __init__(self,*args,plan,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['recipient'].queryset=beats.eligible_owners().filter(siteaccess__site=plan.site).exclude(pk=plan.owner_id).distinct()
        for field in self.fields.values():field.widget.attrs['class']='form-select' if isinstance(field.widget,forms.Select) else 'form-control'


@login_required
def new(request,pk):
    beat_cover._manager(request.user)
    plan=_plan(request,pk)
    form=CoverForm(request.POST or None,plan=plan)
    if request.method=='POST' and form.is_valid():
        try:obj=beat_cover.propose(request.user,pk,**form.cleaned_data)
        except ValidationError as e:form.add_error(None,e)
        else:return redirect('beat_cover_detail',pk=obj.pk)
    return render(request,'erp/beat_form.html',dict(title='Propose temporary execution cover',form=form,back='beat_plans',
        description=f'{plan.number}: original ownership and recorded evidence stay fixed. A different independent checker must approve before the recipient can execute visits or follow-ups.'))


@login_required
def detail(request,pk):
    permitted(request.user,'view_beatplan')
    obj=get_object_or_404(BeatExecutionCover.objects.select_related('plan__site','plan__owner','recipient','created_by','reviewed_by','revoked_by'),pk=pk)
    _plan(request,obj.plan_id)
    return render(request,'erp/beat_cover.html',dict(title=str(obj),cover=obj,
        can_review=obj.status=='submitted' and beat_cover.can_review(request.user,obj),
        can_revoke=obj.status in ['submitted','approved'] and request.user.has_perm('erp.manage_salesterritory') and request.user.has_perm('erp.view_team_beats')))


@login_required
@require_POST
def action(request,pk,action):
    obj=get_object_or_404(BeatExecutionCover,pk=pk)
    _plan(request,obj.plan_id)
    try:
        if action=='revoke':beat_cover.revoke(request.user,pk,request.POST.get('note',''))
        elif action in ['approve','reject']:beat_cover.decide(request.user,pk,action=='approve',request.POST.get('note',''))
        else:
            from django.http import Http404
            raise Http404
    except ValidationError as e:messages.error(request,' '.join(e.messages))
    else:messages.success(request,'Execution cover decision retained.')
    return redirect('beat_cover_detail',pk=pk)
