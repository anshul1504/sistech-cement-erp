from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path, include
from erp import views as v
from erp import order_views as ov
from accounts.auth_views import SignInView
from django.views.generic import RedirectView
urlpatterns = [
    # Keep privileged sign-in on the same throttled, styled authentication flow.
    path('admin/login/', SignInView.as_view(), name='admin_signin'),
    path('admin/password_change/', RedirectView.as_view(pattern_name='password_change')),
    path('admin/password_change/done/', RedirectView.as_view(pattern_name='password_change_done')),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('', v.dashboard, name='dashboard'),
    path('sales/', v.orders, {'kind': 'Sale'}, name='sales'),
    path('purchases/', v.orders, {'kind': 'Purchase'}, name='purchases'),
    path('orders/new/<str:kind>/', ov.order_new, name='order_new'),
    path('orders/price/', ov.price_lookup, name='order_price'),
    path('orders/<int:pk>/', ov.order_detail, name='order_detail'),
    path('orders/<int:pk>/edit/', ov.order_edit, name='order_edit'),
    path('orders/<int:pk>/<str:action>/', v.order_action, name='order_action'),
    path('inventory/opening/', v.opening, name='opening'),
    path('quality/new/', v.quality_new, name='quality_new'),
    path('reports/', v.reports, name='reports'),
    path('audit/', v.audit_list, name='audit'),
    path('organisation/', v.organization, name='organization'),
    path('organisation/edit/', v.organization_edit, name='organization_edit'),
    path('organisation/documents/', v.organization_documents, name='organization_documents'),
    path('organisation/documents/new/', v.organization_document_form, name='organization_document_new'),
    path('organisation/documents/<int:pk>/', v.organization_document, name='organization_document'),
    path('organisation/documents/<int:pk>/edit/', v.organization_document_form, name='organization_document_edit'),
    path('organisation/documents/<int:pk>/file/', v.organization_document_file, name='organization_document_file'),
    path('organisation/documents/<int:pk>/delete/', v.organization_document_delete, name='organization_document_delete'),
    path('brand/<str:kind>/', v.brand_asset, name='brand_asset'),
]
from erp import product_views as pv
urlpatterns += [
    path('products/', pv.products, name='products'),
    path('products/new/', pv.product_form, name='products_new'),
    path('products/catalogue/', pv.products_catalogue, name='products_catalogue'),
    path('products/<int:pk>/', pv.product_detail, name='product_detail'),
    path('products/<int:pk>/edit/', pv.product_form, name='products_edit'),
    path('products/<int:pk>/duplicate/', pv.product_duplicate, name='product_duplicate'),
    path('products/<int:pk>/status/', pv.product_status, name='product_status'),
    path('products/<int:pk>/delete/', pv.product_delete, name='product_delete'),
]
from erp import site_views as sv
urlpatterns += [
    path('sites/', sv.sites, name='sites'),
    path('sites/new/', sv.site_form, name='sites_new'),
    path('sites/<int:pk>/', sv.site_detail, name='site_detail'),
    path('sites/<int:pk>/edit/', sv.site_form, name='sites_edit'),
    path('sites/<int:pk>/status/', sv.site_status, name='site_status'),
    path('sites/<int:pk>/delete/', sv.site_delete, name='site_delete'),
]
from erp import partner_views as ptv
urlpatterns += [
    path('partners/', ptv.partners, name='partners'),
    path('partners/new/', ptv.partner_form, name='partners_new'),
    path('partners/<int:pk>/', ptv.partner_detail, name='partner_detail'),
    path('partners/<int:pk>/edit/', ptv.partner_form, name='partners_edit'),
    path('partners/<int:pk>/status/', ptv.partner_status, name='partner_status'),
    path('partners/<int:pk>/delete/', ptv.partner_delete, name='partner_delete'),
    path('partners/<int:pk>/documents/new/', ptv.partner_document_new, name='partner_document_new'),
    path('partners/<int:pk>/documents/<int:doc_pk>/file/', ptv.partner_document_file, name='partner_document_file'),
    path('partners/<int:pk>/documents/<int:doc_pk>/delete/', ptv.partner_document_delete, name='partner_document_delete'),
]
from erp import influencer_views as iv
urlpatterns += [
    path('influencers/', iv.influencers, name='influencers'),
    path('influencers/districts/', iv.influencer_districts, name='influencer_districts'),
    path('influencers/dealers/', iv.influencer_dealers, name='influencer_dealers'),
    path('influencers/new/', iv.influencer_form, name='influencers_new'),
    path('influencers/import/', iv.influencer_import, name='influencer_import'),
    path('influencers/<int:pk>/', iv.influencer_detail, name='influencer_detail'),
    path('influencers/<int:pk>/edit/', iv.influencer_form, name='influencers_edit'),
    path('influencers/<int:pk>/status/', iv.influencer_status, name='influencer_status'),
    path('influencers/<int:pk>/delete/', iv.influencer_delete, name='influencer_delete'),
    path('influencers/<int:pk>/activities/new/', iv.activity_new, name='influencer_activity_new'),
    path('influencers/<int:pk>/activities/<int:activity_pk>/status/', iv.activity_status, name='influencer_activity_status'),
    path('influencers/<int:pk>/activities/<int:activity_pk>/delete/', iv.activity_delete, name='influencer_activity_delete'),
]
from erp import pricing_views as prv
urlpatterns += [
    path('prices/', prv.prices, name='prices'),
    path('prices/new/', prv.price_form, name='prices_new'),
    path('prices/<int:pk>/edit/', prv.price_form, name='prices_edit'),
    path('prices/<int:pk>/revise/', prv.price_revise, name='price_revise'),
    path('prices/<int:pk>/end/', prv.price_end, name='price_end'),
]
from erp import invoice_views as inv
urlpatterns += [
    path('invoices/', inv.invoices, name='invoices'),
    path('invoices/<int:pk>/', inv.invoice_detail, name='invoice_detail'),
    path('invoices/new/<int:pk>/', inv.invoice_new, name='invoice_new'),
]
from erp import finance_views as fv
urlpatterns += [
    path('receipts/', fv.receipts, name='receipts'),
    path('receipts/new/', fv.receipt_new, name='receipt_new'),
    path('receipts/<int:pk>/', fv.receipt_detail, name='receipt_detail'),
    path('receipts/<int:pk>/bounce/', fv.receipt_bounce, name='receipt_bounce'),
    path('credit-notes/', fv.credit_notes, name='credit_notes'),
    path('credit-notes/<int:pk>/', fv.credit_note_detail, name='credit_note_detail'),
    path('invoices/<int:pk>/credit-note/', fv.credit_note_new, name='credit_note_new'),
    path('outstanding/', fv.outstanding, name='outstanding'),
    path('partners/<int:pk>/ledger/', fv.partner_ledger, name='partner_ledger'),
]
from erp import logistics_views as lv
urlpatterns += [
    path('vehicles/', lv.vehicles, name='vehicles'),
    path('vehicles/new/', lv.vehicle_form, name='vehicle_new'),
    path('vehicles/<int:pk>/edit/', lv.vehicle_form, name='vehicle_edit'),
    path('drivers/new/', lv.driver_form, name='driver_new'),
    path('drivers/<int:pk>/edit/', lv.driver_form, name='driver_edit'),
    path('dispatch/', lv.dispatch_board, name='dispatch_board'),
    path('dispatch/plan/<int:order_pk>/', lv.dispatch_new, name='dispatch_new'),
    path('dispatch/<int:pk>/', lv.dispatch_detail, name='dispatch_detail'),
    path('dispatch/<int:pk>/do/<str:action>/', lv.dispatch_action, name='dispatch_action'),
    path('dispatch/<int:pk>/print/<str:doc>/', lv.dispatch_print, name='dispatch_print'),
    path('dispatch/<int:pk>/pod/', lv.dispatch_pod, name='dispatch_pod'),
]
from erp import freight_views as frv
urlpatterns += [
    path('freight/rates/', frv.freight_rates, name='freight_rates'),
    path('freight/rates/new/', frv.freight_rate_form, name='freight_rate_new'),
    path('freight/rates/<int:pk>/edit/', frv.freight_rate_form, name='freight_rate_edit'),
    path('freight/bills/', frv.freight_bills, name='freight_bills'),
    path('freight/bills/new/<int:transporter_pk>/', frv.freight_bill_new, name='freight_bill_new'),
    path('freight/bills/<int:pk>/', frv.freight_bill_detail, name='freight_bill_detail'),
    path('freight/bills/<int:pk>/<str:decision>/', frv.freight_bill_decide, name='freight_bill_decide'),
]
from erp import procurement_views as pcv
urlpatterns += [
    path('purchase/requests/', pcv.purchase_requests, name='purchase_requests'),
    path('purchase/requests/new/', pcv.pr_form, name='pr_new'),
    path('purchase/requests/reorder/', pcv.reorder, name='reorder'),
    path('purchase/requests/<int:pk>/', pcv.pr_detail, name='pr_detail'),
    path('purchase/requests/<int:pk>/edit/', pcv.pr_form, name='pr_edit'),
    path('purchase/requests/<int:pk>/direct-po/', pcv.pr_direct, name='pr_direct'),
    path('purchase/requests/<int:pk>/rfq/', pcv.rfq_new, name='rfq_new'),
    path('purchase/requests/<int:pk>/do/<str:action>/', pcv.pr_action, name='pr_action'),
    path('purchase/rfq/', pcv.rfqs, name='rfqs'),
    path('purchase/rfq/<int:pk>/', pcv.rfq_detail, name='rfq_detail'),
    path('purchase/rfq/<int:pk>/quote/<int:supplier_pk>/', pcv.quote_form, name='quote_form'),
    path('purchase/rfq/<int:pk>/award/', pcv.rfq_award, name='rfq_award'),
    path('purchase/rfq/<int:pk>/cancel/', pcv.rfq_cancel, name='rfq_cancel'),
]
from erp import inbound_views as ibv
urlpatterns += [
    path('purchase/receipts/', ibv.inbound, name='inbound'),
    path('purchase/receipts/gate-in/<int:order_pk>/', ibv.grn_new, name='grn_new'),
    path('purchase/receipts/<int:pk>/', ibv.grn_detail, name='grn_detail'),
    path('purchase/receipts/<int:pk>/print/', ibv.grn_print, name='grn_print'),
    path('purchase/receipts/<int:pk>/photo/', ibv.grn_photo, name='grn_photo'),
    path('purchase/receipts/<int:pk>/do/<str:action>/', ibv.grn_action, name='grn_action'),
]
from erp import payables_views as apv
urlpatterns += [
    path('payables/invoices/', apv.supplier_invoices, name='supplier_invoices'),
    path('payables/invoices/new/<int:supplier_pk>/', apv.sinv_new, name='sinv_new'),
    path('payables/invoices/<int:pk>/', apv.sinv_detail, name='sinv_detail'),
    path('payables/invoices/<int:pk>/decide/<str:decision>/', apv.sinv_decide, name='sinv_decide'),
    path('payables/debit-notes/', apv.debit_notes, name='debit_notes'),
    path('payables/debit-notes/new/<int:grn_pk>/', apv.dn_new, name='dn_new'),
    path('payables/debit-notes/<int:pk>/', apv.dn_detail, name='dn_detail'),
]
from erp import stock_views as stv
urlpatterns += [
    path('inventory/', stv.inventory, name='inventory'),
    path('inventory/card/<int:site_pk>/<int:product_pk>/', stv.stock_card, name='stock_card'),
    path('inventory/transfers/', stv.transfers, name='transfers'),
    path('inventory/transfers/new/', stv.transfer_new, name='transfer_new'),
    path('inventory/transfers/<int:pk>/', stv.transfer_detail, name='transfer_detail'),
    path('inventory/transfers/<int:pk>/do/<str:action>/', stv.transfer_action, name='transfer_action'),
    path('inventory/damage/', stv.damages, name='damages'),
    path('inventory/damage/new/', stv.damage_new, name='damage_new'),
    path('inventory/damage/<int:pk>/', stv.damage_detail, name='damage_detail'),
    path('inventory/damage/<int:pk>/decide/', stv.damage_decide, name='damage_decide'),
    path('inventory/damage/<int:pk>/photo/', stv.damage_photo, name='damage_photo'),
    path('inventory/counts/', stv.counts, name='counts'),
    path('inventory/counts/<int:pk>/', stv.count_detail, name='count_detail'),
    path('inventory/counts/<int:pk>/do/<str:action>/', stv.count_action, name='count_action'),
    path('inventory/analysis/', stv.stock_analysis, name='stock_analysis'),
]
from erp import production_views as pdv
urlpatterns += [
    path('production/', pdv.production, name='production'),
    path('production/new/', pdv.production_new, name='production_new'),
    path('production/<int:pk>/', pdv.production_detail, name='production_detail'),
    path('production/<int:pk>/coa/', pdv.coa, name='coa'),
    path('production/<int:pk>/trace/', pdv.trace, name='trace'),
    path('production/<int:pk>/<str:action>/', pdv.production_action, name='production_action'),
    path('production/recipes/', pdv.recipes, name='recipes'),
    path('production/recipes/new/', pdv.recipe_new, name='recipe_new'),
    path('production/recipes/<int:pk>/approve/', pdv.recipe_approve, name='recipe_approve'),
    path('production/shifts/', pdv.shift_logs, name='shift_logs'),
    path('production/packing/', pdv.packing, name='packing'),
    path('quality/', pdv.quality, name='quality'),
    path('quality/equipment/', pdv.equipment_save, name='equipment_save'),
]
from erp import accounts_views as acv
urlpatterns += [
    path('accounts-ledger/', acv.journals, name='journals'),
    path('accounts-ledger/voucher/new/', acv.voucher_new, name='voucher_new'),
    path('accounts-ledger/journal/<int:pk>/', acv.journal_detail, name='journal_detail'),
    path('accounts-ledger/journal/<int:pk>/<str:action>/', acv.journal_action, name='journal_action'),
    path('accounts-ledger/trial-balance/', acv.trial_balance, name='trial_balance'),
    path('accounts-ledger/profit-loss/', acv.profit_loss, name='profit_loss'),
    path('accounts-ledger/balance-sheet/', acv.balance_sheet, name='balance_sheet'),
    path('accounts-ledger/account/<int:pk>/', acv.account_ledger, name='account_ledger'),
    path('accounts-ledger/chart/', acv.chart, name='chart'),
    path('accounts-ledger/periods/', acv.periods, name='periods'),
    path('accounts-ledger/periods/<int:pk>/<str:action>/', acv.period_action, name='period_action'),
]
from erp import treasury_views as trv
urlpatterns += [
    path('accounts-ledger/payments/', trv.payments, name='payments'),
    path('accounts-ledger/payments/new/<int:payee_pk>/', trv.payment_new, name='payment_new'),
    path('accounts-ledger/payments/<int:pk>/', trv.payment_detail, name='payment_detail'),
    path('accounts-ledger/payments/<int:pk>/<str:action>/', trv.payment_action, name='payment_action'),
    path('accounts-ledger/bank/', trv.bank_rec, name='bank_rec'),
    path('accounts-ledger/gst/', trv.gst_summary, name='gst_summary'),
    path('accounts-ledger/assets/', trv.fixed_assets, name='fixed_assets'),
    path('accounts-ledger/cash-flow/', trv.cash_flow, name='cash_flow'),
]
