"""Explicit preview, confirmation and retained receipt for commercial ownership transfer."""
import uuid
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, render, redirect
from . import beats, dealer_transfer
from .models import DealerTerritoryAssignment, DealerOwnershipTransfer
from .crm import sites_for
from .crm_forms import style


class TransferForm(forms.Form):
    recipient=forms.ModelChoiceField(queryset=None)
    effective_on=forms.DateField(widget=forms.DateInput(attrs={'type':'date'}))
    reason=forms.CharField(max_length=300,widget=forms.Textarea(attrs={'rows':3}))
    request_key=forms.UUIDField(widget=forms.HiddenInput,initial=uuid.uuid4)
    fingerprint=forms.CharField(required=False,widget=forms.HiddenInput)

    def __init__(self,*args,source,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['recipient'].queryset=beats.eligible_owners().filter(siteaccess__site=source.site).exclude(pk=source.owner_id).distinct()
        self.fields['effective_on'].help_text='Tomorrow or later, within this assignment period. Today’s work uses execution cover.'
        style(self)


@login_required
def new(request,pk):
    source=get_object_or_404(DealerTerritoryAssignment,pk=pk,site__in=sites_for(request.user))
    dealer_transfer._assignment(request.user,pk)
    form=TransferForm(request.POST or None,source=source)
    preview=None
    if request.method=='POST' and form.is_valid():
        data=form.cleaned_data
        try:
            if request.POST.get('phase')=='confirm':
                obj=dealer_transfer.transfer(request.user,pk,data['recipient'],data['effective_on'],data['reason'],data['request_key'],data['fingerprint'])
                return redirect('dealer_transfer_detail',pk=obj.pk)
            preview=dealer_transfer.preview(request.user,pk,data['recipient'],data['effective_on'])
            form=TransferForm(initial={**data,'fingerprint':preview['fingerprint']},source=source)
        except ValidationError as e:form.add_error(None,e)
    return render(request,'erp/dealer_transfer.html',dict(title='Transfer dealer ownership',source=source,form=form,preview=preview))


@login_required
def detail(request,pk):
    obj=get_object_or_404(DealerOwnershipTransfer.objects.select_related('source__site','source__partner','source__owner','replacement__owner','actor'),pk=pk,source__site__in=sites_for(request.user))
    dealer_transfer._assignment(request.user,obj.source_id)
    return render(request,'erp/dealer_transfer_detail.html',dict(title=str(obj),transfer=obj))
