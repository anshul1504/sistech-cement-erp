"""Administration: users, roles & permissions, and system settings (super administrators only)."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.models import Group, Permission, User
from django.contrib.sessions.models import Session
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from erp.models import Site, SiteAccess, AuditEvent
from .forms import MemberForm, NewMemberForm, AdminSetPasswordForm
from .models import UserProfile
from .roles import ACTIONS, MODULES, MANAGED_CODES, PRESETS
from .account_views import sessions_for, access_summary


def require_admin(request):
    if not request.user.is_superuser:
        raise PermissionDenied


def editable_member(pk):
    """Team members only: super administrator accounts are not changed from these screens."""
    member = get_object_or_404(User, pk=pk)
    if member.is_superuser:
        raise PermissionDenied('Super administrator accounts are managed separately in Django administration.')
    return member


def audit(request, action, reference, detail=''):
    AuditEvent.objects.create(actor=request.user, action=action, reference=reference, detail=detail)


# ===================== Users =====================

@login_required
def access_control(request):
    """Users list with search, filters and summary counts."""
    require_admin(request)
    users = User.objects.prefetch_related('groups', 'siteaccess_set__site').select_related('profile').order_by('-is_active', 'first_name', 'username')
    q, role, site, status = (request.GET.get(k, '').strip() for k in ('q', 'role', 'site', 'status'))
    if q:
        users = users.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q) | Q(profile__designation__icontains=q))
    if role:
        users = users.filter(groups__pk=role)
    if site:
        users = users.filter(siteaccess__site__pk=site)
    if status == 'active':
        users = users.filter(is_active=True)
    elif status == 'disabled':
        users = users.filter(is_active=False)
    elif status == 'never':
        users = users.filter(last_login__isnull=True)
    elif status == 'admin':
        users = users.filter(is_superuser=True)
    all_users = User.objects.all()
    stats = {'total': all_users.count(), 'active': all_users.filter(is_active=True).count(),
             'disabled': all_users.filter(is_active=False).count(), 'never': all_users.filter(last_login__isnull=True).count(),
             'admins': all_users.filter(is_superuser=True).count()}
    return render(request, 'accounts/users.html', {
        'title': 'Users', 'page': Paginator(users.distinct(), 20).get_page(request.GET.get('page')), 'stats': stats,
        'roles': Group.objects.order_by('name'), 'sites': Site.objects.order_by('code'),
        'filters': {'q': q, 'role': role, 'site': site, 'status': status}})


@login_required
def member_detail(request, pk):
    require_admin(request)
    member = get_object_or_404(User.objects.prefetch_related('groups'), pk=pk)
    sessions = sessions_for(member)
    tracked = [x for x in sessions if x['signed_in_at']]
    return render(request, 'accounts/user_detail.html', {
        'title': member.get_full_name() or member.username, 'member': member,
        'member_profile': UserProfile.objects.filter(user=member).first(),
        'assigned_sites': member.siteaccess_set.select_related('site').order_by('site__code'),
        'access': access_summary(member), 'sessions': sessions,
        'tracked_sessions': tracked, 'legacy_count': len(sessions) - len(tracked),
        'activity': AuditEvent.objects.filter(actor=member).order_by('-created_at')[:10],
        'changes': AuditEvent.objects.filter(reference=f'user:{member.pk}').exclude(actor=member).select_related('actor').order_by('-created_at')[:5],
        'is_self': member == request.user})


@login_required
@sensitive_post_parameters('password1', 'password2')
@never_cache
def member_edit(request, pk=None):
    require_admin(request)
    member = editable_member(pk) if pk else User()
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
            profile = UserProfile.for_user(member)
            profile.phone, profile.designation, profile.department = (form.cleaned_data[k] for k in ('phone', 'designation', 'department'))
            if not pk:
                profile.password_changed_at = timezone.now()
            profile.save()
            audit(request, 'User access updated' if pk else 'User created', f'user:{member.pk}',
                  f'Active: {member.is_active}; roles: {list(member.groups.values_list("name", flat=True))}; sites: {list(form.cleaned_data["sites"].values_list("code", flat=True))}')
        messages.success(request, 'User saved. Role and location changes apply on their next request.' if pk else f'{member.get_full_name() or member.username} can now sign in.')
        return redirect('member_detail', pk=member.pk)
    return render(request, 'accounts/member_form.html', {'title': 'Edit user' if pk else 'Add user', 'form': form, 'member': member})


@login_required
@require_POST
def member_status(request, pk):
    require_admin(request)
    member = editable_member(pk)
    if member == request.user:
        raise PermissionDenied
    member.is_active = not member.is_active
    member.save(update_fields=['is_active'])
    if not member.is_active:
        _end_sessions(member)
    audit(request, 'User enabled' if member.is_active else 'User disabled', f'user:{member.pk}')
    messages.success(request, f'{member.get_full_name() or member.username} {"can sign in again" if member.is_active else "is disabled and has been signed out"}.')
    return redirect('member_detail', pk=member.pk)


def _end_sessions(member):
    keys = [s['key'] for s in sessions_for(member)]
    Session.objects.filter(session_key__in=keys).delete()
    return len(keys)


@login_required
@require_POST
def member_sign_out(request, pk):
    require_admin(request)
    member = editable_member(pk)
    ended = _end_sessions(member)
    audit(request, 'User signed out everywhere', f'user:{member.pk}', f'{ended} session(s) ended')
    messages.success(request, f'Signed {member.get_full_name() or member.username} out of {ended} device(s).')
    return redirect('member_detail', pk=member.pk)


@login_required
@require_POST
def member_reset_link(request, pk):
    require_admin(request)
    member = editable_member(pk)
    email = (member.email or '').strip()
    if not member.is_active or not email or email.lower().endswith(('.test', '.invalid', '.example')):
        messages.error(request, 'A reset link needs an active account with a real email address.')
        return redirect('member_detail', pk=member.pk)
    form = PasswordResetForm({'email': email})
    form.is_valid()
    try:
        form.save(request=request, use_https=request.is_secure(),
                  email_template_name='registration/password_reset_email.txt', subject_template_name='registration/password_reset_subject.txt')
    except Exception:
        messages.error(request, 'The email could not be sent. Check System settings → Email delivery.')
        return redirect('member_detail', pk=member.pk)
    audit(request, 'Password reset link sent', f'user:{member.pk}', email)
    messages.success(request, f'A password reset link (valid 30 minutes) was sent to {email}.')
    return redirect('member_detail', pk=member.pk)


@login_required
@sensitive_post_parameters('new_password1', 'new_password2')
@never_cache
def member_set_password(request, pk):
    require_admin(request)
    member = editable_member(pk)
    form = AdminSetPasswordForm(member, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        _end_sessions(member)
        profile = UserProfile.for_user(member)
        profile.password_changed_at = timezone.now()
        profile.save(update_fields=['password_changed_at', 'updated_at'])
        audit(request, 'Password set by administrator', f'user:{member.pk}')
        messages.success(request, f'New password set for {member.get_full_name() or member.username}. Share it securely; they have been signed out everywhere.')
        return redirect('member_detail', pk=member.pk)
    return render(request, 'accounts/member_password.html', {'title': 'Set password', 'form': form, 'member': member})


# ===================== Roles & permissions =====================

def role_matrix(selected):
    return [{'label': label, 'cells': [{'code': codes.get(action), 'checked': codes.get(action) in selected, 'label': caption} for action, caption in ACTIONS]}
            for _, label, codes in MODULES]


@login_required
def roles(request):
    require_admin(request)
    rows = []
    for role in Group.objects.annotate(member_count=Count('user', distinct=True)).prefetch_related('permissions').order_by('name'):
        codes = set(role.permissions.filter(content_type__app_label='erp').values_list('codename', flat=True))
        modules = [label for _, label, c in MODULES if codes & set(c.values())]
        rows.append({'role': role, 'members': role.member_count, 'permissions': len(codes & MANAGED_CODES), 'modules': modules,
                     'preset': role.name in PRESETS})
    return render(request, 'accounts/roles.html', {'title': 'Roles & permissions', 'rows': rows, 'module_total': len(MODULES)})


@login_required
def role_detail(request, pk):
    require_admin(request)
    role = get_object_or_404(Group, pk=pk)
    selected = set(role.permissions.filter(content_type__app_label='erp').values_list('codename', flat=True))
    return render(request, 'accounts/role_detail.html', {
        'title': role.name, 'role': role, 'matrix': role_matrix(selected), 'actions': ACTIONS,
        'members': role.user_set.select_related('profile').order_by('first_name', 'username'),
        'permission_count': len(selected & MANAGED_CODES), 'preset': role.name in PRESETS,
        'history': AuditEvent.objects.filter(reference=f'role:{role.pk}').select_related('actor').order_by('-created_at')[:8]})


@login_required
@transaction.atomic
def role_edit(request, pk=None, copy_from=None):
    require_admin(request)
    role = get_object_or_404(Group.objects.select_for_update(), pk=pk) if pk else Group()
    source = get_object_or_404(Group, pk=copy_from) if copy_from else (role if pk else None)
    selected = set(source.permissions.filter(content_type__app_label='erp').values_list('codename', flat=True)) if source else set()
    error, name = '', (f'{source.name} (copy)' if copy_from else role.name)
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
                needs = codes.get('view') or ('view_dispatch' if any(c.endswith('_dispatch') or c == 'approve_weight_exception' for c in codes.values()) else None)
                if needs and (selected & set(codes.values())) and needs not in selected:
                    error = f'Enable View for {label} before granting its actions.' if codes.get('view') else f'{label} needs View on Dispatch planning.'
                    break
        if not error:
            previous = set(role.permissions.values_list('codename', flat=True)) if pk else set()
            role.name = name
            role.save()
            role.permissions.remove(*role.permissions.filter(content_type__app_label='erp', codename__in=MANAGED_CODES))
            role.permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=selected))
            audit(request, 'Role updated' if pk else 'Role created', f'role:{role.pk}',
                  f'{name}; added: {sorted(selected-previous)}; removed: {sorted((previous & MANAGED_CODES)-selected)}' + (f'; copied from {source.name}' if copy_from else ''))
            messages.success(request, 'Role saved. Changes apply to its members on their next request.')
            return redirect('role_detail', pk=role.pk)
    unmanaged = role.permissions.exclude(content_type__app_label='erp', codename__in=MANAGED_CODES) if pk else []
    return render(request, 'accounts/role_form.html', {
        'title': 'Edit role' if pk else ('Duplicate role' if copy_from else 'Create role'), 'role': role, 'name': name,
        'matrix': role_matrix(selected), 'actions': ACTIONS, 'error': error, 'unmanaged': unmanaged, 'source': source if copy_from else None,
        'member_count': role.user_set.count() if pk else 0})


@login_required
def role_duplicate(request, pk):
    return role_edit(request, copy_from=pk)


@login_required
@require_POST
def role_delete(request, pk):
    require_admin(request)
    role = get_object_or_404(Group, pk=pk)
    count = role.user_set.count()
    if count:
        messages.error(request, f'“{role.name}” is assigned to {count} user{"s" if count != 1 else ""}. Remove it from them first.')
        return redirect('role_detail', pk=role.pk)
    name = role.name
    audit(request, 'Role deleted', f'role:{role.pk}', name)
    role.delete()
    messages.success(request, f'Role “{name}” deleted.')
    return redirect('roles')


# ===================== System settings =====================

def _check(label, value, ok=True, note=''):
    return {'label': label, 'value': value, 'state': 'ok' if ok is True else ('warn' if ok is False else ok), 'note': note}


@login_required
def system_settings(request):
    require_admin(request)
    backend = settings.EMAIL_BACKEND.rsplit('.', 1)[0].rsplit('.', 1)[-1]
    smtp = backend == 'smtp'
    db = settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1]
    min_len = next((v.get('OPTIONS', {}).get('min_length') for v in settings.AUTH_PASSWORD_VALIDATORS if v['NAME'].endswith('MinimumLengthValidator')), 8)
    production = not settings.DEBUG
    sections = [
        ('Email delivery', 'Used for sign-in codes and password reset links.', [
            _check('Delivery method', 'SMTP server' if smtp else 'Console (development only)', smtp, '' if smtp else 'Emails are printed in the server log, not delivered.'),
            _check('Mail server', f'{settings.EMAIL_HOST}:{settings.EMAIL_PORT}' if smtp else '—', smtp or 'info'),
            _check('Encryption', 'SSL' if settings.EMAIL_USE_SSL else ('STARTTLS' if settings.EMAIL_USE_TLS else 'None'), settings.EMAIL_USE_SSL or settings.EMAIL_USE_TLS or not smtp),
            _check('Sender', settings.DEFAULT_FROM_EMAIL, 'info'),
        ]),
        ('Sign-in security', 'Two-step verification and brute-force protection.', [
            _check('Email sign-in code (OTP)', 'On' if settings.EMAIL_OTP_ENABLED else 'Off', settings.EMAIL_OTP_ENABLED),
            _check('Username sign-in skips OTP', 'Yes' if settings.EMAIL_OTP_SKIP_FOR_USERNAME else 'No', not settings.EMAIL_OTP_SKIP_FOR_USERNAME,
                   'Set EMAIL_OTP_SKIP_FOR_USERNAME=0 before going live.' if settings.EMAIL_OTP_SKIP_FOR_USERNAME else ''),
            _check('Code validity', f'{settings.EMAIL_OTP_TTL_SECONDS // 60} minutes · {settings.EMAIL_OTP_MAX_ATTEMPTS} attempts', 'info'),
            _check('Sign-in lockout', '5 attempts per account, 30 per IP in 15 minutes', 'info'),
        ]),
        ('Sessions & passwords', 'How long people stay signed in and password rules.', [
            _check('Session length', f'{settings.SESSION_COOKIE_AGE // 3600} hours with “Keep me signed in”, otherwise until the browser closes', 'info'),
            _check('Minimum password length', f'{min_len} characters', min_len >= 10),
            _check('Password reset link', f'Valid {settings.PASSWORD_RESET_TIMEOUT // 60} minutes, single use', 'info'),
        ]),
        ('Server', 'Deployment checks. Change these in the server environment (.env).', [
            _check('Mode', 'Production' if production else 'Development (DEBUG on)', production, '' if production else 'Set DJANGO_DEBUG=0 on the live server.'),
            _check('HTTPS & secure cookies', 'Enforced' if production else 'Not enforced in development', production),
            _check('Allowed hosts', ', '.join(settings.ALLOWED_HOSTS), 'info'),
            _check('Database', db, db != 'sqlite3' or 'info', 'Use PostgreSQL for multi-user production.' if db == 'sqlite3' else ''),
        ]),
    ]
    warnings = sum(1 for _, _, rows in sections for r in rows if r['state'] == 'warn')
    return render(request, 'accounts/system_settings.html', {'title': 'System settings', 'sections': sections, 'warnings': warnings, 'smtp': smtp})


@login_required
@require_POST
def system_test_email(request):
    require_admin(request)
    to = (request.user.email or '').strip()
    if not to:
        messages.error(request, 'Your account has no email address. Add one to send a test email.')
        return redirect('system_settings')
    try:
        send_mail('Sistech ERP test email', 'This is a test email from Sistech ERP. Email delivery is working.\n\nDesign & developed by The Webfix · https://thewebfix.in',
                  None, [to], fail_silently=False)
    except Exception as error:
        messages.error(request, f'Test email failed: {type(error).__name__}. Check the mail server settings.')
        return redirect('system_settings')
    audit(request, 'Test email sent', 'system', to)
    messages.success(request, f'Test email sent to {to}. Check the inbox (and spam folder).')
    return redirect('system_settings')
