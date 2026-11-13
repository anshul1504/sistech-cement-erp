from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path, include
from erp import views as v
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
    path('orders/new/<str:kind>/', v.order_new, name='order_new'),
    path('orders/<int:pk>/', v.order_detail, name='order_detail'),
    path('orders/<int:pk>/<str:action>/', v.order_action, name='order_action'),
    path('inventory/', v.inventory, name='inventory'),
    path('inventory/opening/', v.opening, name='opening'),
    path('production/', v.production, name='production'),
    path('production/new/', v.production_new, name='production_new'),
    path('production/<int:pk>/<str:action>/', v.production_action, name='production_action'),
    path('quality/', v.quality, name='quality'),
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
for module in ['sites', 'products', 'partners']:
    urlpatterns += [path(f'{module}/', v.master_list, {'module': module}, name=module), path(f'{module}/new/', v.master_form, {'module': module}, name=module+'_new'), path(f'{module}/<int:pk>/edit/', v.master_form, {'module': module}, name=module+'_edit')]
