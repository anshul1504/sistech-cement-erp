from django.contrib import admin
from .models import Site, SiteAccess, Product, Partner, Order, OrderLine, StockBalance, StockMovement, Production, QualityTest, AuditEvent

admin.site.site_header = 'Sistech Cement ERP Administration'
admin.site.site_title = 'Sistech ERP'

@admin.register(SiteAccess)
class SiteAccessAdmin(admin.ModelAdmin):
    list_display = ['user', 'site']
    # Site access grants are security administration, never a module-user action.
    def has_module_permission(self, request): return request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    def has_module_permission(self, request): return request.user.is_superuser
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        from .services import scope
        if request.user.is_superuser: return qs
        if self.model is OrderLine: return scope(qs, request.user, 'order__site')
        if self.model in [Site, Product, Partner]: return qs.none()
        return scope(qs, request.user)

for model in [Site, Product, Partner, Order, OrderLine, StockBalance, StockMovement, Production, QualityTest, AuditEvent]:
    admin.site.register(model, ReadOnlyAdmin)

# Security administration is never delegated through ordinary model permissions.
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin, GroupAdmin

class SuperuserOnlyMixin:
    def has_module_permission(self, request): return request.user.is_superuser
    def has_view_permission(self, request, obj=None): return request.user.is_superuser
    def has_add_permission(self, request): return request.user.is_superuser
    def has_change_permission(self, request, obj=None): return request.user.is_superuser
    def has_delete_permission(self, request, obj=None): return request.user.is_superuser

class RestrictedUserAdmin(SuperuserOnlyMixin, UserAdmin): pass
class RestrictedGroupAdmin(SuperuserOnlyMixin, GroupAdmin): pass

admin.site.unregister(User)
admin.site.unregister(Group)
admin.site.register(User, RestrictedUserAdmin)
admin.site.register(Group, RestrictedGroupAdmin)
