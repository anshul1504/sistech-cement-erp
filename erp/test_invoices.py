from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User, Group, Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .invoice_views import post_invoice
from .models import SiteAccess, Site, Product, Partner, Order, OrderLine, Invoice, Organization, AuditEvent


@override_settings(EMAIL_OTP_ENABLED=False)
class InvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('iv-admin', 'iv@example.com', 'Original-Password-816!')
        Organization.objects.update_or_create(pk=1, defaults={'legal_name': 'Sistech Cement & TMT Pvt. Ltd.', 'state': '23', 'gstin': '23ABCDE1234F1Z5', 'pan': 'ABCDE1234F'})
        cls.site = Site.objects.create(code='PLT-01', name='Plant', kind='Plant', state='23', gstin='23ABCDE1234F2Z4')
        cls.cement = Product.objects.create(code='OPC', name='OPC 53 · 50 kg', category='Cement', unit='BAG', gst_rate=18, hsn='2523')
        cls.local = Partner.objects.create(code='D1', name='Bhopal Dealer', kind='Dealer', state='23', gstin='23AAAAA1111A1Z1', payment_terms_days=15, credit_limit=10 ** 7)
        cls.outside = Partner.objects.create(code='D2', name='Nagpur Dealer', kind='Dealer', state='27', credit_limit=10 ** 7)

    def order(self, partner, status='Completed', qty=100, rate=380):
        o = Order.objects.create(kind='Sale', site=self.site, partner=partner, created_by=self.admin, status=status)
        OrderLine.objects.create(order=o, product=self.cement, quantity=qty, rate=rate, gst_rate=18, hsn='2523')
        return o

    def test_intra_state_invoice_snapshot_and_numbering(self):
        o = self.order(self.local)
        inv = post_invoice(self.admin, o.pk, {'vehicle_number': 'MP04GA1234', 'transporter': '', 'lr_number': '', 'eway_bill': ''})
        self.assertTrue(inv.number.startswith('PLT0/'))
        self.assertLessEqual(len(inv.number), 16)
        self.assertEqual((inv.taxable, inv.cgst, inv.sgst, inv.igst, inv.grand_total), (Decimal('38000.00'), Decimal('3420.00'), Decimal('3420.00'), Decimal('0.00'), Decimal('44840.00')))
        self.assertEqual((inv.seller_gstin, inv.buyer_gstin, inv.place_of_supply, inv.interstate), ('23ABCDE1234F2Z4', '23AAAAA1111A1Z1', '23', False))
        self.assertEqual(inv.due_date, timezone.localdate() + timedelta(days=15))
        # Snapshot survives master changes
        Partner.objects.filter(pk=self.local.pk).update(name='Renamed')
        page = self.client_get(inv)
        self.assertContains(page, 'Bhopal Dealer')
        self.assertContains(page, 'Forty Four Thousand Eight Hundred Forty')
        # Second invoice for the same order is refused; next order gets the next number
        with self.assertRaises(ValidationError):
            post_invoice(self.admin, o.pk, {})
        inv2 = post_invoice(self.admin, self.order(self.local).pk, {})
        self.assertEqual(int(inv2.number[-5:]), int(inv.number[-5:]) + 1)

    def client_get(self, inv):
        self.client.force_login(self.admin)
        return self.client.get(reverse('invoice_detail', args=[inv.pk]))

    def test_inter_state_igst_and_b2c(self):
        inv = post_invoice(self.admin, self.order(self.outside, qty=10, rate=333.33).pk, {})
        self.assertTrue(inv.interstate)
        self.assertEqual((inv.cgst, inv.igst), (Decimal('0.00'), Decimal('599.99')))
        self.assertEqual(inv.grand_total, Decimal('3933'))
        self.assertFalse(inv.is_b2b)

    def test_blockers(self):
        with self.assertRaisesMessage(ValidationError, 'Dispatch and post the order first'):
            post_invoice(self.admin, self.order(self.local, status='Approved').pk, {})
        Site.objects.filter(pk=self.site.pk).update(gstin='')
        Organization.objects.filter(pk=1).update(gstin='')
        with self.assertRaisesMessage(ValidationError, 'Set the GSTIN'):
            post_invoice(self.admin, self.order(self.local).pk, {})

    def test_views_permissions_and_export(self):
        o = self.order(self.local)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('order_detail', args=[o.pk]))
        self.assertContains(page, reverse('invoice_new', args=[o.pk]))
        self.assertContains(self.client.get(reverse('invoices')), o.number)  # ready to invoice
        r = self.client.post(reverse('invoice_new', args=[o.pk]), {'vehicle_number': 'bad', 'eway_bill': '12'})
        self.assertContains(r, 'valid vehicle number')
        self.assertContains(r, '12 digits')
        r = self.client.post(reverse('invoice_new', args=[o.pk]), {'vehicle_number': 'mp 04 ga 1234', 'eway_bill': '1234 5678 9012'})
        inv = Invoice.objects.get()
        self.assertRedirects(r, reverse('invoice_detail', args=[inv.pk]))
        self.assertEqual((inv.vehicle_number, inv.eway_bill), ('MP04GA1234', '123456789012'))
        self.assertTrue(AuditEvent.objects.filter(action='Invoice posted', reference=inv.number).exists())
        self.assertRedirects(self.client.get(reverse('invoice_new', args=[o.pk])), reverse('invoice_detail', args=[inv.pk]))
        csv = self.client.get(reverse('invoices') + '?export=csv').content.decode('utf-8-sig')
        self.assertIn(inv.number, csv)
        self.assertIn('23-Madhya Pradesh', csv)
        viewer = User.objects.create_user('iv-view', 'v@example.com', 'Original-Password-816!')
        g = Group.objects.create(name='Invoice viewer'); g.permissions.add(Permission.objects.get(codename='view_invoice'))
        viewer.groups.add(g)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('invoice_detail', args=[inv.pk])).status_code, 404)  # no plant access → not visible
        SiteAccess.objects.create(user=viewer, site=self.site)
        self.assertEqual(self.client.get(reverse('invoice_detail', args=[inv.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('invoices') + '?export=csv').status_code, 403)
        self.assertEqual(self.client.get(reverse('invoice_new', args=[self.order(self.local).pk])).status_code, 403)
