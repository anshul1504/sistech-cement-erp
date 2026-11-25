"""One permission catalogue shared by role editing, presets and tests."""
from django.contrib.auth.models import Group, Permission

ACTIONS = [('view', 'View'), ('create', 'Create'), ('edit', 'Edit'), ('submit', 'Submit'), ('approve', 'Approve'), ('post', 'Post'), ('cancel', 'Cancel'), ('export', 'Export')]
MODULES = [
    ('projects','Project pipeline',{'view':'view_salesproject','create':'add_salesproject','edit':'change_salesproject','export':'export_salesproject'}),
    ('project_team','Project ownership & team',{'view':'view_team_projects','edit':'manage_salesproject'}),
    ('project_activity','Project follow-ups',{'create':'add_projectactivity'}),
    ('territories', 'Sales territories & ownership', {'view':'view_salesterritory','edit':'manage_salesterritory'}),
    ('beats', 'Beat & visit plans', {'view':'view_beatplan','create':'add_beatplan','edit':'change_beatplan','submit':'submit_beatplan','approve':'approve_beatplan','export':'export_beatplan'}),
    ('team_beats', 'Team field coverage', {'view':'view_team_beats'}),
    ('beat_outcomes', 'Field visit outcomes', {'create':'record_beatstop','approve':'publish_beatpromise'}),
    ('schemes', 'Incentive scheme rules', {'view': 'view_incentivescheme', 'create': 'add_incentivescheme', 'edit': 'change_schemeversion', 'submit': 'submit_schemeversion', 'approve': 'approve_schemeversion'}),
    ('scheme_calculations', 'Incentive calculation previews', {'view': 'view_schemeevaluation', 'create': 'add_schemeevaluation', 'export': 'export_schemeevaluation'}),
    ('crm', 'Dealer CRM', {'approve':'approve_crmmerge','view': 'view_crmlead', 'create': 'add_crmlead', 'edit': 'change_crmlead', 'export': 'export_crmlead'}),
    ('crm_team', 'CRM team & ownership', {'view': 'view_team_crm', 'edit': 'assign_crmlead'}),
    ('crm_activity', 'CRM follow-ups', {'create': 'add_crmactivity'}),
    ('dealer_onboarding', 'Dealer onboarding', {'view': 'view_dealerapplication', 'create': 'add_dealerapplication', 'edit': 'change_dealerapplication', 'submit': 'submit_dealerapplication', 'approve': 'approve_dealerapplication'}),
    ('dealer_kyc', 'Dealer KYC evidence', {'view': 'view_dealerdocument', 'approve': 'review_dealerkyc'}),
    ('dealer_credit', 'Dealer credit review', {'approve': 'review_dealercredit'}),
    ('crm_targets', 'Sales targets & performance', {'view': 'view_crmperformance', 'edit': 'manage_salestarget'}),
    ('crm_policy', 'Dealer onboarding policy', {'edit': 'manage_crmpolicy'}),
    ('sales', 'Sales orders', {a: f'{a}_sales_order' for a in ['view', 'create', 'submit', 'approve', 'post', 'cancel']}),
    ('sales_credit_override', 'Sales credit override', {'approve': 'override_credit_sales_order'}),
    ('sales_price_exception', 'Sales below list price', {'approve': 'approve_price_exception'}),
    ('sales_hold', 'Sales order hold / release', {'edit': 'hold_sales_order'}),
    ('returns', 'Customer returns', {'view': 'view_returnrequest', 'create': 'add_returnrequest', 'approve': 'approve_returnrequest', 'post': 'inspect_returnrequest'}),
    ('complaints', 'Complaints', {'view': 'view_complaint', 'create': 'add_complaint', 'edit': 'change_complaint', 'approve': 'decide_complaint'}),
    ('capa', 'CAPA', {'view': 'view_capaaction', 'create': 'add_capaaction', 'edit': 'change_capaaction', 'approve': 'verify_capaaction'}),
    ('quotations', 'Quotations', {'view': 'view_quotation', 'create': 'add_quotation', 'edit': 'change_quotation', 'approve': 'approve_quotation'}),
    ('recognition', 'Star programmes', {'view': 'view_recognitionprogram', 'create': 'add_recognitionprogram', 'edit': 'change_recognitionprogram', 'approve': 'publish_recognitionprogram'}),
    ('influencer_usage', 'Influencer usage', {'view': 'view_influencerusage', 'create': 'add_influencerusage', 'approve': 'verify_influencerusage'}),
    ('scheme_entitlements', 'Scheme entitlements', {'view': 'view_schemeentitlementbatch', 'create': 'add_schemeentitlementbatch', 'approve': 'approve_schemeentitlementbatch'}),
    ('scheme_payouts', 'Scheme payouts', {'view': 'view_schemesettlement', 'create': 'add_schemesettlement', 'approve': 'approve_schemesettlement'}),
    ('supply_contracts', 'Manufacturing partner rates', {'view': 'view_supplycontract', 'create': 'add_supplycontract', 'approve': 'approve_supplycontract'}),
    ('purchase_requests', 'Purchase requests', {'view': 'view_purchaserequest', 'create': 'add_purchaserequest', 'edit': 'change_purchaserequest', 'approve': 'approve_purchaserequest'}),
    ('rfq', 'RFQs & quotes', {'view': 'view_rfq', 'create': 'add_rfq', 'approve': 'award_rfq'}),
    ('purchase', 'Purchase orders', {a: f'{a}_purchase_order' for a in ['view', 'create', 'submit', 'approve', 'post', 'cancel']}),
    ('purchase_high', 'High-value PO approval', {'approve': 'approve_high_value_purchase'}),
    ('company_finance', 'Company-wide finance access', {'view': 'access_company_finance'}),
    ('ledger', 'Journals & vouchers', {'view': 'view_journalentry', 'create': 'add_journalentry', 'approve': 'approve_journalentry'}),
    ('financials', 'Financial statements', {'view': 'view_financials', 'post': 'close_period'}),
    ('period_review', 'Month-end review', {'create': 'prepare_periodclose', 'approve': 'close_period'}),
    ('year_close', 'Year-end carry-forward', {'view': 'view_financialyearclose', 'create': 'prepare_yearclose', 'approve': 'approve_yearclose'}),
    ('commission', 'Sales commission', {'view': 'view_commissionplan', 'create': 'add_commissionplan', 'edit': 'change_commissionplan', 'approve': 'approve_commissionplan'}),
    ('commission_runs', 'Commission runs', {'view': 'view_commissionrun', 'create': 'add_commissionrun', 'approve': 'approve_commissionrun'}),
    ('deposits', 'Dealer security deposits', {'view': 'view_depositentry', 'create': 'add_depositentry', 'approve': 'approve_depositentry'}),
    ('cost_centres', 'Cost centres', {'view': 'view_costcentre', 'create': 'add_costcentre', 'edit': 'change_costcentre'}),
    ('budgets', 'Budgets', {'view': 'view_budget', 'create': 'add_budget', 'edit': 'change_budget', 'approve': 'approve_budget'}),
    ('chart', 'Chart of accounts', {'view': 'view_account', 'create': 'add_account'}),
    ('payments', 'Supplier payments', {'view': 'view_supplierpayment', 'create': 'add_supplierpayment', 'approve': 'approve_supplierpayment', 'post': 'release_supplierpayment'}),
    ('bank', 'Bank reconciliation', {'view': 'view_bankstatementline', 'create': 'add_bankstatementline', 'edit': 'change_bankstatementline'}),
    ('assets', 'Fixed assets', {'view': 'view_fixedasset', 'create': 'add_fixedasset', 'post': 'depreciate_fixedasset'}),
    ('supplier_invoices', 'Supplier invoices', {'view': 'view_supplierinvoice', 'create': 'add_supplierinvoice', 'approve': 'approve_supplierinvoice'}),
    ('debit_notes', 'Debit notes & returns', {'view': 'view_debitnote', 'create': 'add_debitnote'}),
    ('grn', 'Goods receipt (GRN)', {'view': 'view_goodsreceipt', 'create': 'add_goodsreceipt', 'post': 'post_goodsreceipt', 'approve': 'release_goodsreceipt'}),
    ('inventory', 'Inventory', {'view': 'view_stockbalance', 'post': 'post_opening'}),
    ('transfers', 'Stock transfers', {'view': 'view_stocktransfer', 'create': 'add_stocktransfer', 'approve': 'approve_stocktransfer', 'post': 'dispatch_stocktransfer', 'edit': 'receive_stocktransfer'}),
    ('counts', 'Stock counts', {'view': 'view_stockcount', 'create': 'add_stockcount', 'approve': 'approve_stockcount'}),
    ('damage', 'Damaged stock', {'view': 'view_damagereport', 'create': 'add_damagereport', 'approve': 'decide_damagereport'}),
    ('production', 'Production', {'view': 'view_production', 'create': 'add_production', 'post': 'complete_production', 'approve': 'release_production'}),
    ('recipes', 'Recipes & BOM', {'view': 'view_recipe', 'create': 'add_recipe', 'approve': 'approve_recipe'}),
    ('maintenance', 'Maintenance', {'view': 'view_maintenancejob', 'create': 'add_maintenancejob', 'edit': 'change_maintenancejob', 'approve': 'verify_maintenancejob'}),
    ('equipment', 'Equipment & PM schedule', {'view': 'view_equipment', 'create': 'add_equipment', 'edit': 'add_maintenanceplan'}),
    ('shift_logs', 'Shift log', {'view': 'view_shiftlog', 'create': 'add_shiftlog'}),
    ('packing', 'Packing', {'view': 'view_packingrun', 'create': 'add_packingrun'}),
    ('sampling', 'Sampling plan', {'view': 'view_samplingplan', 'create': 'add_samplingplan', 'edit': 'change_samplingplan'}),
    ('quality', 'Quality lab', {'view': 'view_qualitytest', 'create': 'add_qualitytest', 'edit': 'add_labequipment'}),
    ('invoices', 'Tax invoices', {'view': 'view_invoice', 'post': 'post_invoice', 'export': 'export_invoice'}),
    ('receipts', 'Receipts & ledger', {'view': 'view_receipt', 'create': 'add_receipt', 'cancel': 'bounce_receipt'}),
    ('credit_notes', 'Credit notes', {'view': 'view_creditnote', 'create': 'add_creditnote'}),
    ('vehicles', 'Vehicles & drivers', {'view': 'view_vehicle', 'create': 'add_vehicle', 'edit': 'change_vehicle'}),
    ('dispatch', 'Dispatch planning', {'view': 'view_dispatch', 'create': 'add_dispatch'}),
    ('gate', 'Gate-in / gate-out', {'post': 'gate_dispatch'}),
    ('weighbridge', 'Weighbridge readings', {'post': 'weigh_dispatch', 'approve': 'approve_weight_exception'}),
    ('loading', 'Truck loading', {'post': 'load_dispatch'}),
    ('pod', 'Proof of delivery', {'post': 'pod_dispatch', 'approve': 'approve_pod_correction'}),
    ('trip_exceptions', 'Diversions & refused loads', {'approve': 'decide_dispatchincident'}),
    ('freight_rates', 'Freight rates', {'view': 'view_freightrate', 'create': 'add_freightrate', 'edit': 'change_freightrate'}),
    ('freight_bills', 'Freight bills', {'view': 'view_freightbill', 'create': 'add_freightbill', 'approve': 'approve_freightbill'}),
    ('pricing', 'Price list', {'view': 'view_pricerule', 'create': 'add_pricerule', 'edit': 'change_pricerule'}),
    ('influencers', 'Masons & contractors', {'view': 'view_influencer', 'create': 'add_influencer', 'edit': 'change_influencer', 'export': 'export_influencer'}),
    ('partners', 'Business partners', {'view': 'view_partner', 'create': 'add_partner', 'edit': 'change_partner'}),
    ('products', 'Products & materials', {'view': 'view_product', 'create': 'add_product', 'edit': 'change_product'}),
    ('sites', 'Plants & depots', {'view': 'view_site', 'create': 'add_site', 'edit': 'change_site'}),
    ('reports', 'Stock reports', {'view': 'view_stockmovement', 'export': 'export_stockmovement'}),
    ('audit', 'Audit trail', {'view': 'view_auditevent'}),
    ('dashboards', 'Management dashboard', {'view': 'view_executive_dashboard'}),
    ('support', 'Help & support desk', {'approve': 'handle_supportticket'}),
    ('organisation', 'Organisation setup', {'view': 'view_organization', 'edit': 'change_organization'}),
]
MANAGED_CODES = {code for _, _, permissions in MODULES for code in permissions.values()}
ALL_VIEWS = {p['view'] for _, _, p in MODULES if 'view' in p} - {'access_company_finance', 'view_team_crm', 'view_schemeevaluation', 'view_executive_dashboard', 'view_team_beats', 'view_team_projects'}
PRESETS = {
    'Operations maker': ALL_VIEWS | {'create_sales_order', 'submit_sales_order', 'cancel_sales_order', 'post_sales_order', 'create_purchase_order', 'submit_purchase_order', 'cancel_purchase_order', 'post_purchase_order', 'add_production', 'complete_production', 'add_qualitytest'},
    'Operations approver': ALL_VIEWS | {'approve_stockcount', 'approve_stocktransfer', 'decide_damagereport', 'approve_purchaserequest', 'post_invoice', 'approve_sales_order', 'approve_purchase_order', 'post_sales_order', 'post_purchase_order', 'release_production'},
    'Sales executive': {'view_complaint', 'add_complaint', 'change_complaint', 'view_quotation', 'add_quotation', 'change_quotation', 'view_influencerusage', 'add_influencerusage', 'view_crmlead', 'add_crmlead', 'change_crmlead', 'add_crmactivity', 'view_dealerapplication', 'add_dealerapplication', 'change_dealerapplication', 'submit_dealerapplication', 'view_dealerdocument', 'view_crmperformance', 'view_pricerule', 'view_sales_order', 'create_sales_order', 'submit_sales_order', 'cancel_sales_order', 'view_influencer', 'add_influencer', 'change_influencer', 'view_partner', 'view_product', 'view_site'},
    'Sales manager': {'view_complaint', 'add_complaint', 'change_complaint', 'decide_complaint', 'view_capaaction', 'view_quotation', 'add_quotation', 'change_quotation', 'approve_quotation', 'view_recognitionprogram', 'add_recognitionprogram', 'change_recognitionprogram', 'view_influencerusage', 'verify_influencerusage', 'view_incentivescheme', 'add_incentivescheme', 'change_schemeversion', 'submit_schemeversion', 'view_schemeevaluation', 'add_schemeevaluation', 'export_schemeevaluation', 'view_crmlead', 'view_team_crm', 'assign_crmlead', 'add_crmlead', 'change_crmlead', 'add_crmactivity', 'export_crmlead', 'view_dealerapplication', 'view_dealerdocument', 'review_dealerkyc', 'approve_dealerapplication', 'view_crmperformance', 'manage_salestarget', 'view_partner', 'view_site', 'view_product', 'view_invoice', 'view_sales_order'},
    'Purchase officer': {'view_supplycontract', 'add_supplycontract', 'view_purchase_order', 'create_purchase_order', 'submit_purchase_order', 'cancel_purchase_order', 'view_purchaserequest', 'add_purchaserequest',
                         'change_purchaserequest', 'view_rfq', 'add_rfq', 'award_rfq', 'view_stockbalance', 'view_partner', 'view_product', 'view_site'},
    'Purchase head': {'view_supplycontract', 'approve_supplycontract', 'view_debitnote', 'add_debitnote', 'view_supplierinvoice', 'view_purchase_order', 'approve_purchase_order', 'approve_high_value_purchase', 'cancel_purchase_order', 'view_purchaserequest', 'approve_purchaserequest',
                      'view_rfq', 'award_rfq', 'view_stockbalance', 'view_partner', 'view_product', 'view_site'},
    'Accountant': {'view_incentivescheme', 'view_schemeevaluation', 'view_schemeentitlementbatch', 'add_schemeentitlementbatch', 'view_schemesettlement', 'add_schemesettlement', 'prepare_periodclose', 'prepare_yearclose', 'view_financialyearclose', 'access_company_finance', 'view_supplierpayment', 'add_supplierpayment', 'view_bankstatementline', 'add_bankstatementline', 'change_bankstatementline', 'view_fixedasset',
                   'add_fixedasset', 'view_journalentry', 'add_journalentry', 'view_financials', 'view_account', 'view_invoice', 'view_receipt', 'add_receipt', 'view_creditnote',
                   'view_supplierinvoice', 'add_supplierinvoice', 'view_debitnote', 'view_partner', 'view_site', 'view_product', 'view_stockbalance'},
    'Accounts payable': {'view_supplierinvoice', 'add_supplierinvoice', 'view_debitnote', 'add_debitnote', 'view_goodsreceipt', 'view_purchase_order', 'view_partner', 'view_site', 'view_product'},
    'Finance controller': {'view_schemeentitlementbatch', 'approve_schemeentitlementbatch', 'view_schemesettlement', 'approve_schemesettlement', 'view_incentivescheme', 'approve_schemeversion', 'view_schemeevaluation', 'add_schemeevaluation', 'export_schemeevaluation', 'view_crmlead', 'view_team_crm', 'view_dealerapplication', 'view_dealerdocument', 'review_dealercredit', 'view_crmperformance', 'view_financialyearclose', 'approve_yearclose', 'access_company_finance', 'view_supplierpayment', 'approve_supplierpayment', 'release_supplierpayment', 'view_bankstatementline', 'view_fixedasset',
                           'depreciate_fixedasset', 'view_journalentry', 'approve_journalentry', 'view_financials', 'close_period', 'view_account', 'add_account',
                           'view_supplierinvoice', 'approve_supplierinvoice', 'view_debitnote', 'view_goodsreceipt', 'view_purchase_order', 'view_receipt', 'view_invoice',
                           'view_partner', 'view_site', 'view_product'},
    'Store requester': {'view_purchaserequest', 'add_purchaserequest', 'change_purchaserequest', 'view_stockbalance', 'view_product', 'view_site'},
    'Warehouse operator': {'view_stockcount', 'add_stockcount', 'view_stocktransfer', 'add_stocktransfer', 'dispatch_stocktransfer', 'receive_stocktransfer', 'view_damagereport', 'add_damagereport', 'view_goodsreceipt', 'post_goodsreceipt', 'view_sales_order', 'post_sales_order', 'view_purchase_order', 'post_purchase_order', 'view_stockbalance', 'view_stockmovement', 'view_product', 'view_site'},
    'Production operator': {'view_production', 'add_production', 'complete_production', 'view_recipe', 'view_shiftlog', 'add_shiftlog', 'view_packingrun', 'add_packingrun',
                            'view_stockbalance', 'view_product', 'view_site'},
    'Production head': {'view_production', 'add_production', 'complete_production', 'view_recipe', 'add_recipe', 'approve_recipe', 'view_shiftlog', 'add_shiftlog',
                        'view_packingrun', 'add_packingrun', 'view_qualitytest', 'view_stockbalance', 'view_product', 'view_site'},
    'Quality manager': {'view_complaint', 'change_complaint', 'view_capaaction', 'add_capaaction', 'change_capaaction', 'verify_capaaction', 'view_goodsreceipt', 'release_goodsreceipt', 'view_qualitytest', 'add_qualitytest', 'add_labequipment', 'view_production', 'release_production',
                        'view_recipe', 'approve_recipe', 'view_product', 'view_site'},
    'Gate security': {'view_dispatch', 'gate_dispatch', 'view_vehicle', 'view_goodsreceipt', 'add_goodsreceipt', 'view_purchase_order'},
    'Weighbridge operator': {'view_dispatch', 'weigh_dispatch', 'view_vehicle', 'view_goodsreceipt'},
    'Loading supervisor': {'view_dispatch', 'load_dispatch', 'view_vehicle'},
    'Logistics manager': {'view_freightrate', 'add_freightrate', 'change_freightrate', 'view_freightbill', 'approve_freightbill', 'view_dispatch', 'add_dispatch', 'approve_weight_exception', 'pod_dispatch', 'approve_pod_correction', 'view_vehicle', 'add_vehicle', 'change_vehicle',
                          'view_sales_order', 'view_partner', 'view_product', 'view_site'},
    'Management': ALL_VIEWS | {'view_executive_dashboard', 'access_company_finance', 'view_team_crm', 'view_schemeevaluation'},
    'Internal auditor': ALL_VIEWS | {'view_executive_dashboard', 'view_schemeevaluation', 'export_schemeevaluation', 'view_team_crm', 'export_crmlead', 'access_company_finance', 'export_invoice', 'export_influencer', 'export_stockmovement'},
}

