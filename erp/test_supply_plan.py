"""Supply plan: demand from open orders vs 30-day sales, stock / on-order netting, partner source and draft PR creation."""
from decimal import Decimal as D

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import planning
from .models import Order, OrderLine, PurchaseRequest, SupplyContract
from .services import move_stock
from .test_schemes import invoice_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class SupplyPlanTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        move_stock(self.maker, self.site, self.bag, D(100), 'opening:plan', 'Opening', unit_cost=D(300))
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.customer, created_by=self.maker, status='Approved')
        OrderLine.objects.create(order=o, product=self.bag, quantity=500, rate=350, gst_rate=18)
        self.supplier.manufacturing_partner = True; self.supplier.save()
        SupplyContract.objects.create(partner=self.supplier, site=self.site, product=self.bag, rate=D(250), monthly_quantity=1000, valid_from=self.today,
                                      status='approved', created_by=self.maker)

    def test_plan_and_raise_requests(self):
        row = next(r for r in planning.plan([self.site], 7) if r['product'] == self.bag)
        self.assertEqual((row['open'], row['stock'], row['demand'], row['short'], row['source']), (D(500), D(100), D(500), D(400), 'partner'))
        inv = invoice_fixture(self, [(self.bag, D(1000))])
        from .models import InvoiceLine
        InvoiceLine.objects.filter(invoice=inv).update(quantity=3000)          # 100/day × 7 = 700 > open orders
        row = next(r for r in planning.plan([self.site], 7) if r['product'] == self.bag)
        self.assertEqual((row['daily'], row['demand'], row['short']), (D('100.000'), D('700.000'), D('600.000')))
        self.client.force_login(self.checker)
        self.assertEqual(self.client.get(reverse('supply_plan') + '?days=14').status_code, 200)
        key = f'{self.site.pk}:{self.bag.pk}'
        r = self.client.post(reverse('supply_plan'), {'days': '7', 'pick_' + key: 'on', 'qty_' + key: '600'})
        pr = PurchaseRequest.objects.get()
        self.assertEqual((pr.source, pr.lines.get().quantity, pr.lines.get().estimated_rate), ('production', D(600), D(250)))
        row = next(r for r in planning.plan([self.site], 7) if r['product'] == self.bag)
        self.assertEqual(row['short'], D(0))                                     # requested quantity now counts as on order
