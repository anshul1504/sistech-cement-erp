"""PDF documents and reports: render for permitted users, deny others, audit every download."""
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import pdf
from .models import AuditEvent, Order, OrderLine, JournalEntry
from .test_schemes import invoice_fixture, credit_fixture, receipt_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class PdfTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.inv = invoice_fixture(self, [(self.bag, D(1000))])
        self.cn = credit_fixture(self, self.inv, 100, self.inv.lines.get())
        self.rc = receipt_fixture(self, self.inv, D(500))
        self.client.force_login(self.checker)

    def get(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        self.assertEqual(r['Content-Type'], 'application/pdf', url)
        self.assertTrue(r.content.startswith(b'%PDF'), url)
        return r

    def test_documents_and_reports(self):
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.customer, created_by=self.maker)
        OrderLine.objects.create(order=o, product=self.bag, quantity=10, rate=350, gst_rate=18)
        for kind, pk in [('invoice', self.inv.pk), ('credit-note', self.cn.pk), ('receipt', self.rc.pk), ('order', o.pk), ('statement', self.customer.pk)]:
            self.get(reverse('document_pdf', args=[kind, pk]))
        self.assertIn('attachment', self.get(reverse('document_pdf', args=['invoice', self.inv.pk]) + '?download=1&copy=Duplicate+for+transporter')['Content-Disposition'])
        for kind in ['trial-balance', 'profit-loss', 'balance-sheet', 'outstanding']:
            self.get(reverse('report_pdf', args=[kind]))
        self.assertTrue(AuditEvent.objects.filter(action='PDF downloaded', reference=f'invoice:{self.inv.pk}').exists())
        self.assertEqual(self.client.get(reverse('document_pdf', args=['nope', 1])).status_code, 404)
        for name in ['invoice_detail', 'order_detail']:
            pk = self.inv.pk if name == 'invoice_detail' else o.pk
            self.assertContains(self.client.get(reverse(name, args=[pk])), 'View PDF')

    def test_scope_and_permissions(self):
        self.client.force_login(self.outsider)          # other plant
        self.assertEqual(self.client.get(reverse('document_pdf', args=['invoice', self.inv.pk])).status_code, 404)
        nobody = User.objects.create_user('qa-nobody')
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse('document_pdf', args=['invoice', self.inv.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('report_pdf', args=['trial-balance'])).status_code, 403)

    def test_long_table_paginates(self):
        rows = [[i, f'Item {i}', '1', '2'] for i in range(200)]
        data = pdf.render({'title': 'Test', 'sections': [{'headers': ['#', 'Item', 'A', 'B'], 'rows': rows, 'numeric': {2, 3}}]})
        self.assertGreater(data.count(b'/Type /Page\n') + data.count(b'/Type /Page>>') + data.count(b'/Type /Page '), 1)
