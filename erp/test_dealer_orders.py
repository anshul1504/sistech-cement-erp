"""Dealer order requests: dealer asks from own login, sales team turns it into a draft order or declines; no cross-dealer access."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import dealer_orders as DO
from .models import DealerLedgerAccess, DealerOrderRequest, Partner, PriceRule, SiteAccess, Order
from .test_projects import seed_projects


@override_settings(EMAIL_OTP_ENABLED=False)
class DealerOrderRequestTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.customer.kind = 'Dealer'
        self.customer.save()
        self.bag.category = 'Cement'
        self.bag.save()
        PriceRule.objects.get_or_create(product=self.bag, partner=None, site=None, partner_kind='', district='', min_quantity=0,
                                        defaults=dict(rate=D('350'), valid_from=self.today - timedelta(days=5), active=True, created_by=self.maker))
        self.dealer_user = User.objects.create_user('dealer-login', email='d@example.test')
        DealerLedgerAccess.objects.create(user=self.dealer_user, partner=self.customer)
        self.other = Partner.objects.create(code='OTHER-DLR', name='Other Dealer', kind='Dealer')
        self.rep = User.objects.create_user('qa-sales-rep')
        self.rep.user_permissions.set(Permission.objects.filter(codename__in=['create_sales_order', 'view_sales_order']))
        SiteAccess.objects.create(user=self.rep, site=self.site)

    def test_dealer_requests_and_sales_turns_it_into_a_draft_order(self):
        self.client.force_login(self.dealer_user)
        page = self.client.get(reverse('dealer_orders'))
        self.assertContains(page, 'Available now')
        r = self.client.post(reverse('dealer_orders'), {'product_0': self.bag.pk, 'qty_0': '200', 'required_by': str(self.today + timedelta(days=3)),
                                                        'ship_to': 'Shop 4, Bhopal', 'note': 'Unload at back gate'}, follow=True)
        self.assertContains(r, 'sent')
        req = DealerOrderRequest.objects.get()
        self.assertEqual((req.partner, req.status), (self.customer, 'submitted'))
        self.assertEqual(req.lines.get().indicative_rate, D('350.00'))
        self.assertGreater(req.estimate, D('70000'))
        # dealers never see staff screens
        self.assertEqual(self.client.get(reverse('dealer_requests')).status_code, 403)
        # sales rep accepts at their plant → draft order priced from the list
        self.client.force_login(self.rep)
        self.assertContains(self.client.get(reverse('dealer_requests')), req.number)
        r = self.client.post(reverse('dealer_request', args=[req.pk]), {'decision': 'accept', 'site': self.site.pk})
        order = Order.objects.get(dealer_request=req)
        self.assertRedirects(r, reverse('order_detail', args=[order.pk]), fetch_redirect_response=False)
        self.assertEqual((order.status, order.partner, order.customer_ref, order.created_by), ('Draft', self.customer, req.number, self.rep))
        self.assertEqual(order.lines.get().rate, D('350.00'))
        req.refresh_from_db()
        self.assertEqual(req.status, 'accepted')
        with self.assertRaises(ValidationError):
            DO.accept(self.rep, req.pk, self.site)       # only once

    def test_decline_needs_reason_and_dealer_can_cancel_only_own(self):
        req = DO.submit(self.dealer_user, self.customer, self.today + timedelta(days=2), [(self.bag, D('10'))])
        with self.assertRaises(ValidationError):
            DO.reject(self.rep, req.pk, '')
        DO.reject(self.rep, req.pk, 'Clear the overdue first')
        self.client.force_login(self.dealer_user)
        self.assertContains(self.client.get(reverse('dealer_orders')), 'Clear the overdue first')
        mine = DO.submit(self.dealer_user, self.customer, self.today + timedelta(days=2), [(self.bag, D('5'))])
        foreign = DealerOrderRequest.objects.create(number='X-1', partner=self.other, requested_by=self.rep, required_by=self.today)
        self.client.post(reverse('dealer_order_cancel', args=[foreign.pk]))
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, 'submitted')
        self.client.post(reverse('dealer_order_cancel', args=[mine.pk]))
        mine.refresh_from_db()
        self.assertEqual(mine.status, 'cancelled')

    def test_validation(self):
        with self.assertRaises(ValidationError):
            DO.submit(self.dealer_user, self.customer, self.today - timedelta(days=1), [(self.bag, D('5'))])
        with self.assertRaises(ValidationError):
            DO.submit(self.dealer_user, self.customer, self.today, [])
        with self.assertRaises(ValidationError):
            DO.submit(self.dealer_user, self.customer, self.today, [(self.bag, D('5')), (self.bag, D('6'))])
        req = DO.submit(self.dealer_user, self.customer, self.today, [(self.bag, D('5'))])
        other_site = self.site.__class__.objects.create(code='FAR-01', name='Far depot', kind='Depot')
        with self.assertRaises(ValidationError):
            DO.accept(self.rep, req.pk, other_site)      # not the rep's plant
