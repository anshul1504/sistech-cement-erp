from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q

from accounts.work_handoff import automatic_handoff


class Command(BaseCommand):
    help='Retry pending work handoff for disabled/expired users using explicitly configured backups.'

    def handle(self,*args,**options):
        users=get_user_model().objects.filter(Q(is_active=False)|Q(profile__access_expires_at__lte=timezone.now())).distinct()
        moved=blocked=0
        for user in users.iterator():
            run=automatic_handoff(user.pk)
            if run:
                moved+=run.transferred
                blocked+=run.blocked
        self.stdout.write(self.style.SUCCESS(f'Handoff receipts reviewed: {moved} transferred item(s); {blocked} item(s) need attention. Unchanged attempts reuse their receipts.'))
