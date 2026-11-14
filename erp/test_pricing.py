from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .gst import summarise, in_words, financial_year, next_number, price_for, line_tax
from .models import Site, SiteAccess, Product, Partner, Order, OrderLine, PriceRule, NumberSeries


class GstMathTests(TestCase):
    def test_line_tax_split_and_rounding(self):
        self.assertEqual(line_tax(Decimal('1000'), 18, False), (Decimal('90.00'), Decimal('90.00'), Decimal('0.00')))
        self.assertEqual(line_tax(Decimal('1000'), 18, True), (Decimal('0.00'), Decimal('0.00'), Decimal('180.00')))
        line = OrderLine(quantity=Decimal('12.5'), rate=Decimal('333.33'), gst_rate=Decimal('28'), hsn='2523')
        t = summarise([line], False)
        self.assertEqual(t['taxable'], Decimal('4166.63'))
        self.assertEqual((t['cgst'], t['sgst']), (Decimal('583.33'), Decimal('583.33')))
        self.assertEqual(t['grand_total'], Decimal('5333'))
        self.assertEqual(t['round_off'], Decimal('-0.29'))
        self.assertEqual(t['hsn'][0]['taxable'], Decimal('4166.63'))

    def test_amount_in_words(self):
        self.assertEqual(in_words(Decimal('1234567.50')), 'Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven and Fifty Paise Only')
        self.assertEqual(in_words(Decimal('105')), 'Rupees One Hundred Five Only')
        self.assertEqual(in_words(Decimal('30000000')), 'Rupees Three Crore Only')

    def test_financial_year_and_series(self):
        self.assertEqual(financial_year(date(2026, 3, 31)), '2025-26')
        self.assertEqual(financial_year(date(2026, 4, 1)), '2026-27')
        site = Site.objects.create(code='BPL-PLANT', name='Bhopal', kind='Plant')
        self.assertEqual(next_number('INV', site, date(2026, 11, 1)), 'BPLP/26-27/00001')
        self.assertEqual(next_number('INV', site, date(2026, 11, 2)), 'BPLP/26-27/00002')
        self.assertEqual(next_number('INV', site, date(2027, 4, 1)), 'BPLP/27-28/00001')  # resets each FY
        self.assertLessEqual(len(next_number('INV', site, date(2026, 11, 3))), 16)
        self.assertEqual(next_number('SO', site, date(2026, 11, 1)), 'SOBPLPL/26-27/00001')
        self.assertEqual(NumberSeries.objects.count(), 3)


class PriceRuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site = Site.objects.create(code='P1', name='Plant', kind='Plant', state='23')
        cls.depot = Site.objects.create(code='D1', name='Depot', kind='Depot', state='23')
        cls.opc = Product.objects.create(code='OPC53', name='OPC 53', category='Cement', unit='BAG', gst_rate=28, hsn='2523')
        cls.dealer = Partner.objects.create(code='DL1', name='Dealer', kind='Dealer', district='Bhopal', state='23')
        cls.retailer = Partner.objects.create(code='RT1', name='Retailer', kind='Retailer', district='Sehore', state='23')
        today = timezone.localdate()
        R = lambda **k: PriceRule.objects.create(product=cls.opc, valid_from=k.pop('valid_from', today - timedelta(days=10)), **k)
        cls.std = R(rate=400)
        cls.slab = R(rate=390, min_quantity=500)
        cls.bhopal = R(rate=395, district='Bhopal')
        cls.depot_price = R(rate=410, site=cls.depot)
        cls.dealer_kind = R(rate=385, partner_kind='Dealer')
        cls.special = R(rate=380, partner=cls.dealer, min_quantity=1000)
        cls.old = R(rate=350, valid_from=today - timedelta(days=100), valid_to=today - timedelta(days=11))
        cls.future = R(rate=450, valid_from=today + timedelta(days=5), site=cls.site, district='Sehore')

    def test_most_specific_rule_wins(self):
        self.assertEqual(price_for(self.opc, self.site, None, 10).rate, 400)
        self.assertEqual(price_for(self.opc, self.site, None, 600).rate, 390)
        self.assertEqual(price_for(self.opc, self.depot, self.retailer, 10).rate, 410)
        self.assertEqual(price_for(self.opc, self.site, self.dealer, 10).rate, 385)  # dealer type beats district
        self.assertEqual(price_for(self.opc, self.site, self.dealer, 1200).rate, 380)  # own price at slab
        self.assertEqual(price_for(self.opc, self.site, self.retailer, 10).rate, 400)  # future Sehore price not yet valid

    def test_order_uses_list_price_and_flags_discount(self):
        admin = User.objects.create_superuser('pr-admin', 'pr@example.com', 'Original-Password-816!')
        with self.settings(EMAIL_OTP_ENABLED=False):
            self.client.force_login(admin)
            due = (timezone.localdate() + timedelta(days=2)).isoformat()
            data = {'site': self.site.pk, 'partner': self.dealer.pk, 'due_date': due, 'freight_terms': 'ex_works', 'place_of_supply': '',
                    'lines-TOTAL_FORMS': 2, 'lines-INITIAL_FORMS': 0, 'lines-MIN_NUM_FORMS': 1, 'lines-MAX_NUM_FORMS': 50,
                    'lines-0-product': self.opc.pk, 'lines-0-quantity': '100', 'lines-0-rate': '',
                    'lines-1-product': self.opc.pk, 'lines-1-quantity': '50', 'lines-1-rate': '370'}
            r = self.client.post(reverse('order_new', args=['Sale']), data)
            order = Order.objects.get()
            self.assertRedirects(r, reverse('order_detail', args=[order.pk]))
            self.assertTrue(order.number.startswith('SOP1/'))
            a, b = order.lines.order_by('id')
            self.assertEqual((a.rate, a.list_rate, a.price_rule, a.gst_rate, a.hsn), (Decimal('385.00'), Decimal('385.00'), self.dealer_kind, Decimal('28.00'), '2523'))
            self.assertTrue(b.below_list)
            self.assertFalse(order.is_interstate)
            self.assertEqual(order.grand_total, Decimal('72960'))  # (38500 + 18500) × 1.28
            page = self.client.get(reverse('order_detail', args=[order.pk]))
            self.assertContains(page, 'below list price')
            self.assertContains(page, 'CGST')
            # Inter-state when delivered to another state
            Order.objects.filter(pk=order.pk).update(place_of_supply='27')
            self.assertTrue(Order.objects.get(pk=order.pk).is_interstate)
            self.assertContains(self.client.get(reverse('order_detail', args=[order.pk])), 'IGST')
            # Draft can be edited; price API answers
            self.assertEqual(self.client.get(reverse('order_edit', args=[order.pk])).status_code, 200)
            j = self.client.get(reverse('order_price'), {'product': self.opc.pk, 'site': self.site.pk, 'partner': self.dealer.pk, 'qty': 100}).json()
            self.assertEqual(j['rate'], '385.00')
            self.assertEqual(j['next_slab']['rate'], '380.00')
            # No price and no rate → error
            PriceRule.objects.update(active=False)
            data['lines-TOTAL_FORMS'] = 1
            r = self.client.post(reverse('order_new', args=['Sale']), data)
            self.assertContains(r, 'No list price for this product')

    def test_price_revise_versioning_and_overlap(self):
        admin = User.objects.create_superuser('pr-admin2', 'pr2@example.com', 'Original-Password-816!')
        with self.settings(EMAIL_OTP_ENABLED=False):
            self.client.force_login(admin)
            today = timezone.localdate()
            base = {'product': self.opc.pk, 'rate': '401', 'min_quantity': '0', 'site': '', 'district': '', 'partner_kind': '', 'partner': '',
                    'valid_from': today.isoformat(), 'valid_to': '', 'active': 'on', 'notes': ''}
            r = self.client.post(reverse('prices_new'), base)
            self.assertContains(r, 'already covers these dates')
            order = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=admin)
            OrderLine.objects.create(order=order, product=self.opc, quantity=1, rate=400, price_rule=self.std)
            self.assertRedirects(self.client.get(reverse('prices_edit', args=[self.std.pk])), reverse('price_revise', args=[self.std.pk]))
            start = today + timedelta(days=1)
            r = self.client.post(reverse('price_revise', args=[self.std.pk]), {**base, 'rate': '420', 'valid_from': start.isoformat()})
            self.assertEqual(r.status_code, 302)
            self.std.refresh_from_db()
            new = PriceRule.objects.get(replaces=self.std)
            self.assertEqual((self.std.valid_to, new.rate, new.valid_from), (today, Decimal('420.00'), start))
            self.assertEqual(price_for(self.opc, self.site, None, 1).rate, 400)
            self.assertEqual(price_for(self.opc, self.site, None, 1, on=start).rate, 420)
            # Ending an unused future price switches it off
            self.client.post(reverse('price_end', args=[self.future.pk]))
            self.future.refresh_from_db()
            self.assertFalse(self.future.active)
            self.assertEqual(self.client.get(reverse('prices'), {'check_product': self.opc.pk, 'check_qty': '600'}).context['check']['rule'], self.slab)
