"""Schedule periodically to remove expired sessions; request-time enforcement is immediate."""
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import EmailOTPChallenge, UserProfile
from erp.models import AuditEvent


class Command(BaseCommand):
    help = 'Disable expired temporary accounts, consume OTP challenges and revoke database sessions.'

    def handle(self, *args, **options):
        now = timezone.now()
        # Lock each policy so renewal cannot race the scheduled cleanup.
        count = 0
        for pk, user_id in UserProfile.objects.filter(access_expires_at__lte=now, user__is_active=True).values_list('pk', 'user_id'):
            with transaction.atomic():
                user = get_user_model().objects.select_for_update().get(pk=user_id)
                profile = UserProfile.objects.select_for_update().get(pk=pk)
                if profile.access_expires_at is None or profile.access_expires_at > now:
                    continue
                if not user.is_active:
                    continue
                user.is_active = False
                user.save(update_fields=['is_active'])
                EmailOTPChallenge.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=now)
                # Sessions are encoded, so inspect only active sessions; no password/code logging.
                keys = [s.session_key for s in Session.objects.filter(expire_date__gt=now).iterator()
                        if str(s.get_decoded().get('_auth_user_id')) == str(user.pk)]
                Session.objects.filter(session_key__in=keys).delete()
                AuditEvent.objects.create(action='Temporary access expired', reference=f'user:{user.pk}',
                                          detail=f'Expiry {profile.access_expires_at.isoformat()}; {len(keys)} session(s) revoked.')
                count += 1
        self.stdout.write(self.style.SUCCESS(f'{count} expired account(s) disabled.'))
