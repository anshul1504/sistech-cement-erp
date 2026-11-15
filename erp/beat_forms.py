"""Territory, route and field-report forms; every queryset is scoped before binding."""
from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from .crm_forms import style
from .models import SalesTerritory, DealerTerritoryAssignment, BeatPlan, BeatStop, Partner, CUSTOMER_KINDS, CRMLead, Order, Complaint
from .crm import sites_for


class TerritoryForm(forms.ModelForm):
    class Meta:
        model=SalesTerritory
        fields=['site','code','name','kind','parent','state']

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['site'].queryset=sites_for(user).filter(active=True)
        self.fields['parent'].queryset=SalesTerritory.objects.filter(site__in=sites_for(user),active=True).exclude(kind='area')
        self.fields['state'].help_text='Required for a state root. Districts and areas inherit their parent state.'
        style(self)


class AssignmentForm(forms.ModelForm):
    class Meta:
        model=DealerTerritoryAssignment
        fields=['territory','partner','owner','starts_on','ends_on','note']
        widgets={'starts_on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'ends_on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        from .beats import eligible_owners
        self.fields['territory'].queryset=SalesTerritory.objects.filter(site__in=sites_for(user),kind='area',active=True,parent__active=True,parent__parent__active=True,site__active=True)
        self.fields['partner'].queryset=Partner.objects.filter(kind__in=CUSTOMER_KINDS,active=True)
        self.fields['owner'].queryset=eligible_owners()
        self.fields['ends_on'].help_text='Inclusive. Leave empty for an ongoing assignment.'
        style(self)


class PlanForm(forms.ModelForm):
    class Meta:
        model=BeatPlan
        fields=['site','owner','on','title']
        widgets={'on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        from .beats import eligible_owners
        self.fields['site'].queryset=sites_for(user).filter(active=True)
        self.fields['owner'].queryset=eligible_owners()
        if not user.has_perm('erp.manage_salesterritory'):
            self.fields['owner'].queryset=get_user_model().objects.filter(pk=user.pk)
            self.fields['owner'].initial=user.pk
            self.fields['owner'].disabled=True
        if self.instance.pk:
            for key in ['site','owner','on']:self.fields[key].disabled=True
        style(self)


class StopForm(forms.ModelForm):
    class Meta:
        model=BeatStop
        fields=['assignment','sequence','purpose','instructions']

    def __init__(self,*args,plan,**kwargs):
        super().__init__(*args,**kwargs)
        from django.db.models import Q
        self.fields['assignment'].queryset=DealerTerritoryAssignment.objects.filter(superseded_at__isnull=True,site=plan.site,owner=plan.owner,starts_on__lte=plan.on,territory__active=True,territory__parent__active=True,territory__parent__parent__active=True,partner__active=True).filter(Q(ends_on__isnull=True)|Q(ends_on__gte=plan.on)).select_related('partner','territory','owner','territory__site')
        self.fields['sequence'].initial=(plan.stops.order_by('-sequence').values_list('sequence',flat=True).first() or 0)+1
        style(self)


class OutcomeForm(forms.ModelForm):
    class Meta:
        model=BeatStop
        fields=['visited_on','outcome','note','next_action_on','promised_amount','promised_on','lead','order','complaint']
        widgets={key:forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d') for key in ['visited_on','next_action_on','promised_on']}
        widgets.update(note=forms.Textarea(attrs={'rows':3}))
        labels={'lead':'Related CRM lead','order':'Related sales order','complaint':'Related complaint','promised_on':'Promised payment date'}

    def __init__(self,*args,user,stop,**kwargs):
        super().__init__(*args,**kwargs)
        from .crm import lead_scope
        self.fields['visited_on'].initial=timezone.localdate()
        self.fields['visited_on'].required=True
        self.fields['outcome'].required=True
        self.fields['note'].required=True
        partner=stop.assignment.partner
        self.fields['lead'].queryset=lead_scope(CRMLead.objects.filter(site=stop.plan.site,partner=partner),user) if user.has_perm('erp.view_crmlead') else CRMLead.objects.none()
        self.fields['order'].queryset=Order.objects.filter(site=stop.plan.site,partner=partner,kind='Sale') if user.has_perm('erp.view_sales_order') else Order.objects.none()
        self.fields['complaint'].queryset=Complaint.objects.filter(partner=partner,invoice__site=stop.plan.site) if user.has_perm('erp.view_complaint') else Complaint.objects.none()
        self.fields['promised_amount'].help_text='Field report only. Finance must accept it into collections; no receipt is created.'
        style(self)
