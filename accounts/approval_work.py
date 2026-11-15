"""Live pending-approval catalog. Routing does not approve a source or change its authority."""
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, Subquery
from django.urls import reverse
from django.utils import timezone

from erp import models as M
from . import delegation as D
from .security import access_allowed


@dataclass(frozen=True)
class Workflow:
    model: type
    permission: str
    view_permission: str
    filters: dict
    site_path: str = 'site'
    maker_paths: tuple = ('created_by_id',)
    route: str = ''


CATALOG = {
    'crm_merge': Workflow(M.CRMLeadMerge,'approve_crmmerge','view_crmlead',{'status':'requested'},'source.site',(), 'crm_lead_detail'),
    'pod_correction': Workflow(M.PODCorrection,'approve_pod_correction','view_dispatch',{'status':'requested'},'dispatch.site',(), 'dispatch_detail'),
    'freight_recovery': Workflow(M.FreightRecoveryAdjustment,'approve_freightbill','view_freightbill',{'status':'requested'},'',('created_by_id','bill.created_by_id'),'freight_bill_detail'),
    'journal_reversal': Workflow(M.JournalReversalRequest,'approve_journalentry','view_journalentry',{'status':'requested'},'',('created_by_id','entry.created_by_id'),'journal_detail'),
    'recipe': Workflow(M.Recipe,'approve_recipe','view_recipe',{'status':'draft'},'',route='recipes'),
    'weight_exception': Workflow(M.Dispatch,'approve_weight_exception','view_dispatch',{'status':'weighed','exception_approved_by__isnull':True},maker_paths=('created_by_id','loaded_by_id'),route='dispatch_detail'),
    'recognition_publication': Workflow(M.RecognitionProgram,'publish_recognitionprogram','view_recognitionprogram',{'status':'ranked'},'',('created_by_id','ranked_by_id'),'recognition_detail'),
    'recognition_usage': Workflow(M.InfluencerUsage,'verify_influencerusage','view_influencerusage',{'status':'pending'},'',route='influencer_usage'),
    'complaint_decision': Workflow(M.Complaint,'decide_complaint','view_complaint',{'status':'investigating'},'invoice.site',route='complaint_detail'),
    'complaint_closure': Workflow(M.Complaint,'decide_complaint','view_complaint',{'status':'decided'},'invoice.site',route='complaint_detail'),
    'capa_verification': Workflow(M.CapaAction,'verify_capaaction','view_capaaction',{'status':'done'},'complaint.invoice.site',(), 'capa_detail'),
    'freight_bill': Workflow(M.FreightBill,'approve_freightbill','view_freightbill',{'status':'submitted'},'',route='freight_bill_detail'),
    'dealer_kyc': Workflow(M.DealerApplication,'review_dealerkyc','view_dealerapplication',{'status':'submitted'},route='crm_application_detail'),
    'dealer_credit': Workflow(M.DealerApplication,'review_dealercredit','view_dealerapplication',{'status':'kyc'},route='crm_application_detail'),
    'dealer_activation': Workflow(M.DealerApplication,'approve_dealerapplication','view_dealerapplication',{'status':'credit'},route='crm_application_detail'),
    'sales_order': Workflow(M.Order,'approve_sales_order','view_sales_order',{'kind':'Sale','status':'Submitted'},route='order_detail'),
    'purchase_order': Workflow(M.Order,'approve_purchase_order','view_purchase_order',{'kind':'Purchase','status':'Submitted'},route='order_detail'),
    'quotation': Workflow(M.Quotation,'approve_quotation','view_quotation',{'status':'approval'},route='quotation_detail'),
    'purchase_request': Workflow(M.PurchaseRequest,'approve_purchaserequest','view_purchaserequest',{'status':'submitted'},route='pr_detail'),
    'rfq': Workflow(M.RFQ,'award_rfq','view_rfq',{'status':'open'},maker_paths=('created_by_id','request.created_by_id'),route='rfq_detail'),
    'transfer': Workflow(M.StockTransfer,'approve_stocktransfer','view_stocktransfer',{'status':'requested'},'from_site',route='transfer_detail'),
    'transfer_discrepancy': Workflow(M.StockTransfer,'approve_stocktransfer','view_stocktransfer',{'status':'discrepancy'},'to_site',('created_by_id','received_by_id','dispatched_by_id'),'transfer_detail'),
    'stock_count': Workflow(M.StockCount,'approve_stockcount','view_stockcount',{'status':'submitted'},maker_paths=('submitted_by_id',),route='count_detail'),
    'damage': Workflow(M.DamageReport,'decide_damagereport','view_damagereport',{'status':'reported'},route='damage_detail'),
    'inbound_quality': Workflow(M.GRNLine,'release_goodsreceipt','view_goodsreceipt',{'grn__status':'posted','hold__gt':0,'qc_at__isnull':True},'grn.site',('grn.posted_by_id',),''),
    'production_quality': Workflow(M.Production,'release_production','view_production',{'status':'Quality hold'},maker_paths=('created_by_id','completed_by_id'),route='production_detail'),
    'supplier_invoice': Workflow(M.SupplierInvoice,'approve_supplierinvoice','view_supplierinvoice',{'status':'exception'},route='sinv_detail'),
    'payment_approval': Workflow(M.SupplierPayment,'approve_supplierpayment','view_supplierpayment',{'status':'proposed'},'',route='payment_detail'),
    'payment_release': Workflow(M.SupplierPayment,'release_supplierpayment','view_supplierpayment',{'status':'approved'},'',route='payment_detail'),
    'journal': Workflow(M.JournalEntry,'approve_journalentry','view_journalentry',{'kind':'manual','status':'draft'},'',route='journal_detail'),
    'scheme_rules': Workflow(M.SchemeVersion,'approve_schemeversion','view_incentivescheme',{'status':'submitted'},'scheme.site',('created_by_id','updated_by_id','submitted_by_id'),'scheme_detail'),
    'scheme_entitlement': Workflow(M.SchemeEntitlementBatch,'approve_schemeentitlementbatch','view_schemeentitlementbatch',{'status':'submitted'},'version.scheme.site',('created_by_id','evaluation.created_by_id'),'scheme_batch'),
    'scheme_payout': Workflow(M.SchemeSettlement,'approve_schemesettlement','view_schemesettlement',{'status':'proposed'},'',route='scheme_payouts'),
    'supply_rate': Workflow(M.SupplyContract,'approve_supplycontract','view_supplycontract',{'status':'draft'},route='mfg_partners'),
    'customer_return': Workflow(M.ReturnRequest,'approve_returnrequest','view_returnrequest',{'status':'requested'},route='return_detail'),
    'beat_cover': Workflow(M.BeatExecutionCover,'approve_beatplan','view_beatplan',{'status':'submitted'},'plan.site',('created_by_id','recipient_id','plan.owner_id','plan.created_by_id','plan.submitted_by_id'),'beat_cover_detail'),
    'beat_plan': Workflow(M.BeatPlan,'approve_beatplan','view_beatplan',{'status':'submitted'},maker_paths=('created_by_id','owner_id','submitted_by_id'),route='beat_detail'),
    'period_close': Workflow(M.FinancialPeriod,'close_period','view_financials',{'status':'open'},'',(), 'period_detail'),
    'year_close': Workflow(M.FinancialYearClose,'approve_yearclose','view_financialyearclose',{'status':'proposed'},'',('prepared_by_id',),'year_detail'),
    'year_reopen': Workflow(M.FinancialYearClose,'approve_yearclose','view_financialyearclose',{'status':'reopen_requested'},'',('prepared_by_id','reopen_requested_by_id'),'year_detail'),
}


