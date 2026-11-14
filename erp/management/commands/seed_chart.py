"""Create the standard chart of accounts (Schedule III groups) used by automatic postings. Existing accounts are kept."""
from django.core.management.base import BaseCommand

from erp.gl import ensure_chart


class Command(BaseCommand):
    help = 'Create the standard chart of accounts.'

    def handle(self, *args, **options):
        made = ensure_chart()
        self.stdout.write(self.style.SUCCESS(f'{made} account(s) created. Ask your accountant to review names and groups.'))
