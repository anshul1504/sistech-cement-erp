"""Dealer ownership, statement accuracy, export safety and closed-access regressions."""
from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import DealerLedgerAccess, Partner, Order, Receipt, AuditEvent
from .test_projects import seed_projects
from .test_schemes import invoice_fixture


@override_settings(EMAIL_OTP_ENABLED=False)
class DealerPortalTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.customer.kind = 'Dealer'
        self.customer.save()
        self.user = User.objects.create_user('portal-owner', email='dealer@example.test')
        self.access = DealerLedgerAccess.objects.create(user=self.user, partner=self.customer)
        self.client.force_login(self.user)
        self.invoice = invoice_fixture(self, [(self.bag, Decimal('1000'))])
        self.foreign = Partner.objects.create(code='FOREIGN', name='Foreign Private Dealer', kind='Dealer')
        self.receipt = Receipt.objects.create(number='OWN-RC', partner=self.customer, amount=200, created_by=self.maker)
        Receipt.objects.create(number='PRIVATE-RC', partner=self.foreign, amount=999, created_by=self.maker)

    def test_dashboard_owns_sources_and_login_lands_here(self):
        Order.objects.create(number='OWN-CONFIRMED', site=self.site, partner=self.customer, kind='Sale', status='Approved', created_by=self.maker)
        Order.objects.create(number='OWN-DRAFT', site=self.site, partner=self.customer, kind='Sale', created_by=self.maker)
        Order.objects.create(number='FOREIGN-CONFIRMED', site=self.site, partner=self.foreign, kind='Sale', status='Approved', created_by=self.maker)
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('dealer_dashboard'))
        response = self.client.get(reverse('dealer_dashboard'), {'partner': self.foreign.pk})
        self.assertContains(response, self.invoice.number)
        self.assertContains(response, 'OWN-CONFIRMED')
        self.assertContains(response, 'OWN-RC')
        for private in ['PRIVATE-RC', 'FOREIGN-CONFIRMED', 'OWN-DRAFT', self.foreign.name]:
            self.assertNotContains(response, private)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(self.client.get(reverse('dealer_invoices')), self.invoice.number)
        self.assertEqual(self.client.get(reverse('invoice_detail', args=[self.invoice.pk])).status_code, 403)

    def test_statement_pdf_uses_same_filtered_rows_and_audits(self):
        params = {'from': str(timezone.localdate()), 'to': str(timezone.localdate())}
        page = self.client.get(reverse('my_ledger'), params)
        with patch('erp.pdf.render', return_value=b'%PDF-1.4 QA') as render:
            response = self.client.get(reverse('my_ledger'), {**params, 'export': 'pdf', 'partner': self.foreign.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment;', response['Content-Disposition'])
        spec = render.call_args.args[0]
        self.assertEqual(spec['number'], self.customer.code)
        self.assertEqual(spec['sections'][0]['total_row'][-1], f'{page.context["closing"]:,.2f}')
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action='PDF downloaded', reference=f'partner:{self.customer.pk}').exists())
        real_pdf = self.client.get(reverse('my_ledger'), {**params, 'export': 'pdf'})
        self.assertTrue(real_pdf.content.startswith(b'%PDF'))

    def test_invalid_range_never_falls_back_to_all_history(self):
        for params in [{'from': 'bad'}, {'to': '2026-02-30'}, {'from': '2026-12-01', 'to': '2026-01-01'}]:
            for export in ['', 'csv', 'pdf']:
                with patch('erp.finance_views.ledger_rows') as rows:
                    response = self.client.get(reverse('my_ledger'), {**params, 'export': export})
                self.assertEqual(response.status_code, 400)
                rows.assert_not_called()

    def test_csv_formula_safe_private_and_audited(self):
        self.receipt.status = 'bounced'
        self.receipt.bounced_on = timezone.localdate()
        self.receipt.bounce_reason = '=HYPERLINK("https://invalid.test")'
        self.receipt.save()
        response = self.client.get(reverse('my_ledger'), {'export': 'csv'})
        self.assertContains(response, "'=HYPERLINK")
        self.assertIn('no-store', response['Cache-Control'])
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action='Ledger CSV downloaded').exists())

    def test_revoked_and_wrong_kind_accounts_are_denied_everywhere(self):
        for field, value in [('active', False), ('kind', 'Supplier')]:
            self.customer.active, self.customer.kind = True, 'Dealer'
            setattr(self.customer, field, value)
            self.customer.save()
            for route in ['dealer_dashboard', 'dealer_invoices', 'my_ledger']:
                self.assertEqual(self.client.get(reverse(route), {'export': 'pdf'}).status_code, 404)
