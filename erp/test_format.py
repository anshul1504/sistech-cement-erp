import datetime
from decimal import Decimal
from django.test import SimpleTestCase
from .templatetags.erp_format import indian, qty, cell


class FormatTests(SimpleTestCase):
    def test_indian_grouping(self):
        self.assertEqual(indian(1200000), '12,00,000')
        self.assertEqual(indian('204000', 2), '2,04,000.00')
        self.assertEqual(indian(-45678901), '-4,56,78,901')
        self.assertEqual(indian(999), '999')

    def test_qty_and_cells(self):
        self.assertEqual(qty(Decimal('2400.000')), '2,400')
        self.assertEqual(qty(Decimal('12.500')), '12.5')
        self.assertEqual(cell(('₹', Decimal('103500'))), '₹1,03,500.00')
        self.assertEqual(cell(datetime.date(2026, 11, 13)), '13 Nov 2026')
        self.assertEqual(cell(None), '—')
        self.assertEqual(cell('Approved'), 'Approved')
