"""Daily 'My work' email digest: every active user with an email address gets the approvals and tasks waiting for them.

Schedule once a day (e.g. cron at 08:30 IST):  python manage.py send_reminders
Use --dry-run to see who would receive what without sending. Links use the ERP_BASE_URL setting.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from erp.insights import my_queue
from erp.models import AuditEvent


class Command(BaseCommand):
    help = "Email each user the work waiting for them (approvals, PODs, complaints, CAPA, follow-ups)."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List recipients and counts without sending.')
        parser.add_argument('--user', help='Only this username.')

    def handle(self, *args, dry_run=False, user=None, **opts):
        users = get_user_model().objects.filter(is_active=True).exclude(email='').order_by('username')
        if user:
            users = users.filter(username=user)
        base = settings.ERP_BASE_URL
        sent = failed = 0
        for u in users:
            items = my_queue(u)
            if not items:
                continue
            total = sum(i['count'] for i in items)
            ctx = {'name': u.get_full_name() or u.username, 'items': [dict(i, link=base + i['url']) for i in items], 'total': total,
                   'today': timezone.localdate(), 'home': base + '/', 'my_work': base + '/my-work/'}
            if dry_run:
                self.stdout.write(f'{u.username}: {total} waiting — ' + ', '.join(f"{i['label']} ({i['count']})" for i in items))
                continue
            msg = EmailMultiAlternatives(f'Sistech ERP: {total} item{"s" if total != 1 else ""} waiting for you', render_to_string('emails/reminders.txt', ctx),
                                         settings.DEFAULT_FROM_EMAIL, [u.email])
            msg.attach_alternative(render_to_string('emails/reminders.html', ctx), 'text/html')
            try:
                msg.send()
                sent += 1
            except Exception as e:  # one bad address or SMTP hiccup must not stop the rest
                failed += 1
                self.stderr.write(f'{u.username}: {e.__class__.__name__}')
        if not dry_run:
            AuditEvent.objects.create(actor=None, action='Reminder digest sent', reference=str(timezone.localdate()), detail=f'{sent} sent, {failed} failed')
            self.stdout.write(self.style.SUCCESS(f'Reminder digest: {sent} sent, {failed} failed.'))
