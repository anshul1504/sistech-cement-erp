"""Scoped construction opportunities with evidence-gated stages and retained source links."""
import hashlib
import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Sum,Q
from django.utils import timezone
from .crm import sites_for,lead_scope
from .gst import next_number,money
from .models import (SalesProject,ProjectActivity,ProjectLink,ProjectEvent,Site,Partner,Quotation,Order,Invoice,CreditNote,
                     AuditEvent,PROJECT_STAGES,PROJECT_WEIGHTS,CUSTOMER_KINDS)
from .services import permitted,require_site
from .project_forms import ProjectForm,ActivityForm,LinkForm

CLOSED=['won','lost']
FIELDS=['name','location','district','contact_name','mobile','prospect_name','partner','lead','estimated_value','cement_bags','tmt_mt','expected_close','next_action_on','notes']


def eligible_owners():
    ids=[u.pk for u in get_user_model().objects.filter(is_active=True) if u.has_perm('erp.view_salesproject') and u.has_perm('erp.change_salesproject')]
    return get_user_model().objects.filter(pk__in=ids).order_by('username')


def scope(qs,user,prefix=''):
    if user.is_superuser:return qs
    root=prefix+'__' if prefix else ''
    qs=qs.filter(**{root+'site__in':sites_for(user)})
    return qs if user.has_perm('erp.view_team_projects') else qs.filter(**{root+'owner':user})


def _lock(user,pk):
    header=SalesProject.objects.only('site_id').get(pk=pk);require_site(user,header.site)
    Site.objects.select_for_update().get(pk=header.site_id)
    project=SalesProject.objects.select_for_update().select_related('site','owner','partner','lead').get(pk=pk)
    if project.owner_id!=user.pk and not user.is_superuser and not user.has_perm('erp.view_team_projects'):raise PermissionDenied
    return project


def _editor(user,project):
    if project.owner_id!=user.pk and not user.has_perm('erp.manage_salesproject'):raise PermissionDenied


def _open(project):
    if project.stage in CLOSED:raise ValidationError('Closed projects retain their evidence. A manager must reopen before changing them.')


def _errors(form):return ValidationError([f'{key}: {m}' for key,values in form.errors.items() for m in values])


def _current_source(link):
    if link.quotation_id:
        q=link.quotation
        return dict(kind='quotation',number=q.number,revision=q.revision,status=q.status,site=q.site_id,partner=q.partner_id,lead=q.lead_id,valid_until=str(q.valid_until),order=q.order_id)
    order=link.order
    return dict(kind='order',number=order.number,status=order.status,site=order.site_id,partner=order.partner_id,approved_by=order.approved_by_id)


def snapshot(project):
    return dict(number=project.number,name=project.name,site=project.site_id,owner=project.owner_id,stage=project.stage,partner=project.partner_id,
        lead=project.lead_id,referral=project.referral_id,buyer=project.buyer_name,location=project.location,district=project.district,
        estimated_value=str(project.estimated_value),cement_bags=project.cement_bags,tmt_mt=str(project.tmt_mt),
        expected_close=str(project.expected_close) if project.expected_close else None,next_action_on=str(project.next_action_on) if project.next_action_on else None,
        links=[dict(id=l.pk,active=l.active,source=l.snapshot,current=_current_source(l)) for l in project.links.all()])


def _event(user,project,action,note):
    row=ProjectEvent(project=project,action=action,actor=user,note=note,snapshot=snapshot(project));row.full_clean();row.save()
    AuditEvent.objects.create(actor=user,site=project.site,action='Project '+action.lower(),reference=project.number,detail=note)


def _party(project):
    if project.partner and (project.partner.kind not in CUSTOMER_KINDS or not project.partner.active):raise ValidationError('Choose an active approved customer.')
    if project.lead:
        if project.lead.site_id!=project.site_id or project.lead.stage=='lost':raise ValidationError('Choose an accessible non-lost CRM lead at the project site.')
        if project.partner_id and project.lead.partner_id!=project.partner_id:raise ValidationError('The CRM lead must be onboarded/linked to the chosen customer first.')
    if not project.partner_id and not project.prospect_name:raise ValidationError('Enter the prospect/buyer name or choose an approved customer.')


