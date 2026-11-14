from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import sourcing as SC
from .models import Site, Product, Partner, Order, OrderLine, SupplyContract, FreightRate, Organization
from .services import transition_order

D = Decimal
TODAY = timezone.localdate


@override_settings(EMAIL_OTP_ENABLED=False)
class SourcingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_sistech', verbosity=0)
        cls.a = User.objects.create_superuser('sc-a', 'a@example.com', 'Original-Password-816!')
        cls.b = User.objects.create_superuser('sc-b', 'b@example.com', 'Original-Password-816!')
        cls.opc = Product.objects.get(code='SIS-OPC53-50')
        cls.rajat, cls.pioneer = Site.objects.get(code='PLT-RJT-DHR'), Site.objects.get(code='PLT-PIO-DHR')
        cls.dealer = Partner.objects.create(code='D1', name='Indore Traders', kind='Dealer', state='23', district='Indore', credit_limit=10 ** 8)

    def approved(self, site, rate, start=None):
        c = SC.save_contract(self.a, site.operated_by, site, self.opc, D(rate), start or TODAY() - timedelta(days=10), monthly_quantity=D(20000))
        return SC.decide_contract(self.b, c.pk, True)

    def test_seed_profile_is_safe_to_rerun(self):
        org = Organization.current()
        self.assertEqual((org.city, org.state, org.trade_name), ('Indore', '23', 'SISTECH'))
        self.assertEqual(Site.objects.filter(operated_by__manufacturing_partner=True).count(), 6)
        org.phone = '0731000000'; org.save()
        call_command('seed_sistech', verbosity=0)
        self.assertEqual((Organization.current().phone, Partner.objects.filter(manufacturing_partner=True).count()), ('0731000000', 6))

    def test_contract_rules_and_po_controls(self):
        with self.assertRaisesMessage(ValidationError, 'operated by'):
            SC.save_contract(self.a, self.pioneer.operated_by, self.rajat, self.opc, D(250), TODAY())
        c1 = self.approved(self.rajat, 260)
        c2 = SC.save_contract(self.a, self.rajat.operated_by, self.rajat, self.opc, D(255), TODAY())
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            SC.decide_contract(self.a, c2.pk, True)
        SC.decide_contract(self.b, c2.pk, True)
        c1.refresh_from_db()
        self.assertEqual((c1.valid_to, SC.current(self.rajat, self.opc)), (TODAY() - timedelta(days=1), c2))
        # PO to the plant: wrong partner and above-contract rate are refused
        other = Partner.objects.get(code='MFG-PIONEER')
        po = Order.objects.create(kind='Purchase', site=self.rajat, partner=other, created_by=self.a)
        OrderLine.objects.create(order=po, product=self.opc, quantity=1000, rate=270, gst_rate=18)
        with self.assertRaises(ValidationError) as e:
            transition_order(self.a, po.pk, 'submit')
        self.assertTrue(any('above the contract' in m for m in e.exception.messages) and any('operated by' in m for m in e.exception.messages))
        # The order form fills the contract rate when the rate is left empty
        self.client.force_login(self.a)
        r = self.client.post(reverse('order_new', args=['Purchase']), {'site': self.rajat.pk, 'partner': self.rajat.operated_by.pk, 'due_date': TODAY().isoformat(),
                                                                      'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0', 'lines-MIN_NUM_FORMS': '1', 'lines-MAX_NUM_FORMS': '50',
                                                                      'lines-0-product': self.opc.pk, 'lines-0-quantity': '500', 'lines-0-rate': '', 'receipt_tolerance_pct': '0',
                                                                      'advance_amount': '0'})
        self.assertEqual(r.status_code, 302, r.content[:400])
        self.assertEqual(Order.objects.filter(kind='Purchase', partner=self.rajat.operated_by).get().lines.get().rate, D('255.00'))

    def test_advisor_ranks_landed_cost_and_screens(self):
        self.approved(self.rajat, 260)
        self.approved(self.pioneer, 250)
        for site, rate in ((self.rajat, 300), (self.pioneer, 700)):  # per MT freight to Indore
            FreightRate.objects.create(origin=site, destination='Indore', basis='per_mt', rate=rate, valid_from=TODAY() - timedelta(days=5))
        opts = SC.options(self.opc, 400, 'indore')
        # 50 kg bag = 0.05 MT: Rajat 260 + 15 = 275 beats Pioneer 250 + 35 = 285 although Pioneer's rate is lower
        self.assertEqual([(o['site'], o['landed']) for o in opts[:2]], [(self.rajat, D('275.00')), (self.pioneer, D('285.00'))])
        self.assertTrue(opts[0].get('best'))
        sc = SC.scorecard(self.rajat.operated_by)
        self.assertEqual(sc['rows'][0]['contracts'][0].rate, D('260.00'))
        self.client.force_login(self.a)
        for url in [reverse('mfg_partners'), reverse('contract_new'), reverse('sourcing_advisor') + f'?product={self.opc.pk}&qty=400&dealer={self.dealer.pk}']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