def path(obj, dotted):
    for part in dotted.split('.'):
        obj = getattr(obj,part)
        if obj is None:
            return None
    return obj



def pending(kind):
    workflow=CATALOG[kind]
    qs=workflow.model.objects.filter(**workflow.filters)
    if kind=='recipe':
        from django.db.models import F, Value
        from django.db.models.functions import Greatest
        qs=qs.alias(_approval_horizon=Greatest(F('effective_from'),Value(timezone.localdate())))
        newer=M.Recipe.objects.filter(product_id=OuterRef('product_id'),status='approved',version__gt=OuterRef('version'),effective_from__lte=OuterRef('_approval_horizon'))
        qs=qs.alias(_superseded_draft=Exists(newer)).filter(_superseded_draft=False)
    if kind=='weight_exception':qs=qs.exclude(exception='')
    if kind=='complaint_closure':
        qs=qs.exclude(resolution='credit',credit_note__isnull=True).exclude(resolution='replace',replacement_order__isnull=True)
        unresolved=M.CapaAction.objects.filter(complaint_id=OuterRef('pk')).exclude(status='effective')
        from django.db.models import Q
        qs=qs.alias(_unresolved_capa=Exists(unresolved)).exclude(Q(category='quality') & ~Q(decision='reject') & Q(_unresolved_capa=True))
    if kind=='rfq':
        quotes=M.SupplierQuote.objects.filter(rfq_id=OuterRef('pk'),valid_until__gte=timezone.localdate())
        qs=qs.alias(_has_live_quote=Exists(quotes)).filter(_has_live_quote=True)
    if kind=='period_close':
        reviews=M.PeriodCloseReview.objects.filter(period_id=OuterRef('pk')).order_by('-prepared_at','-pk')
        qs=qs.alias(_latest_review=Subquery(reviews.values('pk')[:1]),
            _latest_approval=Subquery(reviews.values('approved_at')[:1])).filter(
                _latest_review__isnull=False,_latest_approval__isnull=True)
    return qs


