from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from erp import views as v
urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/login/', auth.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth.LogoutView.as_view(), name='logout'),
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
]
for module in ['sites', 'products', 'partners']:
    urlpatterns += [path(f'{module}/', v.master_list, {'module': module}, name=module), path(f'{module}/new/', v.master_form, {'module': module}, name=module+'_new'), path(f'{module}/<int:pk>/edit/', v.master_form, {'module': module}, name=module+'_edit')]
