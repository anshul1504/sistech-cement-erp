from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from .models import AuthRateLimit


@transaction.atomic
def allow_attempt(request, purpose, identifier='', limit=5):
    """15-minute window. No passwords, raw email addresses or IPs stored."""
    now = timezone.now()
    keys = [(f'{purpose}:ip:{request.META.get("REMOTE_ADDR", "unknown")}', 30)]
    if identifier:
        keys.append((f'{purpose}:identity:{identifier.strip().casefold()}', limit))
    allowed = True
    for value, maximum in keys:
        digest = salted_hmac('auth-throttle', value).hexdigest()
        row, _ = AuthRateLimit.objects.get_or_create(key=digest, defaults={'started_at': now})
        row = AuthRateLimit.objects.select_for_update().get(pk=row.pk)
        if row.started_at <= now - timedelta(minutes=15):
            row.started_at, row.attempts = now, 0
        if row.attempts >= maximum:
            allowed = False
        row.attempts += 1
        row.save(update_fields=['started_at', 'attempts'])
    return allowed


def clear_identity_limit(identifier):
    value = f'login:identity:{identifier.strip().casefold()}'
    AuthRateLimit.objects.filter(key=salted_hmac('auth-throttle', value).hexdigest()).delete()


def access_allowed(user, now=None):
    """Expiry is checked against current persisted policy, including cached user instances."""
    from .models import UserProfile
    return bool(user and user.is_authenticated and user.is_active and not
                UserProfile.objects.filter(user_id=user.pk, access_expires_at__lte=now or timezone.now()).exists())