def source(kind, pk, lock=False):
    workflow = CATALOG.get(kind)
    if not workflow:
        return None
    qs = pending(kind).filter(pk=pk)
    if lock:
        qs = qs.select_for_update(of=('self',))
    obj = qs.first()
    if obj is None:
        return None
    if kind == 'rfq' and not obj.quotes.filter(valid_until__gte=timezone.localdate()).exists():
        return None
    review = None
    if kind == 'period_close':
        review = obj.reviews.order_by('-prepared_at','-pk').first()
        if review is None or review.approved_at:
            return None
    makers = {path(obj,p) for p in workflow.maker_paths} - {None}
    if kind in {'complaint_decision','complaint_closure','capa_verification'}:
        from erp import complaints as CM
        makers.update(CM.capa_makers(obj) if kind=='capa_verification' else CM.complaint_makers(obj))
    if kind in {'dealer_kyc','dealer_credit','dealer_activation'}:
        from erp.crm import review_makers
        makers.update(review_makers(obj,workflow.permission))
    if kind in {'sales_order','purchase_order'}:
        makers.update(D.order_makers(obj))
    if kind in {'scheme_rules','beat_plan'}:
        makers.update(obj.contributors.values_list('pk',flat=True))
    if kind=='crm_merge':
        from erp.crm_merge import makers as merge_makers
        makers.update(merge_makers(obj))
    if kind == 'pod_correction':
        from erp import pod
        makers.update(pod.makers(obj))
    if kind == 'weight_exception':
        from erp.logistics import exception_makers
        makers.update(exception_makers(obj))
    if kind == 'recognition_publication':
        makers.update(obj.contributors.values_list('pk',flat=True))
    if kind == 'beat_cover':
        makers.update(obj.plan.contributors.values_list('pk',flat=True))
    if review is not None:
        makers.add(review.prepared_by_id)
    site = path(obj,workflow.site_path) if workflow.site_path else None
    reference = getattr(obj,'number',None) or getattr(obj,'batch',None) or str(obj)
    if kind == 'recognition_usage':reference=f'Usage {obj.pk} · {obj.influencer.name} · {obj.bill_reference}'
    if kind == 'inbound_quality':
        reference = f'{obj.grn.number} / line {obj.pk}'
    return dict(kind=kind,object_id=obj.pk,object=obj,site=site,makers=tuple(sorted(makers)),
                reference=reference[:160],permission=workflow.permission,view_permission=workflow.view_permission,
                scope_label=str(site) if site else ('Company recipe' if kind=='recipe' else 'Company recognition' if kind in {'recognition_publication','recognition_usage'} else 'Private record access' if kind in {'complaint_decision','complaint_closure','capa_verification'} else 'Company finance'),
                status=getattr(obj,'status','Quality hold'))


