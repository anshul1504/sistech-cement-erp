from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import inbound as IB
from .models import Site, Product, Partner, Order, OrderLine, StockBalance, GoodsReceipt, Weighment, Organization
from .services import transition_order

D = Decimal


def row(received, accepted, rejected=0, damaged=0, hold=0, reason=''):
    return {'received': D(str(received)), 'accepted': D(str(accepted)), 'rejected': D(str(rejected)), 'damaged': D(str(damaged)), 'hold': D(str(hold)), 'reason': reason}


@override_settings(EMAIL_OTP_ENABLED=False)
class InboundTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23'})
        cls.plant = Site.objects.create(code='PLT', name='Plant', kind='Plant', state='23', uses_weighbridge=True, weight_tolerance_pct=D('0.50'), scale_id='WB-1')
        cls.depot = Site.objects.create(code='DEP', name='Depot', kind='Depot', state='23')
        cls.coal = Product.objects.create(code='COAL', name='Coal', category='Fuel', unit='MT', gst_rate=5)
        cls.bags = Product.objects.create(code='BAG', name='HDPE bags', category='Packing', unit='PCS', gst_rate=18)
        cls.supplier = Partner.objects.create(code='S1', name='Coal Co', kind='Supplier', state='23')
        cls.store = User.objects.create_superuser('in-store', 's@example.com', 'Original-Password-816!')
        cls.lab = User.objects.create_superuser('in-lab', 'l@example.com', 'Original-Password-816!')

    def po(self, site, product, qty, rate=9000, tol=2):
        o = Order.objects.create(kind='Purchase', site=site, partner=self.supplier, created_by=self.store, receipt_tolerance_pct=tol)
        OrderLine.objects.create(order=o, product=product, quantity=qty, rate=rate, gst_rate=5)
        transition_order(self.store, o.pk, 'submit')
        transition_order(self.lab, o.pk, 'approve')
        return Order.objects.get(pk=o.pk)

    def truck(self, o, challan, qty, vehicle='MP04GA1001'):
        return IB.gate_in(self.store, o, {'vehicle_number': vehicle, 'challan_number': challan, 'driver_mobile': '98765 43210'}, {o.lines.get(): qty})

    def stock(self, site, product):
        return StockBalance.objects.filter(site=site, product=product).values_list('quantity', flat=True).first() or 0

    def test_weighbridge_receipt_hold_and_release(self):
        o = self.po(self.plant, self.coal, 30)
        with self.assertRaisesMessage(ValidationError, 'Only approved'):
            IB.gate_in(self.store, Order.objects.create(kind='Purchase', site=self.plant, partner=self.supplier, created_by=self.store), {'vehicle_number': 'MP04GA9', 'challan_number': 'X'}, {})
        g = self.truck(o, 'inv-101', 20)
        self.assertEqual((g.status, g.vehicle_number, g.challan_number, g.driver_mobile), ('gate_in', 'MP04GA1001', 'INV-101', '9876543210'))
        with self.assertRaisesMessage(ValidationError, 'already been received'):
            self.truck(o, 'INV-101', 5, vehicle='MP04GA2002')
        with self.assertRaisesMessage(ValidationError, 'already inside'):
            self.truck(o, 'INV-102', 5)
        with self.assertRaisesMessage(ValidationError, 'not possible now'):
            IB.weigh(self.store, g.pk, 'tare', 10000, 'scale')
        with self.assertRaisesMessage(ValidationError, 'needs a reason'):
            IB.weigh(self.store, g.pk, 'gross', 30000)
        IB.weigh(self.store, g.pk, 'gross', 30000, 'scale')
        with self.assertRaisesMessage(ValidationError, 'less than gross'):
            IB.weigh(self.store, g.pk, 'tare', 31000, 'scale')
        IB.weigh(self.store, g.pk, 'tare', 10500, 'scale')
        IB.weigh(self.store, g.pk, 'tare', 10400, 'scale', 'Driver was in the cab')  # reweigh keeps the original
        g = GoodsReceipt.objects.get(pk=g.pk)
        self.assertEqual((g.status, g.net_kg, Weighment.objects.filter(grn=g).count()), ('unloaded', 19600, 3))
        line = g.lines.get()
        self.assertEqual(IB.suggested_received(g)[line.pk], D('19.600'))
        with self.assertRaisesMessage(ValidationError, 'does not match net weight'):
            IB.post(self.store, g.pk, {line: row(20, 20)}, 'x')
        with self.assertRaisesMessage(ValidationError, 'but received is'):
            IB.post(self.store, g.pk, {line: row('19.6', 15, hold=4)}, 'x')
        with self.assertRaisesMessage(ValidationError, 'reason for rejected'):
            IB.post(self.store, g.pk, {line: row('19.6', 15, rejected='0.6', hold=4)}, 'x')
        with self.assertRaisesMessage(ValidationError, 'shortage / excess'):
            IB.post(self.store, g.pk, {line: row('19.6', '15.6', hold=4)})
        IB.post(self.store, g.pk, {line: row('19.6', 15, rejected='0.6', hold=4, reason='Wet coal')}, 'Transit loss 400 kg')
        self.assertEqual(self.stock(self.plant, self.coal), D('15'))  # hold is not stock
        o.refresh_from_db()
        self.assertEqual((o.status, o.lines.get().received_quantity()), ('Part received', D('19')))
        with self.assertRaisesMessage(ValidationError, 'posted the GRN'):
            IB.release(self.store, line.pk, 4, 0, 'ok')
        with self.assertRaisesMessage(ValidationError, 'must equal'):
            IB.release(self.lab, line.pk, 3, 0, 'LR-1')
        IB.release(self.lab, line.pk, 3, 1, 'LR-1: moisture 13% on one heap')
        self.assertEqual(self.stock(self.plant, self.coal), D('18'))
        with self.assertRaisesMessage(ValidationError, 'already been decided'):
            IB.release(self.lab, line.pk, 1, 0, 'again')
        # Second truck: 30 MT + 2% = 30.6 may be taken; 19 already counted (15 + 4 hold) → 11.6 room
        g2 = self.truck(o, 'INV-102', 12, vehicle='MP04GA2002')
        IB.weigh(self.store, g2.pk, 'gross', 22000, 'scale'); IB.weigh(self.store, g2.pk, 'tare', 10000, 'scale')
        l2 = g2.lines.get()
        with self.assertRaisesMessage(ValidationError, 'Reject the excess'):
            IB.post(self.store, g2.pk, {l2: row(12, 12)})
        IB.post(self.store, g2.pk, {l2: row(12, '11.6', rejected='0.4', reason='Above PO quantity')})
        o.refresh_from_db()
        self.assertEqual(o.status, 'Completed')
        self.assertEqual(self.stock(self.plant, self.coal), D('29.6'))

    def test_depot_without_weighbridge_and_short_close(self):
        o = self.po(self.depot, self.bags, 10000, rate=12, tol=0)
        g = self.truck(o, 'B-1', 6000)
        self.assertEqual(g.status, 'unloaded')
        with self.assertRaisesMessage(ValidationError, 'does not use a weighbridge'):
            IB.weigh(self.store, g.pk, 'gross', 5000, 'scale')
        IB.post(self.lab, g.pk, {g.lines.get(): row(6000, 5900, damaged=100, reason='Torn')})
        self.assertEqual(Order.objects.get(pk=o.pk).status, 'Part received')
        with self.assertRaisesMessage(ValidationError, 'short-close'):
            transition_order(self.store, o.pk, 'cancel')
        g2 = self.truck(o, 'B-2', 4000, vehicle='MP04GA3003')
        with self.assertRaisesMessage(ValidationError, 'still inside'):
            transition_order(self.store, o.pk, 'close')
        IB.cancel(self.store, g2.pk, 'Wrong size bags')
        transition_order(self.store, o.pk, 'close')
        self.assertEqual(Order.objects.get(pk=o.pk).status, 'Completed')

    def test_screens(self):
        o = self.po(self.plant, self.coal, 30)
        self.client.force_login(self.store)
        self.assertEqual(self.client.get(reverse('inbound')).status_code, 200)
        self.assertContains(self.client.get(reverse('order_detail', args=[o.pk])), 'Gate-in supplier truck')
        r = self.client.post(reverse('grn_new', args=[o.pk]), {'vehicle_number': 'mp 04 ga 7', 'challan_number': 'C-9', f'qty_{o.lines.get().pk}': '10'})
        g = GoodsReceipt.objects.get()
        self.assertRedirects(r, reverse('grn_detail', args=[g.pk]))
        for kind, kg in [('gross', '20000'), ('tare', '10000')]:
            self.client.post(reverse('grn_action', args=[g.pk, kind]), {'weight_kg': kg, 'source': 'scale'})
        page = self.client.get(reverse('grn_detail', args=[g.pk]))
        self.assertContains(page, 'Post GRN')
        self.assertContains(page, 'value="10"')  # received suggested from net weight
        line = g.lines.get()
        bad = {f'received_{line.pk}': '10', f'accepted_{line.pk}': '9', f'rejected_{line.pk}': '0', f'damaged_{line.pk}': '0', f'hold_{line.pk}': '0', 'variance_reason': 'kept'}
        self.client.post(reverse('grn_action', args=[g.pk, 'post']), bad)
        page = self.client.get(reverse('grn_detail', args=[g.pk]))
        self.assertContains(page, 'value="9"')  # typed values come back after an error
        self.assertContains(page, 'value="kept"')
        self.client.post(reverse('grn_action', args=[g.pk, 'post']), {**bad, f'hold_{line.pk}': '1'})
        self.assertEqual(GoodsReceipt.objects.get().status, 'posted')
        self.assertNotContains(self.client.get(reverse('grn_detail', args=[g.pk])), 'Record decision')  # poster cannot release
        self.assertContains(self.client.get(reverse('grn_print', args=[g.pk])), 'Goods receipt note')
        self.client.force_login(self.lab)
        self.assertContains(self.client.get(reverse('grn_detail', args=[g.pk])), 'Record decision')
        self.client.post(reverse('grn_action', args=[g.pk, 'release']), {'line': line.pk, 'accept': '1', 'reject': '0', 'note': 'LR-7 ok'})
        self.assertEqual(self.stock(self.plant, self.coal), D('10'))
