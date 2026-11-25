"""Mobile API sign-in and request authentication (SRS Phase 4: Android apps).

Sign-in follows the web policy exactly: password first (same rate limit), then — when email OTP applies to that
identifier — the 6-digit email code, bound to the server session the app keeps for those two calls. Success returns a
bearer token once. Every API call then sends `Authorization: Bearer <token>`; the token stops working when it is
revoked (sign-out), after 30 days, when the password changes, or when the account is disabled or expired.
"""
import hashlib
import json
import secrets
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from erp.models import AuditEvent
from .models import ApiToken
from .otp import fingerprint, issue_challenge, verify_challenge, masked_email
from .security import allow_attempt, access_allowed, clear_identity_limit

TOKEN_DAYS = 30


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_token(user, device=''):
    raw = 'sst_' + secrets.token_urlsafe(32)
    ApiToken.objects.create(user=user, key_hash=_hash(raw), credential_hash=fingerprint(user.password, 'password'), device=(device or '')[:80],
                            expires_at=timezone.now() + timedelta(days=TOKEN_DAYS))
    AuditEvent.objects.create(actor=user, action='App signed in', reference=f'user:{user.pk}', detail=(device or 'Android app')[:250])
    return raw


def user_for(request):
    """(user, token) for a valid bearer token, else (None, None)."""
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return None, None
    token = ApiToken.objects.select_related('user').filter(key_hash=_hash(header[7:].strip())).first()
    now = timezone.now()
    if (token is None or token.revoked_at or token.expires_at <= now or not access_allowed(token.user)
            or token.credential_hash != fingerprint(token.user.password, 'password')):
        return None, None
    if not token.last_used_at or token.last_used_at < now - timedelta(minutes=1):
        ApiToken.objects.filter(pk=token.pk).update(last_used_at=now)
    return token.user, token


def error(message, status=400, **extra):
    return JsonResponse({'ok': False, 'error': message, **extra}, status=status)


def body(request):
    try:
        return json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return {}


def api(methods=('GET',), perm=None):
    """Decorator for API views: bearer-token user, allowed methods, optional permission (string or callable(user))."""
    def wrap(view):
        @csrf_exempt
        @wraps(view)
        def inner(request, *args, **kwargs):
            if request.method not in methods:
                return error('Method not allowed.', 405)
            user, token = user_for(request)
            if user is None:
                return error('Sign in again.', 401)
            if perm and not (perm(user) if callable(perm) else user.has_perm(perm)):
                return error('You do not have access to this.', 403)
            request.user, request.api_token = user, token
            return view(request, *args, **kwargs)
        return inner
    return wrap


# ---------- Sign-in endpoints ----------

def _otp_applies(identifier):
    from .auth_views import otp_required_for
    return settings.EMAIL_OTP_ENABLED and otp_required_for(identifier)


@csrf_exempt
def login(request):
    if request.method != 'POST':
        return error('Method not allowed.', 405)
    data = body(request)
    identifier, password, device = (data.get('username') or '').strip(), data.get('password') or '', (data.get('device') or '')[:80]
    if not allow_attempt(request, 'login', identifier):
        return error('Too many sign-in attempts. Please wait 15 minutes and try again.', 429)
    user = authenticate(request, username=identifier, password=password)
    if user is None or not access_allowed(user):
        return error('We couldn’t sign you in. Check your details and try again.', 401)
    if _otp_applies(identifier):
        if not request.session.session_key:
            request.session.save()
        request.session.cycle_key()
        try:
            challenge = issue_challenge(request, user)
        except ValidationError as e:
            return error(' '.join(e.messages), 400)
        request.session['otp_challenge'] = str(challenge.pk)
        request.session['api_device'] = device
        request.session.set_expiry(15 * 60)
        return JsonResponse({'ok': True, 'otp_required': True, 'email': masked_email(user.email)})
    clear_identity_limit(identifier)
    return JsonResponse({'ok': True, 'otp_required': False, 'token': issue_token(user, device), 'expires_in_days': TOKEN_DAYS})


@csrf_exempt
def verify(request):
    if request.method != 'POST':
        return error('Method not allowed.', 405)
    code = str(body(request).get('code') or '').strip()
    if not (len(code) == 6 and code.isdigit()):
        return error('Enter the 6-digit code from your email.')
    challenge_id = request.session.get('otp_challenge')
    if not challenge_id:
        return error('Your sign-in session has ended. Start again.', 401)
    from .models import EmailOTPChallenge
    ch = EmailOTPChallenge.objects.filter(pk=challenge_id).first()
    if ch and not allow_attempt(request, 'otp-verify', str(ch.user_id), limit=10):
        return error('Too many attempts. Start signing in again in 15 minutes.', 429)
    user, message = verify_challenge(request, code)
    if user is None:
        return error(message, 401)
    device = request.session.get('api_device', '')
    request.session.flush()
    return JsonResponse({'ok': True, 'token': issue_token(user, device), 'expires_in_days': TOKEN_DAYS})


@api(methods=('POST',))
def logout(request):
    ApiToken.objects.filter(pk=request.api_token.pk).update(revoked_at=timezone.now())
    AuditEvent.objects.create(actor=request.user, action='App signed out', reference=f'user:{request.user.pk}')
    return JsonResponse({'ok': True})
