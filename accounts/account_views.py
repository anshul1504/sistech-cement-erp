"""My account: overview, profile editing, security (password + devices) and activity."""
import hashlib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from erp.models import AuditEvent
from .forms import ProfileForm, ChangePasswordForm
from .models import UserProfile
from .otp import masked_email
from .roles import ACTIONS, MODULES

SECURITY_ACTIONS = ['Account signed in', 'Account signed out', 'Password changed', 'Password reset completed',
                    'Sign-in code sent', 'OTP delivery failed', 'Other sessions signed out', 'Session signed out',
                    'Profile updated', 'Emergency contact added', 'Emergency contact updated', 'Emergency contact removed']
PASSWORD_REVIEW_DAYS = 90


# ---------- helpers ----------

def session_id(key):
    """Opaque, non-reversible id for a session (the real key is never sent to the browser)."""
    return hashlib.sha256(f'{settings.SECRET_KEY}:{key}'.encode()).hexdigest()[:20]


def user_sessions(request):
    """Active sessions of the current user, current device first."""
    rows = []
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if data.get('_auth_user_id') != str(request.user.pk):
            continue
        signed_in = parse_datetime(data.get('signed_in_at', '') or '')
        rows.append({'id': session_id(session.session_key), 'key': session.session_key,
                     'current': session.session_key == request.session.session_key,
                     'client': data.get('client') or 'Device details not recorded (signed in before tracking)',
                     'signed_in_at': signed_in, 'expires': session.expire_date})
    rows.sort(key=lambda r: (not r['current'], -(r['signed_in_at'].timestamp() if r['signed_in_at'] else 0)))
    return rows


def other_sessions(request):
    return [r for r in user_sessions(request) if not r['current']]


def access_summary(user):
    """What this user can do, module by module, from the role matrix."""
    rows = []
    for _, label, codes in MODULES:
        allowed = [caption for action, caption in ACTIONS if codes.get(action) and user.has_perm('erp.' + codes[action])]
        if allowed:
            rows.append({'module': label, 'actions': allowed})
    return rows


def two_step_status(user):
    """(level, message) describing how the second sign-in step applies to this user."""
    email = (user.email or '').strip()
    if not settings.EMAIL_OTP_ENABLED:
        return 'off', 'Two-step sign-in is turned off on this server. Only your password is checked.'
    if not email or email.lower().endswith(('.test', '.invalid', '.example')):
        return 'warn', 'Your account has no deliverable email, so sign-in codes cannot be sent. Ask your administrator to set your company email.'
    target = masked_email(email)
    if settings.EMAIL_OTP_SKIP_FOR_USERNAME:
        return 'partial', f'Signing in with your email sends a 6-digit code to {target}. Signing in with your username skips the code on this server.'
    return 'on', f'Every sign-in asks for a 6-digit code sent to {target}.'


def account_context(request, active):
    profile = UserProfile.for_user(request.user)
    return {'profile': profile, 'active_tab': active, 'session_count': len(user_sessions(request)),
            'contact_count': request.user.emergency_contacts.count()}


# ---------- pages ----------

@login_required
def profile(request):
    user = request.user
    ctx = account_context(request, 'overview')
    level, message = two_step_status(user)
    ctx.update({
        'title': 'My account',
        'roles': user.groups.order_by('name'),
        'assigned_sites': user.siteaccess_set.select_related('site').order_by('site__code'),
        'access': access_summary(user),
        'two_step': {'level': level, 'message': message},
        'recent': AuditEvent.objects.filter(actor=user, action__in=SECURITY_ACTIONS).order_by('-created_at')[:5],
        'password_review_days': PASSWORD_REVIEW_DAYS,
        'contacts': user.emergency_contacts.all()[:3],
    })
    return render(request, 'accounts/profile.html', ctx)


@login_required
def profile_edit(request):
    user = request.user
    profile = UserProfile.for_user(user)
    initial = {'first_name': user.first_name, 'last_name': user.last_name, 'phone': profile.phone,
               'designation': profile.designation, 'department': profile.department}
    form = ProfileForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        changed = [name for name in form.changed_data if name != 'remove_photo' or data['remove_photo']]
        user.first_name, user.last_name = data['first_name'], data['last_name']
        user.save(update_fields=['first_name', 'last_name'])
        profile.phone, profile.designation, profile.department = data['phone'], data['designation'], data['department']
        if data.get('photo'):
            profile.photo, profile.photo_type = data['photo']
        elif data.get('remove_photo'):
            profile.photo, profile.photo_type = None, ''
        profile.save()
        AuditEvent.objects.create(actor=user, action='Profile updated', reference=f'user:{user.pk}', detail='Changed: ' + (', '.join(changed) or 'nothing'))
        messages.success(request, 'Your profile has been updated.')
        return redirect('profile')
    ctx = account_context(request, 'overview')
    ctx.update({'title': 'Edit profile', 'form': form})
    return render(request, 'accounts/profile_edit.html', ctx)


class SecurityView(auth.PasswordChangeView):
    """Security tab: change password, two-step status and signed-in devices."""
    template_name = 'accounts/security.html'
    form_class = ChangePasswordForm
    success_url = reverse_lazy('account_security')

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'request': self.request}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(account_context(self.request, 'security'))
        level, message = two_step_status(self.request.user)
        sessions = user_sessions(self.request)
        ctx.update({'title': 'Security',
                    'sessions': [x for x in sessions if x['current'] or x['signed_in_at']],
                    'legacy_sessions': [x for x in sessions if not x['current'] and not x['signed_in_at']],
                    'session_total': len(sessions),
                    'two_step': {'level': level, 'message': message}, 'password_review_days': PASSWORD_REVIEW_DAYS})
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        profile = UserProfile.for_user(self.request.user)
        profile.password_changed_at = timezone.now()
        profile.save(update_fields=['password_changed_at', 'updated_at'])
        AuditEvent.objects.create(actor=self.request.user, action='Password changed', reference=f'user:{self.request.user.pk}')
        messages.success(self.request, 'Password changed. You stay signed in here; other devices have been signed out.')
        return response


