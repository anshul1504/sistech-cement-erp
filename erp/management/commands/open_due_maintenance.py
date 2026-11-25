"""Open preventive maintenance jobs that are due. Schedule daily: python manage.py open_due_maintenance --user <username>"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from erp.maintenance import open_due


class Command(BaseCommand):
    help = 'Open preventive maintenance jobs for every plan that is due.'

    def add_arguments(self, parser):
        parser.add_argument('--user', required=True, help='Username recorded as the reporter (needs maintenance job permission and site access).')

    def handle(self, *args, user, **opts):
        u = get_user_model().objects.filter(username=user, is_active=True).first()
        if not u:
            raise CommandError('Active user not found.')
        made = open_due(u)
        self.stdout.write(self.style.SUCCESS(f'{len(made)} preventive job(s) opened.'))