# Sales exceptions (FR-SLS-003, FR-AR-004): price below list for sales/operations approvers, credit override for finance only.
PRESETS['Sales manager'] |= {'approve_sales_order', 'approve_price_exception', 'hold_sales_order'}
PRESETS['Operations approver'] |= {'approve_price_exception', 'hold_sales_order'}
PRESETS['Finance controller'] |= {'view_sales_order', 'override_credit_sales_order', 'hold_sales_order', 'view_returnrequest'}
PRESETS['Sales executive'] |= {'view_returnrequest', 'add_returnrequest'}
PRESETS['Sales manager'] |= {'view_returnrequest', 'add_returnrequest', 'approve_returnrequest'}
PRESETS['Warehouse operator'] |= {'view_returnrequest', 'inspect_returnrequest'}
PRESETS['Quality manager'] |= {'view_returnrequest', 'inspect_returnrequest'}
PRESETS['Sales executive'] |= {'view_salesterritory','view_beatplan','add_beatplan','change_beatplan','submit_beatplan','record_beatstop'}
PRESETS['Sales manager'] |= {'view_salesterritory','manage_salesterritory','view_beatplan','view_team_beats','add_beatplan','change_beatplan','submit_beatplan','approve_beatplan','record_beatstop','export_beatplan'}
PRESETS['Finance controller'] |= {'view_salesterritory','view_beatplan','view_team_beats'}
PRESETS['Accountant'] |= {'view_beatplan','view_team_beats','publish_beatpromise'}
PRESETS['Internal auditor'] |= {'view_team_beats','export_beatplan'}
PRESETS['Management'] |= {'view_team_beats'}
PRESETS['Sales executive'] |= {'view_salesproject','add_salesproject','change_salesproject','add_projectactivity'}
PRESETS['Sales manager'] |= {'view_salesproject','add_salesproject','change_salesproject','add_projectactivity','view_team_projects','manage_salesproject','export_salesproject'}
PRESETS['Internal auditor'] |= {'view_team_projects','export_salesproject'}
PRESETS['Management'] |= {'view_team_projects'}
PRESETS['Support desk'] = {'handle_supportticket'}

