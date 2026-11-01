from django.core.management.base import BaseCommand
from accounts.roles import initialize_roles

class Command(BaseCommand):
    help = 'Create default ERP roles. Existing role edits are preserved unless --reset is explicitly supplied.'
    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Replace permissions of the named default roles with their presets.')
    def handle(self, *args, **options):
        initialize_roles(update=options['reset'])
        self.stdout.write(self.style.SUCCESS('Default roles ready. Assign roles AND sites under Access control.'))
