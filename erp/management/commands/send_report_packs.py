"""Email due Excel report packs. Schedule daily (e.g. 07:30 IST): python manage.py send_report_packs
Daily packs cover yesterday, weekly packs go on Monday for the last 7 days, monthly packs on the 1st for last month.
Each pack is rebuilt with the subscriber's current permissions; a user who lost access gets nothing."""
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.utils import timezone

from erp import report_packs as RP
from erp.models import ReportSubscription, AuditEvent


class Command(BaseCommand):
    help = 'Email report packs that are due today.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true', help='Send every active subscription now, regardless of schedule.')

    def handle(self, *args, dry_run=False, force=False, **opts):
        sent = skipped = failed = 0
        for sub in ReportSubscription.objects.filter(active=True, user__is_active=True).exclude(user__email='').select_related('user'):
            if not force and not RP.due(sub):
                continue
            period = RP.period_for(sub.frequency)
            built = RP.build(sub.user, sub.pack, period)
            if not built:
                skipped += 1
                continue
            if dry_run:
                self.stdout.write(f'{sub.user.username}: {sub.get_pack_display()} {period.start} – {period.end}')
                continue
            name, data = built
            msg = EmailMessage(f'Sistech ERP · {sub.get_pack_display()} · {period.label}',
                               f'Attached: {sub.get_pack_display()} for {period.start:%d %b %Y} – {period.end:%d %b %Y}, limited to your plants and permissions.\n'
                               f'Change or stop this email: {settings.ERP_BASE_URL}/dashboards/report-packs/\n\nDesign & developed by The Webfix · https://thewebfix.in',
                               settings.DEFAULT_FROM_EMAIL, [sub.user.email])
            msg.attach(name, data, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            try:
                msg.send()
            except Exception as e:
                failed += 1
                self.stderr.write(f'{sub.user.username}: {e.__class__.__name__}')
                continue
            sub.last_sent_at = timezone.now()
            sub.save(update_fields=['last_sent_at'])
            sent += 1
        if not dry_run:
            AuditEvent.objects.create(actor=None, action='Report packs sent', reference=str(timezone.localdate()), detail=f'{sent} sent, {skipped} without access, {failed} failed')
            self.stdout.write(self.style.SUCCESS(f'Report packs: {sent} sent, {skipped} without access, {failed} failed.'))
