"""Post documents created before the general ledger existed (or after a failed posting). Safe to run again: one voucher per document."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from erp import gl
from erp.models import Invoice, Receipt, CreditNote, SupplierInvoice, DebitNote, FreightBill, StockMovement


class Command(BaseCommand):
    help = 'Post existing invoices, receipts, credit notes, supplier invoices, debit notes, freight bills and stock movements to the ledger.'

    @transaction.atomic
    def handle(self, *args, **options):
        gl.ensure_chart()
        user = get_user_model().objects.filter(is_superuser=True).order_by('pk').first()
        if user is None:
            raise CommandError('Create a super administrator before backfilling the ledger.')
        counts = {}

        def run(label, items, fn):
            n = 0
            for obj in items:
                with transaction.atomic():
                    if fn(user, obj):
                        n += 1
            counts[label] = n

        run('stock movements', StockMovement.objects.select_related('product', 'site').order_by('pk'), gl.post_stock)
        run('tax invoices', Invoice.objects.select_related('partner', 'site'), gl.post_invoice)
        run('credit notes', CreditNote.objects.select_related('partner', 'invoice__site'), gl.post_credit_note)
        run('receipts', Receipt.objects.select_related('partner'), gl.post_receipt)
        for r in Receipt.objects.filter(status='bounced'):
            gl.reverse_source(user, f'rct:{r.pk}', reason='Cheque bounced')
        run('supplier invoices', SupplierInvoice.objects.filter(status__in=['matched', 'approved']).select_related('supplier', 'site'), gl.post_supplier_invoice)
        run('debit notes', DebitNote.objects.select_related('supplier', 'site'), gl.post_debit_note)
        run('freight bills', FreightBill.objects.filter(status='approved').select_related('transporter'), gl.post_freight_bill)
        self.stdout.write(self.style.SUCCESS('Ledger up to date: ' + ', '.join(f'{v} {k}' for k, v in counts.items()) + ' processed.'))
