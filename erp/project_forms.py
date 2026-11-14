"""Project input and source selection with explicit site/owner boundaries."""
from django import forms
from .crm_forms import style
from .forms import normalise_mobile
from .crm import sites_for,lead_scope
from .models import SalesProject,ProjectActivity,Partner,CRMLead,InfluencerActivity,Quotation,Order,CUSTOMER_KINDS,LEAD_KINDS


class ProjectForm(forms.ModelForm):
    mobile=forms.CharField(max_length=25)
    class Meta:
        model=SalesProject
        fields=['site','owner','name','location','district','contact_name','mobile','prospect_name','partner','lead','referral',
                'estimated_value','cement_bags','tmt_mt','expected_close','next_action_on','notes']
        labels={'name':'Project / construction name','site':'Plant / depot','lead':'CRM buyer / lead','referral':'Import referral / project source','next_action_on':'Next action date'}
        widgets={'expected_close':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'next_action_on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'notes':forms.Textarea(attrs={'rows':3})}

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        from .projects import eligible_owners
        self.fields['site'].queryset=sites_for(user).filter(active=True)
        self.fields['owner'].queryset=eligible_owners()
        if not user.has_perm('erp.manage_salesproject'):
            self.fields['owner'].queryset=eligible_owners().filter(pk=user.pk);self.fields['owner'].initial=user.pk;self.fields['owner'].disabled=True
        self.fields['partner'].queryset=Partner.objects.filter(kind__in=CUSTOMER_KINDS,active=True) if user.has_perm('erp.view_partner') else Partner.objects.none()
        self.fields['lead'].queryset=lead_scope(CRMLead.objects.all(),user) if user.has_perm('erp.view_crmlead') else CRMLead.objects.none()
        self.fields['referral'].queryset=InfluencerActivity.objects.filter(kind__in=LEAD_KINDS).select_related('influencer','dealer') if user.has_perm('erp.view_influencer') else InfluencerActivity.objects.none()
        self.fields['referral'].label_from_instance=lambda row:f'{row.pk} · {row.influencer.name} · {row.title or row.location}'
        self.fields['next_action_on'].required=True
        self.fields['estimated_value'].help_text='Planning estimate excluding GST, not booked sales or an invoice total.'
        self.fields['partner'].help_text='Existing approved customer. CRM prospects must complete onboarding before sales conversion.'
        self.fields['referral'].help_text='Optional legacy referral/project import. Source status and volumes are retained, not rewritten.'
        if self.instance.pk:
            self.fields['site'].disabled=True;self.fields['referral'].disabled=True
        style(self)

    def clean_mobile(self):
        value=normalise_mobile(self.cleaned_data['mobile'])
        if not value:raise forms.ValidationError('Enter a valid 10-digit Indian mobile number.')
        return value


class ActivityForm(forms.ModelForm):
    class Meta:
        model=ProjectActivity
        fields=['kind','on','note','next_action_on']
        labels={'on':'Activity date','note':'Conversation / result','next_action_on':'Next action date'}
        widgets={'on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'next_action_on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'note':forms.Textarea(attrs={'rows':3})}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.fields['next_action_on'].required=True;style(self)


class LinkForm(forms.Form):
    quotation=forms.ModelChoiceField(queryset=Quotation.objects.none(),required=False)
    order=forms.ModelChoiceField(queryset=Order.objects.none(),required=False)
    note=forms.CharField(max_length=300,widget=forms.Textarea(attrs={'rows':2}))

    def __init__(self,*args,user,project,**kwargs):
        super().__init__(*args,**kwargs)
        from django.db.models import Q
        if user.has_perm('erp.view_quotation'):
            party=Q(partner=project.partner) if project.partner_id else Q(pk__in=[])
            if project.lead_id:party|=Q(lead=project.lead)
            self.fields['quotation'].queryset=Quotation.objects.filter(party,site=project.site).exclude(status__in=['rejected','expired','revised']).order_by('-created_at')
        if user.has_perm('erp.view_sales_order') and project.partner_id:
            self.fields['order'].queryset=Order.objects.filter(site=project.site,partner=project.partner,kind='Sale').exclude(status__in=['Cancelled','Revised']).order_by('-created_at')
        style(self)

    def clean(self):
        data=super().clean()
        if bool(data.get('quotation'))==bool(data.get('order')):raise forms.ValidationError('Select exactly one quotation or sales order.')
        return data
