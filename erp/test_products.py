from decimal import Decimal

from django.contrib.auth.models import User, Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Product, Site, StockBalance, AuditEvent

BASE = {'code': '', 'name': '', 'category': '', 'brand': '', 'active': 'on', 'grade': '', 'standard': '', 'bag_weight_kg': '',
        'bar_diameter_mm': '', 'weight_per_metre_kg': '', 'bar_length_m': '', 'bars_per_bundle': '', 'unit': '', 'reorder_level': '0',
        'hsn': '', 'gst_rate': '', 'description': ''}


@override_settings(EMAIL_OTP_ENABLED=False)
class ProductTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('p-admin', 'p.admin@example.com', 'Original-Password-816!')
        cls.viewer = User.objects.create_user('p-view', 'p.view@example.com', 'Original-Password-816!')
        g = Group.objects.create(name='Product viewer'); g.permissions.add(Permission.objects.get(codename='view_product'))
        cls.viewer.groups.add(g)

    def setUp(self):
        self.client.force_login(self.admin)

    def post(self, url, **data):
        return self.client.post(url, {**BASE, **data})

    def test_tmt_product_defaults_and_rules(self):
        response = self.post(reverse('products_new'), code='tmt-500d-12', name='TMT Fe 500D 12 mm', category='TMT', grade='Fe 500D',
                             bar_diameter_mm='12', bar_length_m='12', bars_per_bundle='5', unit='MT', gst_rate='18', bag_weight_kg='50')
        product = Product.objects.get(code='TMT-500D-12')
        self.assertRedirects(response, reverse('product_detail', args=[product.pk]))
        self.assertEqual((product.weight_per_metre_kg, product.standard, product.hsn, product.bag_weight_kg), (Decimal('0.889'), 'IS 1786:2008', '7214', None))
        self.assertEqual(product.spec_line, 'Fe 500D · 12 mm · 0.889 kg/m')
        self.assertEqual(product.bundle_weight_kg, Decimal('53.34'))

    def test_category_validation(self):
        r = self.post(reverse('products_new'), code='X1', name='Bad TMT', category='TMT', grade='OPC 53', unit='BAG')
        for msg in ['Choose a steel grade', 'Select the bar size', 'not bags']:
            self.assertContains(r, msg)
        r = self.post(reverse('products_new'), code='X2', name='Bad cement', category='Cement', grade='Fe 500', unit='BAG', bag_weight_kg='', hsn='25')
        for msg in ['Choose a cement grade', 'bag weight', 'HSN must be 4, 6 or 8 digits']:
            self.assertContains(r, msg)
        r = self.post(reverse('products_new'), code='CEM-OK', name='OPC 53 50kg', category='Cement', grade='OPC 53', unit='BAG', bag_weight_kg='50')
        cement = Product.objects.get(code='CEM-OK')
        self.assertEqual((cement.standard, cement.hsn), ('IS 269:2015', '2523'))

    def test_list_detail_and_filters(self):
        Product.objects.create(code='RM-1', name='Clinker', category='Raw material', unit='MT')
        tmt = Product.objects.create(code='TMT-1', name='TMT 16', category='TMT', grade='Fe 500D', bar_diameter_mm=16, unit='MT')
        site = Site.objects.create(code='S1', name='Rolling Mill', kind='Plant')
        StockBalance.objects.create(site=site, product=tmt, quantity=Decimal('42.5'))
        page = self.client.get(reverse('products') + '?category=TMT')
        self.assertEqual([p.code for p in page.context['page'].object_list], ['TMT-1'])
        self.assertEqual(page.context['page'].object_list[0].stock, Decimal('42.5'))
        detail = self.client.get(reverse('product_detail', args=[tmt.pk]))
        self.assertContains(detail, 'Rolling Mill')
        self.assertEqual(detail.context['total'], Decimal('42.5'))

    def test_duplicate_status_and_delete_rules(self):
        src = Product.objects.create(code='TMT-A', name='TMT 12', category='TMT', grade='Fe 500D', bar_diameter_mm=12, unit='MT', bar_length_m=12, bars_per_bundle=5)
        form = self.client.get(reverse('product_duplicate', args=[src.pk])).context['form']
        self.assertEqual((form.initial['grade'], form.initial['code']), ('Fe 500D', ''))
        self.client.post(reverse('product_status', args=[src.pk]))
        src.refresh_from_db(); self.assertFalse(src.active)
        site = Site.objects.create(code='S2', name='Depot', kind='Depot')
        StockBalance.objects.create(site=site, product=src, quantity=1)
        self.client.post(reverse('product_delete', args=[src.pk]))
        self.assertTrue(Product.objects.filter(pk=src.pk).exists())  # used, so kept
        unused = Product.objects.create(code='DEL', name='Delete me', category='Spares', unit='PCS')
        self.assertRedirects(self.client.post(reverse('product_delete', args=[unused.pk])), reverse('products'))
        self.assertFalse(Product.objects.filter(pk=unused.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action='Product deleted').exists())

    def test_standard_catalogue_is_idempotent(self):
        self.client.post(reverse('products_catalogue'))
        first = Product.objects.count()
        self.assertEqual(first, 4 + 14)
        self.assertTrue(Product.objects.filter(code='TMT-500D-12', bar_diameter_mm=12, hsn='7214').exists())
        self.client.post(reverse('products_catalogue'))
        self.assertEqual(Product.objects.count(), first)

    def test_viewer_cannot_change(self):
        p = Product.objects.create(code='V1', name='V', category='Spares', unit='PCS')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('product_detail', args=[p.pk])).status_code, 200)
        for url in [reverse('products_new'), reverse('products_edit', args=[p.pk]), reverse('product_duplicate', args=[p.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403)
        for url in [reverse('product_status', args=[p.pk]), reverse('product_delete', args=[p.pk]), reverse('products_catalogue')]:
            self.assertEqual(self.client.post(url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('products')), 'Add standard catalogue')

    def test_inactive_products_hidden_from_new_orders_and_production(self):
        from .forms import LineForm, ProductionForm
        live = Product.objects.create(code='L1', name='Live', category='Cement', grade='OPC 53', unit='BAG', bag_weight_kg=50)
        old = Product.objects.create(code='O1', name='Old', category='Cement', grade='OPC 43', unit='BAG', bag_weight_kg=50, active=False)
        raw = Product.objects.create(code='R1', name='Clinker', category='Raw material', unit='MT')
        self.assertEqual(set(LineForm().fields['product'].queryset), {live, raw})
        self.assertEqual(set(ProductionForm().fields['product'].queryset), {live, raw})