@transaction.atomic
def save(user,data,pk=None):
    permitted(user,'change_salesproject' if pk else 'add_salesproject')
    project=_lock(user,pk) if pk else None
    if not pk and isinstance(data.get('site'),Site):require_site(user,data['site'])
    if project:_editor(user,project);_open(project)
    form=ProjectForm(data,instance=project,user=user)
    if not form.is_valid():raise _errors(form)
    values=form.cleaned_data;site=values['site'];owner=values['owner']
    require_site(user,site);Site.objects.select_for_update().get(pk=site.pk)
    if not owner.is_active or not owner.has_perm('erp.view_salesproject') or not owner.has_perm('erp.change_salesproject'):raise ValidationError('Choose an active project owner.')
    require_site(owner,site)
    if owner.pk!=user.pk and not user.has_perm('erp.manage_salesproject'):raise PermissionDenied
    if project and project.links.filter(active=True).exists():
        original=SalesProject.objects.get(pk=pk)
        identity=(values['partner'].pk if values['partner'] else None,values['lead'].pk if values['lead'] else None)
        onboarded=(original.partner_id is None and original.lead_id==identity[1] and values['lead'] and values['lead'].partner_id==identity[0] and identity[0] is not None)
        if (original.partner_id,original.lead_id)!=identity and not onboarded:raise ValidationError('Unlink active commercial sources before changing buyer/CRM identity.')
    obj=form.save(commit=False);obj.site=site;obj.owner=owner
    _party(obj)
    if obj.next_action_on and obj.next_action_on<timezone.localdate():raise ValidationError('Choose today or a future next action.')
    if obj.expected_close and obj.expected_close<timezone.localdate():raise ValidationError('A newly set expected close cannot be in the past.')
    if not obj.next_action_on:raise ValidationError('An open project needs its next action date.')
    if not obj.name.strip() or not obj.location.strip():raise ValidationError('Enter the project name and site location.')
    obj.prospect_name=' '.join(obj.prospect_name.split())
    obj.name=' '.join(obj.name.split());obj.location=' '.join(obj.location.split());obj.district=' '.join(obj.district.split())
    obj.dedupe_key=hashlib.sha256(json.dumps([obj.name.casefold(),obj.location.casefold(),obj.partner_id,obj.prospect_name.strip().casefold() if not obj.partner_id else '']).encode()).hexdigest()
    if SalesProject.objects.filter(site=site,dedupe_key=obj.dedupe_key).exclude(pk=pk).exists():raise ValidationError('A project with this buyer, name and location already exists at this site. Ask the owner/manager to review it.')
    if not pk:
        obj.number=next_number('PRJ',site);obj.created_by=user
        if obj.referral:
            source=obj.referral
            if source.dealer_id and source.dealer_id!=obj.partner_id:raise ValidationError('Use the referral’s existing dealer as the project customer.')
            obj.source_snapshot=dict(id=source.pk,kind=source.kind,title=source.title,location=source.location,influencer=source.influencer.name,
                dealer=source.dealer_id,status=source.status,expected_bags=source.expected_bags,expected_tmt_mt=str(source.expected_tmt_mt),date=str(source.date))
    obj.full_clean();obj.save();_event(user,obj,'Created' if not pk else 'Updated','Project profile/ownership saved')
    return obj


@transaction.atomic
def activity(user,pk,data):
    permitted(user,'add_projectactivity');p=_lock(user,pk);_editor(user,p);_open(p)
    form=ActivityForm(data)
    if not form.is_valid():raise _errors(form)
    values=form.cleaned_data
    if values['on']>timezone.localdate() or values['on']<timezone.localtime(p.created_at).date():raise ValidationError('Activity date must be from project creation through today.')
    if not values['next_action_on'] or values['next_action_on']<timezone.localdate():raise ValidationError('Keep a current/future next action on an open project.')
    row=ProjectActivity(project=p,created_by=user,**values);row.full_clean();row.save()
    p.next_action_on=row.next_action_on;p.save(update_fields=['next_action_on','updated_at'])
    _event(user,p,'Activity recorded',f'{row.get_kind_display()} · {row.note[:350]}')
    return row


def _matching_quote(p,q):
    return q.site_id==p.site_id and ((p.partner_id and q.partner_id==p.partner_id) or (p.lead_id and q.lead_id==p.lead_id and (not q.partner_id or not p.partner_id or q.partner_id==p.partner_id)))


def _valid_quotes(p):
    return [l.quotation for l in p.links.filter(active=True,quotation__isnull=False).select_related('quotation') if _matching_quote(p,l.quotation) and l.quotation.status in ['sent','accepted','converted'] and (l.quotation.status!='sent' or l.quotation.valid_until>=timezone.localdate())]


def _valid_orders(p):
    return [l.order for l in p.links.filter(active=True,order__isnull=False).select_related('order') if p.partner_id and l.order.partner_id==p.partner_id and l.order.site_id==p.site_id and l.order.approved_by_id and l.order.kind=='Sale' and l.order.status not in ['Draft','Submitted','Revised','Cancelled']]


