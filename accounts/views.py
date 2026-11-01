from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.cache import never_cache
from erp.models import SiteAccess, AuditEvent
from .forms import MemberForm, NewMemberForm
from .roles import ACTIONS, MODULES, MANAGED_CODES

def require_admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied

@login_required
def access_control(request):
    require_admin(request)
    return render(request, 'accounts/access_control.html', {'title': 'Access control', 'roles': Group.objects.prefetch_related('permissions', 'user_set').order_by('name'), 'members': User.objects.prefetch_related('groups', 'siteaccess_set__site').order_by('username'), 'active_count': User.objects.filter(is_active=True).count()})

@login_required
@transaction.atomic
def role_edit(request, pk=None):
    require_admin(request)
    role = get_object_or_404(Group.objects.select_for_update(), pk=pk) if pk else Group()
    selected = set(role.permissions.filter(content_type__app_label='erp').values_list('codename', flat=True)) if pk else set()
    error, name = '', role.name
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        selected = set(request.POST.getlist('permissions'))
        if not name or len(name) > 150:
            error = 'Enter a role name with 1–150 characters.'
        elif Group.objects.exclude(pk=pk).filter(name__iexact=name).exists():
            error = 'A role with this name already exists.'
        elif selected - MANAGED_CODES:
            error = 'The request contains permissions outside the ERP role matrix.'
        else:
            for _, label, codes in MODULES:
                if (selected & set(codes.values())) and codes['view'] not in selected:
                    error = f'Enable View for {label} before granting its actions.'
                    break
        if not error:
            previous = set(role.permissions.values_list('codename', flat=True)) if pk else set()
            role.name = name
            role.save()
            role.permissions.remove(*role.permissions.filter(content_type__app_label='erp', codename__in=MANAGED_CODES))
            role.permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=selected))
            AuditEvent.objects.create(actor=request.user, action='Role updated' if pk else 'Role created', reference=f'role:{role.pk}', detail=f'{name}; added: {sorted(selected-previous)}; removed: {sorted((previous & MANAGED_CODES)-selected)}')
            messages.success(request, 'Role matrix saved. Changes apply on the next request.')
            return redirect('access_control')
    matrix = [{'label': label, 'cells': [{'code': codes.get(action), 'checked': codes.get(action) in selected, 'label': caption} for action, caption in ACTIONS]} for _, label, codes in MODULES]
    unmanaged = role.permissions.exclude(content_type__app_label='erp', codename__in=MANAGED_CODES) if pk else []
    return render(request, 'accounts/role_form.html', {'title': 'Edit role' if pk else 'Create role', 'role': role, 'name': name, 'matrix': matrix, 'actions': ACTIONS, 'error': error, 'unmanaged': unmanaged})

@login_required
@sensitive_post_parameters('password1', 'password2')
@never_cache
def member_edit(request, pk=None):
    require_admin(request)
    member = get_object_or_404(User, pk=pk) if pk else User()
    if member.is_superuser:
        raise PermissionDenied('Super administrator accounts are managed separately in Django administration.')
    form = (MemberForm if pk else NewMemberForm)(request.POST or None, instance=member)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            member = form.save(commit=False)
            if not pk:
                member.username = form.cleaned_data['username']
                member.set_password(form.cleaned_data['password1'])
            member.save()
            member.groups.set(form.cleaned_data['roles'])
            SiteAccess.objects.filter(user=member).delete()
            SiteAccess.objects.bulk_create([SiteAccess(user=member, site=site) for site in form.cleaned_data['sites']])
            AuditEvent.objects.create(actor=request.user, action='User access updated' if pk else 'User created', reference=f'user:{member.pk}', detail=f'Active: {member.is_active}; roles: {list(member.groups.values_list("name", flat=True))}; sites: {list(form.cleaned_data["sites"].values_list("code", flat=True))}')
        messages.success(request, 'User access saved. Disabled users lose access; role and site changes apply on the next request.')
        return redirect('access_control')
    return render(request, 'accounts/member_form.html', {'title': f'Edit access · {member.username}' if pk else 'Create team member', 'form': form, 'member': member})
