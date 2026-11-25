"""Secondary sales: dealer and staff entry, retailer must sit under the dealer, CSV import all-or-nothing, sell-through maths."""
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from . import secondary as S
from . import network as net
from .models import DealerLedgerAccess, Partner, SecondarySale
from .test_projects import seed_projects
from .test_schemes import invoice_fixture


@override_settings(EMAIL_OTP_ENABLED=False)
class SecondarySalesTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.customer.kind = 'Dealer'
        self.customer.save()
        self.bag.category = 'Cement'
        self.bag.save()
        self.admin = User.objects.create_superuser('sec-admin', 's@example.test', 'x')
        self.shop = Partner.objects.create(code='RT-SEC', name='Corner Hardware', kind='Retailer')
        net.set_parent(self.admin, self.shop, self.customer)
        self.elsewhere = Partner.objects.create(code='RT-FAR', name='Far Retailer', kind='Retailer')
        self.dealer_user = User.objects.create_user('sec-dealer')
        DealerLedgerAccess.objects.create(user=self.dealer_user, partner=self.customer)

    def data(self, **kw):
        d = dict(date=self.today, product=self.bag, quantity=D('100'), retailer=self.shop, retailer_name='', rate=None, note='')
        d.update(kw)
        return d

    def test_entry_rules(self):
        S.record(self.dealer_user, self.customer, self.data(), 'dealer')
        with self.assertRaises(ValidationError):
            S.record(self.dealer_user, self.customer, self.data(retailer=self.elsewhere), 'dealer')   # not under this dealer
        with self.assertRaises(ValidationError):
            S.record(self.dealer_user, self.customer, self.data(retailer=None), 'dealer')             # neither retailer nor name
        S.record(self.dealer_user, self.customer, self.data(retailer=None, retailer_name='New shop, Kolar'), 'dealer')
        with self.assertRaises(PermissionDenied):
            S.record(User.objects.create_user('nobody'), self.customer, self.data(), 'staff')
        self.assertEqual(SecondarySale.objects.count(), 2)

    def test_sell_through_and_dealer_page(self):
        invoice_fixture(self, [(self.bag, D('1000'))])
        S.record(self.dealer_user, self.customer, self.data(quantity=D('1')), 'dealer')
        report = S.sell_through(self.today.replace(day=1), self.today)
        row = next(r for r in report['rows'] if r['dealer'] == self.customer)
        self.assertGreater(row['primary_mt'], 0)
        self.assertEqual(row['secondary_mt'], row['primary_mt'])        # 1 bag bought (fixture qty 1), 1 bag sold on
        self.assertEqual(row['through'], D('100.0'))
        self.assertEqual(row['retailers'], 1)
        self.client.force_login(self.dealer_user)
        page = self.client.get(reverse('dealer_secondary'))
        self.assertContains(page, 'Corner Hardware')
        self.assertNotContains(page, 'Far Retailer')
        r = self.client.post(reverse('dealer_secondary'), {'retailer': self.shop.pk, 'product': self.bag.pk, 'quantity': '5', 'date': str(self.today)}, follow=True)
        self.assertContains(r, 'saved')
        mine = SecondarySale.objects.latest('pk')
        self.client.post(reverse('dealer_secondary'), {'cancel': mine.pk})
        mine.refresh_from_db()
        self.assertEqual(mine.status, 'cancelled')
        self.assertEqual(self.client.get(reverse('secondary_sales')).status_code, 403)

    def test_import_is_all_or_nothing_and_staff_screen(self):
        good = f'dealer code,retailer,product,qty,date\n{self.customer.code},RT-SEC,{self.bag.code},10,{self.today:%d-%m-%Y}\n{self.customer.code},Village Store,{self.bag.code},5,{self.today}\n'
        self.assertEqual(S.import_csv(self.admin, good), 2)
        bad = good + f'{self.customer.code},RT-SEC,NOPE,1,{self.today}\n'
        with self.assertRaises(ValidationError):
            S.import_csv(self.admin, bad)
        self.assertEqual(SecondarySale.objects.count(), 2)
        self.assertEqual(SecondarySale.objects.filter(retailer=self.shop).count(), 1)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('secondary_sales'))
        self.assertContains(page, 'Village Store')
        self.assertContains(page, 'Sell-through')
