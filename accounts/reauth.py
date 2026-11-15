"""Short-lived, session-bound confirmation for sensitive web actions (SRS §3.2)."""
from urllib.parse import urlencode, urlsplit, unquote
from math import isfinite

from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

TTL_SECONDS = 300
SESSION_KEY = 'sensitive_auth'


def confirm(session, user):
    """Call only after checking credentials, or completing the password + OTP sign-in."""
    session[SESSION_KEY] = {
        'user': str(user.pk), 'credential': user.get_session_auth_hash(),
        'email': user.email.strip().casefold(), 'at': timezone.now().timestamp(),
        'session': salted_hmac('sensitive-auth-session', session.session_key or '').hexdigest(),
    }


def recent(request):
    proof = request.session.get(SESSION_KEY)
    if not isinstance(proof, dict):
        return False
    at = proof.get('at')
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    try:
        at = float(at)
    except (ValueError, OverflowError):
        return False
    if not isfinite(at):
        return False
    age = timezone.now().timestamp() - at
    return (0 <= age < TTL_SECONDS and proof.get('user') == str(request.user.pk)
            and proof.get('email') == request.user.email.strip().casefold()
            and isinstance(proof.get('credential'), str)
            and constant_time_compare(proof['credential'], request.user.get_session_auth_hash())
            and isinstance(proof.get('session'), str)
            and constant_time_compare(proof['session'], salted_hmac(
                'sensitive-auth-session', request.session.session_key or '').hexdigest()))


def safe_destination(value):
    """Use only local absolute paths, including when a proxy supplies an unexpected host."""
    value = (value or '').strip()
    decoded = unquote(value)
    if (len(value) > 2048 or not value.startswith('/') or decoded.startswith('//')
            or '\\' in decoded or any(ord(c) < 32 for c in decoded)):
        return '/'
    try:
        parts = urlsplit(value)
    except ValueError:
        return '/'
    return '/' if parts.scheme or parts.netloc else value


# Action routes return to their GET detail page. No POST body or file is retained/replayed.
DETAIL_ROUTES = {
    'payment_action': 'payment_detail', 'journal_action': 'journal_detail',
    'period_action': 'period_detail', 'year_action': 'year_detail',
    'order_action': 'order_detail', 'quotation_action': 'quotation_detail',
    'damage_decide': 'damage_detail', 'count_action': 'count_detail',
    'transfer_action': 'transfer_detail',
    'crm_application_action': 'crm_application_detail',
    'member_status': 'member_detail', 'member_sign_out': 'member_detail',
    'member_set_password': 'member_detail', 'role_delete': 'roles', 'role_duplicate': 'roles',
    'scheme_payout_decide': 'scheme_payouts', 'scheme_payout_new': 'scheme_payouts',
    'delegation_revoke': 'delegation_detail',
}


def confirmation_url(request, route, kwargs):
    target = DETAIL_ROUTES.get(route)
    if target:
        if target in {'roles', 'scheme_payouts'}:
            next_url = reverse(target)
        else:
            next_url = reverse(target, kwargs={'pk': kwargs['pk']})
    else:
        next_url = request.get_full_path() if request.method == 'GET' else request.path
    return reverse('reauthenticate') + '?' + urlencode({'next': safe_destination(next_url)})


def sensitive(request, match):
    """Single inventory for every currently exposed sensitive web entry point."""
    route, kw = match.url_name, match.kwargs
    if request.method == 'GET':
        return route == 'payments' and request.GET.get('export') == 'bank'
    if request.method != 'POST':
        return False
    if match.app_name == 'admin':
        return route not in {'login', 'logout'}
    if route in {
        'partners_new', 'partners_edit', 'organization_edit', 'crm_application_edit',
        'scheme_payout_new', 'scheme_payout_decide', 'damage_decide',
        'member_new', 'member_edit', 'member_status', 'member_set_password',
        'delegation_new', 'delegation_revoke',
        'role_new', 'role_edit', 'role_delete', 'role_duplicate',
        'prices_new', 'prices_edit', 'price_revise', 'price_end',
    }:
        return True
    actions = {
        'payment_action': {'approve', 'release', 'fail'},
        'journal_action': {'approve', 'reverse'},
        'period_action': {'reopen'},
        'year_action': {'approve', 'request_reopen', 'approve_reopen'},
        'order_action': {'approve'},
        'quotation_action': {'approve'},
        'count_action': {'approve'},
        'transfer_action': {'resolve'},
        'crm_application_action': {'credit', 'approve', 'activate'},
    }
    return kw.get('action') in actions.get(route, set())
