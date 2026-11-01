from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import AuthRateLimit
from accounts.models import EmailOTPChallenge

class Command(BaseCommand):
    help = 'Delete expired authentication throttle windows. Safe to run daily.'
    def handle(self, *args, **options):
        count, _ = AuthRateLimit.objects.filter(started_at__lt=timezone.now()-timedelta(minutes=15)).delete()
        self.stdout.write(f'Deleted {count} expired authentication counters.')
        challenges, _ = EmailOTPChallenge.objects.filter(expires_at__lt=timezone.now()-timedelta(days=7)).delete()
        self.stdout.write(f'Deleted {challenges} OTP challenges older than seven days.')