@transaction.atomic
def link(user,pk,data):
    permitted(user,'change_salesproject');p=_lock(user,pk);_editor(user,p);_open(p)
    form=LinkForm(data,user=user,project=p)
    if not form.is_valid():raise _errors(form)
    q,order=form.cleaned_data['quotation'],form.cleaned_data['order']
    if q:
        q=Quotation.objects.select_for_update().get(pk=q.pk)
        if not _matching_quote(p,q):raise ValidationError('Quotation must match the project site and buyer/CRM lead.')
        if ProjectLink.objects.filter(active=True,quotation__number=q.number).exclude(project=p).exists():raise ValidationError('A revision in this quotation family is already linked to another project.')
        if q.order_id and ProjectLink.objects.filter(active=True,order_id=q.order_id).exclude(project=p).exists():raise ValidationError('The converted order belongs to another project link.')
        lookup=dict(quotation=q)
        source=dict(kind='quotation',id=q.pk,number=q.number,revision=q.revision,status=q.status,site=q.site_id,partner=q.partner_id,lead=q.lead_id)
    else:
        order=Order.objects.select_for_update().get(pk=order.pk)
        if order.kind!='Sale' or order.site_id!=p.site_id or not p.partner_id or order.partner_id!=p.partner_id:raise ValidationError('Sales order must match the approved customer and project site.')
        if ProjectLink.objects.filter(active=True,quotation__order=order).exclude(project=p).exists():raise ValidationError('The source quotation is linked to another project; review that attribution first.')
        lookup=dict(order=order);source=dict(kind='order',id=order.pk,number=order.number,status=order.status,site=order.site_id,partner=order.partner_id)
    old=ProjectLink.objects.filter(active=True,**lookup).first()
    if old:
        if old.project_id==p.pk:return old
        raise ValidationError('This commercial document already belongs to another active project link.')
    row=ProjectLink(project=p,note=form.cleaned_data['note'],created_by=user,snapshot=source,**lookup);row.full_clean();row.save()
    _event(user,p,'Source linked',f'{source["number"]} · {row.note}')
    return row


@transaction.atomic
def unlink(user,pk,link_pk,note):
    permitted(user,'change_salesproject');p=_lock(user,pk);_editor(user,p);_open(p)
    row=p.links.select_for_update().get(pk=link_pk)
    if not row.active or not (note or '').strip():raise ValidationError('Unlink an active document with a reason.')
    valid_ids={q.pk for q in _valid_quotes(p)}
    if p.stage in ['quoted','negotiation'] and row.quotation_id in valid_ids and len(valid_ids)<=1:raise ValidationError('Move the project to a stage without quotation evidence before unlinking its last qualifying offer.')
    row.active,row.unlinked_by,row.unlinked_at,row.unlink_note=False,user,timezone.now(),note;row.full_clean();row.save()
    _event(user,p,'Source unlinked',f'{row.snapshot["number"]} · {note}')
    return row


@transaction.atomic
def change_stage(user,pk,stage,note,next_action=None):
    permitted(user,'change_salesproject');p=_lock(user,pk);_editor(user,p);_open(p)
    if stage not in dict(PROJECT_STAGES) or not (note or '').strip():raise ValidationError('Choose a valid stage and record the stage-change evidence.')
    if stage=='sampling' and not p.activities.filter(kind='sample').exists():raise ValidationError('Record sample/technical-review evidence before entering this stage.')
    if stage in ['quoted','negotiation'] and not _valid_quotes(p):raise ValidationError('Link a current sent/accepted/converted quotation before using this stage.')
    if stage=='won' and not _valid_orders(p):raise ValidationError('A won project requires a linked, independently approved sales order.')
    if stage not in CLOSED and (not next_action or next_action<timezone.localdate()):raise ValidationError('Open/held stages need today or a future follow-up.')
    p.stage=stage;p.next_action_on=None if stage in CLOSED else next_action;p.closed_reason=note if stage in CLOSED else ''
    p.full_clean();p.save();_event(user,p,'Stage changed',note)
    return p


@transaction.atomic
def reopen(user,pk,note,next_action):
    permitted(user,'manage_salesproject');p=_lock(user,pk)
    if p.stage not in CLOSED or not (note or '').strip() or not next_action or next_action<timezone.localdate():raise ValidationError('Reopen a closed project with a reason and current/future next action.')
    p.stage='qualified';p.closed_reason='';p.next_action_on=next_action;p.save();_event(user,p,'Reopened',note)
    return p


def pipeline(user,qs):
    rows=list(qs)
    totals={key:dict(key=key,label=label,count=0,value=Decimal(0),weighted=Decimal(0)) for key,label in PROJECT_STAGES}
    for p in rows:
        row=totals[p.stage];row['count']+=1;row['value']+=p.estimated_value;row['weighted']+=money(p.estimated_value*PROJECT_WEIGHTS[p.stage]/100)
    return list(totals.values())


def invoice_summary(user,project):
    if not user.has_perm('erp.view_invoice'):return None
    order_ids=project.links.filter(active=True,order__isnull=False).values_list('order_id',flat=True)
    invoices=Invoice.objects.filter(site=project.site,partner_id=project.partner_id,order__partner_id=project.partner_id,order_id__in=order_ids)
    taxable=invoices.aggregate(t=Sum('taxable'))['t'] or Decimal(0)
    credits=CreditNote.objects.filter(invoice__in=invoices).aggregate(t=Sum('taxable'))['t'] or Decimal(0)
    return dict(taxable=taxable,credits=credits,net=taxable-credits,count=invoices.count(),invoices=invoices[:50])
