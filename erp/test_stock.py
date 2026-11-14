from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import stock as ST
from .models import (Site, Product, Partner, Order, OrderLine, StockBalance, StockMovement, StockTransfer, DamageReport, StockCount, Organization)
from .services import move_stock, transition_order

D = Decimal


def bal(site, product, status='available'):
    return StockBalance.objects.filter(site=site, product=product, status=status).values_list('quantity', flat=True).first() or D('0')


@override_settings(EMAIL_OTP_ENABLED=False)
class StockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'default_bag_weight': 50})
        cls.plant = Site.objects.create(code='PLT', name='Plant', kind='Plant', state='23')
        cls.depot = Site.objects.create(code='DEP', name='Depot', kind='Depot', state='23')
        cls.opc = Product.objects.create(code='OPC', name='OPC 53', category='Cement', unit='BAG', bag_weight_kg=50, gst_rate=28, reorder_level=100, max_level=5000)
        cls.a = User.objects.create_superuser('st-a', 'a@example.com', 'Original-Password-816!')
        cls.b = User.objects.create_superuser('st-b', 'b@example.com', 'Original-Password-816!')
        cls.c = User.objects.create_superuser('st-c', 'c@example.com', 'Original-Password-816!')

    def test_weighted_average_cost(self):
        move_stock(self.a, self.plant, self.opc, D(100), 'o1', 'Opening', unit_cost=D(300))
        move_stock(self.a, self.plant, self.opc, D(50), 'r1', 'Receipt', unit_cost=D(360))
        b = StockBalance.objects.get(site=self.plant, product=self.opc)
        self.assertEqual((b.quantity, b.avg_cost, b.value), (D(150), D('320.0000'), D('48000.00')))
        out = move_stock(self.a, self.plant, self.opc, D(-30), 'd1', 'Dispatch')
        self.assertEqual((out.unit_cost, out.balance_after, out.uom), (D('320.0000'), D(120), 'BAG'))
        move_stock(self.a, self.plant, self.opc, D(10), 'p1', 'Production')  # no cost → average unchanged
        self.assertEqual(StockBalance.objects.get(site=self.plant, product=self.opc).avg_cost, D('320.0000'))
        with self.assertRaisesMessage(ValidationError, 'Insufficient'):
            move_stock(self.a, self.plant, self.opc, D(-500), 'd2', 'Dispatch')
        self.assertEqual(ST.mt_factor(self.opc), D('0.05'))

    def test_damage_report_and_disposition(self):
        move_stock(self.a, self.plant, self.opc, D(200), 'o1', 'Opening', unit_cost=D(300))
        with self.assertRaisesMessage(ValidationError, 'Say where'):
            ST.report_damage(self.a, self.plant, self.opc, 10, 'wet', '', 'Ramesh')
        d = ST.report_damage(self.a, self.plant, self.opc, 10, 'wet', 'Godown 2 stack B', 'Ramesh (store)', 'Roof leak')
        self.assertEqual((bal(self.plant, self.opc), bal(self.plant, self.opc, 'damaged'), d.value), (D(190), D(10), D('3000.00')))
        with self.assertRaisesMessage(ValidationError, 'cannot decide'):
            ST.decide_damage(self.a, d.pk, 4, 6, 'x')
        with self.assertRaisesMessage(ValidationError, 'must equal'):
            ST.decide_damage(self.b, d.pk, 4, 5, 'x')
        ST.decide_damage(self.b, d.pk, 4, 6, 'Four re-packed, six to crusher')
        self.assertEqual((bal(self.plant, self.opc), bal(self.plant, self.opc, 'damaged')), (D(194), D(0)))
        self.assertTrue(StockMovement.objects.filter(kind='Scrap', quantity=D(-6), status='damaged').exists())

    def test_transfer_with_transit_and_discrepancy(self):
        move_stock(self.a, self.plant, self.opc, D(100), 'o1', 'Opening', unit_cost=D(300))
        t = ST.request_transfer(self.a, self.plant, self.depot, 'Festive demand at Rewa', [(self.opc, 50)])
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            ST.decide_transfer(self.a, t.pk, True)
        ST.decide_transfer(self.b, t.pk, True)
        line = t.lines.get()
        with self.assertRaisesMessage(ValidationError, 'approved 50'):
            ST.dispatch_transfer(self.a, t.pk, {line.pk: 60}, 'MP04GA1234')
        ST.dispatch_transfer(self.a, t.pk, {line.pk: 40}, 'MP04 GA 1234', eway='123456789012')
        self.assertEqual(bal(self.plant, self.opc), D(60))
        rows = {(r['site'].code, r['product'].code): r for r in ST.position([self.plant, self.depot])}
        self.assertEqual(rows[('DEP', 'OPC')]['transit'], D(40))
        with self.assertRaisesMessage(ValidationError, 'dispatched cannot'):
            ST.receive_transfer(self.a, t.pk, {line.pk: 40}, {})
        with self.assertRaisesMessage(ValidationError, 'cannot be more'):
            ST.receive_transfer(self.b, t.pk, {line.pk: 39}, {line.pk: 2})
        t = ST.receive_transfer(self.b, t.pk, {line.pk: 35}, {line.pk: 3})
        self.assertEqual(t.status, 'discrepancy')
        self.assertEqual((bal(self.depot, self.opc), bal(self.depot, self.opc, 'damaged')), (D(35), D(3)))
        self.assertEqual(StockBalance.objects.get(site=self.depot, product=self.opc, status='available').avg_cost, D('300.0000'))
        self.assertEqual(DamageReport.objects.get(site=self.depot).quantity, D(3))  # transit damage needs a disposition too
        self.assertEqual(ST.transit_loss(t), D('600.00'))  # 2 bags short
        with self.assertRaisesMessage(ValidationError, 'not involved'):
            ST.resolve_transfer(self.b, t.pk, 'ok')
        ST.resolve_transfer(self.c, t.pk, 'Two bags recovered from transporter bill')
        self.assertEqual(StockTransfer.objects.get(pk=t.pk).status, 'received')

    def test_position_reserved_and_count(self):
        move_stock(self.a, self.plant, self.opc, D(100), 'o1', 'Opening', unit_cost=D(300))
        dealer = Partner.objects.create(code='D1', name='Dealer', kind='Dealer', state='23', credit_limit=10 ** 8)
        o = Order.objects.create(kind='Sale', site=self.plant, partner=dealer, created_by=self.a)
        OrderLine.objects.create(order=o, product=self.opc, quantity=120, rate=350, gst_rate=28)
        transition_order(self.a, o.pk, 'submit'); transition_order(self.b, o.pk, 'approve')
        r = ST.position([self.plant])[0]
        self.assertEqual((r['available'], r['reserved'], r['free'], r['mt']), (D(100), D(120), D(-20), D('5.000')))
        c = ST.start_count(self.a, self.plant)
        with self.assertRaisesMessage(ValidationError, 'already open'):
            ST.start_count(self.a, self.plant, 'Cement')
        line = c.lines.get()
        move_stock(self.a, self.plant, self.opc, D(-10), 'd1', 'Dispatch')  # sale during the count
        with self.assertRaisesMessage(ValidationError, 'Enter a count'):
            ST.save_count(self.a, c.pk, {line.pk: None}, {}, submit=True)
        ST.save_count(self.a, c.pk, {line.pk: D(97)}, {line.pk: 'Torn bags'}, submit=True)
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            ST.review_count(self.a, c.pk, 'approve', note='ok')
        ST.review_count(self.b, c.pk, 'recount', [line.pk])
        ST.save_count(self.a, c.pk, {line.pk: D(98)}, {}, submit=True)
        with self.assertRaisesMessage(ValidationError, 'Record why'):
            ST.review_count(self.b, c.pk, 'approve')
        ST.review_count(self.b, c.pk, 'approve', note='Bag breakage in stacking')
        self.assertEqual(bal(self.plant, self.opc), D(88))  # 100 − 10 sold − 2 variance against the frozen 100
        self.assertEqual(StockCount.objects.get(pk=c.pk).status, 'posted')
        flags = ST.analysis([self.plant])[0]
        self.assertFalse(flags['slow'])

    def test_screens(self):
        move_stock(self.a, self.plant, self.opc, D(100), 'o1', 'Opening', unit_cost=D(300))
        self.client.force_login(self.a)
        for url in [reverse('inventory'), reverse('stock_card', args=[self.plant.pk, self.opc.pk]), reverse('transfers'), reverse('transfer_new'),
                    reverse('damages'), reverse('damage_new'), reverse('counts'), reverse('stock_analysis'), reverse('inventory') + '?show=low']:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('transfer_new'), {'from_site': self.plant.pk, 'to_site': self.depot.pk, 'reason': 'Depot stock', 'product_0': self.opc.pk, 'qty_0': '20'})
        t = StockTransfer.objects.get()
        self.assertRedirects(r, reverse('transfer_detail', args=[t.pk]))
        r = self.client.post(reverse('counts'), {'site': self.plant.pk, 'blind': 'on'})
        c = StockCount.objects.get()
        self.assertRedirects(r, reverse('count_detail', args=[c.pk]))
        self.assertNotContains(self.client.get(reverse('count_detail', args=[c.pk])), '>100<')  # blind: system qty hidden
        r = self.client.post(reverse('damage_new'), {'site': self.plant.pk, 'product': self.opc.pk, 'quantity': '5', 'reason': 'torn', 'location': 'Bay 3', 'owner': 'Loader'})
        self.assertRedirects(r, reverse('damage_detail', args=[DamageReport.objects.get().pk]))

    def test_unvalued_stock_takes_first_cost(self):
        move_stock(self.a, self.depot, self.opc, D(100), 'legacy', 'Opening')  # before costing began
        move_stock(self.a, self.depot, self.opc, D(50), 'r1', 'Receipt', unit_cost=D(300))
        self.assertEqual(StockBalance.objects.get(site=self.depot, product=self.opc).avg_cost, D('300'))
        from .templatetags.erp_format import rupees
        self.assertEqual((rupees(D('-211.11')), rupees(D('1200'))), ('−₹211', '₹1,200'))