@login_required
@require_POST
def session_revoke(request, sid):
    target = next((r for r in other_sessions(request) if r['id'] == sid), None)
    if not target:
        messages.error(request, 'That session has already ended.')
        return redirect('account_security')
    Session.objects.filter(session_key=target['key']).delete()
    AuditEvent.objects.create(actor=request.user, action='Session signed out', reference=f'user:{request.user.pk}', detail=target['client'])
    messages.success(request, 'That device has been signed out.')
    return redirect('account_security')


@login_required
@require_POST
def sign_out_legacy_sessions(request):
    """Sign out older sessions that were created before device details were recorded."""
    legacy = [r for r in other_sessions(request) if not r['signed_in_at']]
    Session.objects.filter(session_key__in=[r['key'] for r in legacy]).delete()
    AuditEvent.objects.create(actor=request.user, action='Other sessions signed out', reference=f'user:{request.user.pk}', detail=f'{len(legacy)} older session(s) ended')
    messages.success(request, f'Signed out of {len(legacy)} older session(s).')
    return redirect('account_security')


@login_required
@require_POST
def sign_out_other_sessions(request):
    sessions = other_sessions(request)
    Session.objects.filter(session_key__in=[s['key'] for s in sessions]).delete()
    AuditEvent.objects.create(actor=request.user, action='Other sessions signed out', reference=f'user:{request.user.pk}', detail=f'{len(sessions)} session(s) ended')
    messages.success(request, f'Signed out of {len(sessions)} other device(s). This device stays signed in.' if sessions else 'No other active sessions were found.')
    return redirect('account_security')


@login_required
def activity(request):
    kind = request.GET.get('kind', 'all')
    events = AuditEvent.objects.filter(actor=request.user).select_related('site').order_by('-created_at')
    if kind == 'security':
        events = events.filter(action__in=SECURITY_ACTIONS)
    elif kind == 'work':
        events = events.exclude(action__in=SECURITY_ACTIONS)
    else:
        kind = 'all'
    ctx = account_context(request, 'activity')
    ctx.update({'title': 'My activity', 'page': Paginator(events, 20).get_page(request.GET.get('page')), 'kind': kind,
                'security_actions': SECURITY_ACTIONS})
    return render(request, 'accounts/activity.html', ctx)


@login_required
def photo(request, user_id):
    """Profile photos are visible to signed-in colleagues only."""
    profile = UserProfile.objects.filter(user_id=user_id).first()
    if not profile or not profile.photo:
        raise Http404
    response = HttpResponse(bytes(profile.photo), content_type=profile.photo_type)
    response['Cache-Control'] = 'private, max-age=31536000, immutable' if request.GET.get('v') else 'private, max-age=300'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


# ---------- emergency contacts (owned by the signed-in user) ----------
MAX_CONTACTS = 5


@login_required
def contacts(request):
    ctx = account_context(request, 'contacts')
    ctx.update({'title': 'Emergency contacts', 'contacts': request.user.emergency_contacts.all(), 'max_contacts': MAX_CONTACTS})
    return render(request, 'accounts/contacts.html', ctx)


@login_required
def contact_form(request, pk=None):
    from django.shortcuts import get_object_or_404
    from .forms import EmergencyContactForm
    contact = get_object_or_404(request.user.emergency_contacts, pk=pk) if pk else None
    if not contact and request.user.emergency_contacts.count() >= MAX_CONTACTS:
        messages.error(request, f'You can keep up to {MAX_CONTACTS} emergency contacts. Edit or delete one first.')
        return redirect('account_contacts')
    form = EmergencyContactForm(request.POST or None, instance=contact)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        saved.user = request.user
        first = not request.user.emergency_contacts.exclude(pk=saved.pk).exists()
        if first:
            saved.is_primary = True  # the only contact is always the primary one
        saved.save()
        if saved.is_primary:
            request.user.emergency_contacts.exclude(pk=saved.pk).update(is_primary=False)
        AuditEvent.objects.create(actor=request.user, action='Emergency contact updated' if pk else 'Emergency contact added',
                                  reference=f'user:{request.user.pk}', detail=saved.name)
        messages.success(request, f'{saved.name} {"updated" if pk else "added"}.')
        return redirect('account_contacts')
    ctx = account_context(request, 'contacts')
    ctx.update({'title': 'Edit emergency contact' if pk else 'Add emergency contact', 'form': form, 'contact': contact})
    return render(request, 'accounts/contact_form.html', ctx)


@login_required
@require_POST
def contact_delete(request, pk):
    from django.shortcuts import get_object_or_404
    contact = get_object_or_404(request.user.emergency_contacts, pk=pk)
    name, was_primary = contact.name, contact.is_primary
    contact.delete()
    if was_primary:
        replacement = request.user.emergency_contacts.first()
        if replacement:
            replacement.is_primary = True
            replacement.save(update_fields=['is_primary', 'updated_at'])
    AuditEvent.objects.create(actor=request.user, action='Emergency contact removed', reference=f'user:{request.user.pk}', detail=name)
    messages.success(request, f'{name} removed.')
    return redirect('account_contacts')
