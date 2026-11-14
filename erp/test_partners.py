from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User, Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Partner, PartnerDocument, Order, OrderLine, Product, Site, AuditEvent

BASE = {'kind': '', 'code': '', 'name': '', 'territory': '', 'active': 'on', 'contact_person': '', 'phone': '', 'alt_phone': '', 'email': '',
        'gstin': '', 'pan': '', 'address': '', 'city': '', 'district': '', 'state': '', 'pincode': '', 'shipping_address': '',
        'credit_limit': '0', 'payment_terms_days': '0', 'security_deposit': '0', 'bank_name': '', 'bank_account_number': '', 'bank_ifsc': '', 'notes': ''}


@override_settings(EMAIL_OTP_ENABLED=False)
class PartnerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('pt-admin', 'pt.admin@example.com', 'Original-Password-816!')
        cls.viewer = User.objects.create_user('pt-view', 'pt.view@example.com', 'Original-Password-816!')
        g = Group.objects.create(name='Partner viewer'); g.permissions.add(Permission.objects.get(codename='view_partner'))
        cls.viewer.groups.add(g)
        cls.site = Site.objects.create(code='P1', name='Plant', kind='Plant')
        cls.product = Product.objects.create(code='C1', name='OPC', category='Cement', grade='OPC 53', unit='BAG', bag_weight_kg=50)

    def setUp(self):
        self.client.force_login(self.admin)

    def post(self, url, **data):
        return self.client.post(url, {**BASE, **data})

    def test_create_vendor_pan_from_gstin_and_detail(self):
        r = self.post(reverse('partners_new'), kind='Supplier', code='sup-bpl', name='Bhopal Supplier', state='23', gstin='23abcde1234f1z5',
                      payment_terms_days='30', phone='+91 98765 43210')
        p = Partner.objects.get(code='SUP-BPL')
        self.assertRedirects(r, reverse('partner_detail', args=[p.pk]))
        self.assertEqual((p.pan, p.gstin), ('ABCDE1234F', '23ABCDE1234F1Z5'))
        self.assertContains(self.client.get(reverse('partner_detail', args=[p.pk])), 'Bhopal Supplier')

    def test_validation(self):
        r = self.post(reverse('partners_new'), kind='Supplier', code='D1', name='D', state='27', gstin='23ABCDE1234F1Z5', pan='ZZZZZ9999Z',
                      pincode='99', bank_ifsc='bad', phone='12')
        for msg in ['must match the PAN', 'state code is 27', '6-digit PIN', 'valid 11-character IFSC', 'valid phone number']:
            self.assertContains(r, msg)

    def test_credit_position_and_sales_partner_types(self):
        p = Partner.objects.create(code='R1', name='Retail Co', kind='Retailer', credit_limit=Decimal('10000'))
        order = Order.objects.create(site=self.site, partner=p, kind='Sale', created_by=self.admin, status='Approved')
        OrderLine.objects.create(order=order, product=self.product, quantity=20, rate=450)
        page = self.client.get(reverse('partner_detail', args=[p.pk]))
        self.assertEqual((page.context['credit']['exposure'], page.context['credit']['used_pct'], page.context['credit']['state']), (Decimal('9000'), 90, 'warn'))
        form = self.client.get(reverse('order_new', args=['Sale'])).context['form']
        self.assertIn(p, form.fields['partner'].queryset)
        supplier = Partner.objects.create(code='S1', name='Gypsum Supplier', kind='Supplier')
        self.assertNotIn(supplier, form.fields['partner'].queryset)

    def test_kyc_documents_and_permissions(self):
        p = Partner.objects.create(code='T1', name='Trans Co', kind='Transporter')
        r = self.client.post(reverse('partner_document_new', args=[p.pk]), {'doc_type': 'vehicle_rc', 'number': 'MP04AB1234',
                             'valid_until': (timezone.localdate() + timedelta(days=5)).isoformat(),
                             'upload': SimpleUploadedFile('rc.pdf', b'%PDF-1.4 x', 'application/pdf')})
        self.assertRedirects(r, reverse('partner_detail', args=[p.pk]))
        doc = PartnerDocument.objects.get()
        self.assertEqual(doc.status[0], 'expiring')
        self.assertEqual(self.client.get(reverse('partner_document_file', args=[p.pk, doc.pk]))['Content-Type'], 'application/pdf')
        bad = self.client.post(reverse('partner_document_new', args=[p.pk]), {'doc_type': 'other', 'upload': SimpleUploadedFile('x.html', b'<html>', 'text/html')})
        self.assertContains(bad, 'Upload a PDF, PNG, JPG or WebP file')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('partner_document_file', args=[p.pk, doc.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('partner_document_new', args=[p.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('partner_document_delete', args=[p.pk, doc.pk])).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post(reverse('partner_document_delete', args=[p.pk, doc.pk]))
        self.assertFalse(PartnerDocument.objects.exists())

    def test_status_delete_and_type_lock(self):
        used = Partner.objects.create(code='U1', name='Used Dealer', kind='Dealer', credit_limit=1)
        Order.objects.create(site=self.site, partner=used, kind='Sale', created_by=self.admin)
        self.assertTrue(self.client.get(reverse('partners_edit', args=[used.pk])).context['form'].fields['kind'].disabled)
        self.client.post(reverse('partner_status', args=[used.pk]))
        used.refresh_from_db(); self.assertFalse(used.active)
        self.client.post(reverse('partner_delete', args=[used.pk]))
        self.assertTrue(Partner.objects.filter(pk=used.pk).exists())
        empty = Partner.objects.create(code='E1', name='Empty', kind='Supplier')
        self.assertRedirects(self.client.post(reverse('partner_delete', args=[empty.pk])), reverse('partners'))
        self.assertTrue(AuditEvent.objects.filter(action='Partner deleted').exists())

    def test_list_groups_and_search(self):
        Partner.objects.create(code='D9', name='Sharma Traders', kind='Dealer', city='Rewa')
        Partner.objects.create(code='S9', name='Coal Supplier', kind='Supplier')
        page = self.client.get(reverse('partners') + '?group=customers')
        self.assertEqual([p.code for p in page.context['partners']], ['D9'])
        self.assertEqual([p.code for p in self.client.get(reverse('partners') + '?q=rewa').context['partners']], ['D9'])