# Budgets & cost centres (FR-FIN-001/015): accountant prepares, finance controller approves.
PRESETS['Accountant'] |= {'view_costcentre', 'add_costcentre', 'change_costcentre', 'view_budget', 'add_budget', 'change_budget'}
PRESETS['Finance controller'] |= {'view_costcentre', 'view_budget', 'approve_budget'}
# Sales commission: sales manager drafts plans; accountant calculates runs; finance controller approves plans and posts runs.
PRESETS['Sales manager'] |= {'view_commissionplan', 'add_commissionplan', 'change_commissionplan'}
PRESETS['Accountant'] |= {'view_commissionplan', 'view_commissionrun', 'add_commissionrun'}
PRESETS['Finance controller'] |= {'view_commissionplan', 'view_commissionrun', 'approve_commissionplan', 'approve_commissionrun'}
PRESETS['Accountant'] |= {'view_depositentry', 'add_depositentry'}
PRESETS['Finance controller'] |= {'view_depositentry', 'approve_depositentry'}
PRESETS['Logistics manager'] |= {'decide_dispatchincident', 'add_returnrequest', 'view_returnrequest'}
PRESETS['Production operator'] |= {'view_maintenancejob', 'add_maintenancejob', 'change_maintenancejob', 'view_equipment'}
PRESETS['Production head'] |= {'view_maintenancejob', 'add_maintenancejob', 'change_maintenancejob', 'verify_maintenancejob', 'view_equipment', 'add_equipment', 'add_maintenanceplan'}
PRESETS['Quality manager'] |= {'view_samplingplan', 'add_samplingplan', 'change_samplingplan'}

def initialize_roles(update=False):
    for name, codes in PRESETS.items():
        group, created = Group.objects.get_or_create(name=name)
        if created or update:
            group.permissions.set(Permission.objects.filter(content_type__app_label='erp', codename__in=codes))


def order_permission(user, kind, action, site=None, makers=()):
    if action == 'approve' and site is not None:
        from .delegation import can_approve
        return user.pk not in makers and can_approve(user, f'approve_{"sales" if kind == "Sale" else "purchase"}_order', site, makers=makers)
    return user.has_perm(f'erp.{action}_{"sales" if kind == "Sale" else "purchase"}_order')
