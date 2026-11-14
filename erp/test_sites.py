from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User, Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Site, SiteAccess, Product, StockBalance, Organization, OrganizationDocument, AuditEvent

BASE = {'kind': '', 'facility_type': '', 'code': '', 'name': '', 'active': 'on', 'address': '', 'city': '', 'district': '', 'state': '',
        'pincode': '', 'gstin': '', 'manager': '', 'phone': '', 'email': '', 'capacity_tpa': '', 'notes': ''}


@override_settings(EMAIL_OTP_ENABLED=False)
class SiteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('s-admin', 's.admin@example.com', 'Original-Password-816!')
        cls.ops = User.objects.create_user('s-ops', 's.ops@example.com', 'Original-Password-816!')
        g = Group.objects.create(name='Site keeper')
        g.permissions.add(*Permission.objects.filter(codename__in=['view_site', 'add_site', 'change_site']))
        cls.ops.groups.add(g)
        org = Organization.current(); org.pan = 'ABCDE1234F'; org.save()

    def post(self, url, **data):
        return self.client.post(url, {**BASE, **data})

    def test_create_plant_with_valid_gstin(self):
        self.client.force_login(self.admin)
        r = self.post(reverse('sites_new'), kind='Plant', facility_type='tmt_rolling', code='plt-sat', name='Satna Rolling Mill',
                      city='Satna', state='23', pincode='485001', gstin='23abcde1234f1z5', capacity_tpa='250000', manager=str(self.ops.pk))
        site = Site.objects.get(code='PLT-SAT')
        self.assertRedirects(r, reverse('site_detail', args=[site.pk]))
        self.assertEqual((site.gstin, site.manager, site.full_address), ('23ABCDE1234F1Z5', self.ops, 'Satna, Madhya Pradesh 485001'))

    def test_gstin_state_pan_and_facility_rules(self):
        self.client.force_login(self.admin)
        r = self.post(reverse('sites_new'), kind='Depot', facility_type='cement_grinding', code='D1', name='Depot', state='27', gstin='23ABCDE1234F1Z5', pincode='12')
        for msg in ['cannot be a manufacturing facility', 'state code 27', '6-digit PIN']:
            self.assertContains(r, msg)
        r = self.post(reverse('sites_new'), kind='Plant', code='P9', name='Plant', state='23', gstin='23ZZZZZ9999Z1Z5')
        self.assertContains(r, 'must contain the company PAN ABCDE1234F')

    def test_non_admin_sees_only_assigned_and_keeps_access_to_created(self):
        a = Site.objects.create(code='A', name='Assigned', kind='Plant')
        Site.objects.create(code='B', name='Hidden', kind='Depot')
        SiteAccess.objects.create(user=self.ops, site=a)
        self.client.force_login(self.ops)
        page = self.client.get(reverse('sites'))
        self.assertEqual([r['site'].code for r in page.context['rows']], ['A'])
        hidden = Site.objects.get(code='B')
        self.assertEqual(self.client.get(reverse('site_detail', args=[hidden.pk])).status_code, 404)
        self.post(reverse('sites_new'), kind='Depot', facility_type='depot', code='NEW', name='New depot')
        self.assertTrue(SiteAccess.objects.filter(user=self.ops, site__code='NEW').exists())

    def test_detail_shows_stock_people_and_licences(self):
        site = Site.objects.create(code='P1', name='Cement Works', kind='Plant')
        SiteAccess.objects.create(user=self.ops, site=site)
        product = Product.objects.create(code='C1', name='OPC 53', category='Cement', grade='OPC 53', unit='BAG', bag_weight_kg=50, reorder_level=10)
        StockBalance.objects.create(site=site, product=product, quantity=Decimal('5'))
        OrganizationDocument.objects.create(doc_type='factory_licence', site=site, valid_until=timezone.localdate() - timedelta(days=2),
                                            file=b'%PDF-1.4', file_name='f.pdf', content_type='application/pdf', size=8)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('site_detail', args=[site.pk]))
        for text in ['OPC 53', 's-ops', 'Factory licence', 'Expired', 'Low']:
            self.assertContains(page, text)
        listing = self.client.get(reverse('sites'))
        self.assertEqual(listing.context['rows'][0]['docs'], {'expired': 1, 'expiring': 0})
        new_doc = self.client.get(reverse('organization_document_new') + f'?site={site.pk}')
        self.assertEqual(str(new_doc.context['form'].initial.get('site')), str(site.pk))

    def test_status_and_delete_rules(self):
        used = Site.objects.create(code='U', name='Used', kind='Plant')
        product = Product.objects.create(code='X', name='X', category='Spares', unit='PCS')
        StockBalance.objects.create(site=used, product=product, quantity=1)
        empty = Site.objects.create(code='E', name='Empty', kind='Depot')
        self.client.force_login(self.admin)
        self.client.post(reverse('site_status', args=[used.pk]))
        used.refresh_from_db(); self.assertFalse(used.active)
        self.client.post(reverse('site_delete', args=[used.pk]))
        self.assertTrue(Site.objects.filter(pk=used.pk).exists())
        self.assertRedirects(self.client.post(reverse('site_delete', args=[empty.pk])), reverse('sites'))
        self.assertFalse(Site.objects.filter(pk=empty.pk).exists())
        self.client.force_login(self.ops)
        self.assertEqual(self.client.post(reverse('site_delete', args=[used.pk])).status_code, 403)
