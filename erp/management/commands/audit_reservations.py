"""Read-only rollout audit; never invent legacy allocations or write live stock."""
from django.core.management.base import BaseCommand
from erp.models import OrderLine,StockBalance
from erp.reservations import reserved

class Command(BaseCommand):
    help='Report historical open sales lines without firm allocation tracking and overcommitted balances; no writes.'
    def handle(self,*args,**options):
        legacy=OrderLine.objects.filter(order__kind='Sale',order__status__in=['Approved','Part dispatched','On hold'],stock_reservation__isnull=True)
        breaches=sum(reserved(row.site,row.product)>row.quantity for row in StockBalance.objects.filter(status='available').select_related('site','product'))
        self.stdout.write(f'Untracked open sales lines: {legacy.count()}; overcommitted physical balances: {breaches}.')
        self.stdout.write('No reservations created or stock changed. Review and allocate historical orders before treating them as firm commitments.')