def eligible(person, task):
    person = get_user_model().objects.get(pk=person.pk)
    if not access_allowed(person):
        return False, 'The recipient is disabled or their temporary access expired.'
    if not person.has_perm('erp.'+task['view_permission']):
        return False, 'The recipient needs existing module read access.'
    if person.pk in task['makers']:
        return False, 'The recipient is a maker/contributor and cannot be the independent reviewer.'
    if task['kind'] in {'complaint_decision','complaint_closure','capa_verification'}:
        from erp import complaints as CM
        qualified=CM.can_verify(person,task['object']) if task['kind']=='capa_verification' else CM.can_decide(person,task['object'],close=task['kind']=='complaint_closure')
        if not qualified:return False,'The recipient lacks independent scoped complaint/CAPA review authority.'
    if task['kind'] in {'dealer_kyc','dealer_credit','dealer_activation'}:
        from erp.crm import can_review_application
        if not can_review_application(person,task['object'],task['permission']):
            return False,'The recipient needs scoped CRM review authority and independence from the preceding review lane.'
    if task['kind'] == 'freight_bill':
        from erp.freight import can_decide
        if not can_decide(person,task['object']):
            return False,'The recipient lacks current consolidated freight scope or independent review authority.'
    policy = D.POLICIES[task['permission']]
    private_case=task['kind'] in {'complaint_decision','complaint_closure','capa_verification'} and task['site'] is None
    if policy[1] and not private_case and not (person.is_superuser or M.SiteAccess.objects.filter(user=person,site=task['site']).exists()):
        return False, 'The recipient needs existing access to the source site.'
    if policy[2] and not person.has_perm('erp.access_company_finance'):
        return False, 'The recipient needs existing company-finance scope.'
    if not D.can_approve(person,task['permission'],task['site'],makers=task['makers']):
        return False, 'The recipient lacks current direct/delegated approval authority.'
    if task['kind'] == 'sales_order':
        from erp.sales import credit_problems
        order = task['object']
        required = []
        if credit_problems(order):
            required.append('override_credit_sales_order')
        if any(line.below_list for line in order.lines.all()):
            required.append('approve_price_exception')
        if any(not D.can_approve(person,p,order.site,makers=task['makers']) for p in required):
            return False, 'The recipient also needs the required credit/price exception authority.'
    if task['kind'] == 'purchase_order':
        from erp.procurement import approval_limit
        if task['object'].grand_total > approval_limit() and not D.can_approve(person,'approve_high_value_purchase',task['site'],makers=task['makers']):
            return False, 'The recipient also needs high-value purchase authority.'
    if task['kind'] in {'beat_plan','beat_cover'} and not person.has_perm('erp.view_team_beats'):
        return False, 'The recipient needs team-beat visibility.'
    if task['kind'] == 'scheme_payout' and not person.has_perm('erp.view_schemeentitlementbatch'):
        return False, 'The combined finance workspace also needs entitlement read access.'
    return True, ''


def public(task):
    return dict(kind=task['kind'],object_id=task['object_id'],reference=task['reference'],
                site_id=task['site'].pk if task['site'] else None,permission=task['permission'],
                status=task['status'],makers=list(task['makers']))


def source_url(task):
    route = CATALOG[task['kind']].route
    if not route:
        return reverse('grn_detail',args=[task['object'].grn_id])
    if task['kind']=='crm_merge':return reverse('crm_lead_detail',args=[task['object'].source_id])
    if task['kind']=='pod_correction':return reverse('dispatch_detail',args=[task['object'].dispatch_id])
    if task['kind']=='freight_recovery':return reverse('freight_bill_detail',args=[task['object'].bill_id])
    if task['kind']=='journal_reversal':return reverse('journal_detail',args=[task['object'].entry_id])
    if route in {'scheme_payouts','mfg_partners','influencer_usage','recipes'}:
        return reverse(route)
    return reverse(route,args=[task['object_id']])
