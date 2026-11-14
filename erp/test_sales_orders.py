"""Sales order controls (FR-SLS-004…010, FR-AR-003/004): duplicate check, credit override, price exception, ATP / backorder,
hold and release, revision, reasons on cancel / short-close, stages and screens."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import sales
from .models import Order, OrderLine, SiteAccess, Partner
from .services import transition_order, move_stock
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class SalesOrderTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        move_stock(self.maker, self.site, self.bag, D(100), 'opening:so', 'Opening', unit_cost=D(300))
        # an approver without credit-override / price-exception / hold authority
        self.plain = User.objects.create_user('qa-plain-approver')
        self.plain.user_permissions.set(Permission.objects.filter(content_type__app_label='erp').exclude(
            codename__in=['override_credit_sales_order', 'approve_price_exception', 'hold_sales_order']))
        SiteAccess.objects.create(user=self.plain, site=self.site)

    def order(self, qty=10, rate=350, list_rate=None, submit=True, **kw):
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.customer, created_by=self.maker, **kw)
        OrderLine.objects.create(order=o, product=self.bag, quantity=qty, rate=rate, list_rate=list_rate, gst_rate=18)
        if submit:
            transition_order(self.maker, o.pk, 'submit', confirm_duplicate=True)
            o.refresh_from_db()
        return o

    def test_duplicate_request_is_flagged(self):
        self.order()
        dup = self.order(submit=False)
        with self.assertRaisesMessage(ValidationError, 'Possible duplicate'):
            transition_order(self.maker, dup.pk, 'submit')
        transition_order(self.maker, dup.pk, 'submit', confirm_duplicate=True)
        self.assertEqual(Order.objects.get(pk=dup.pk).status, 'Submitted')

    def test_credit_override_needs_authority_reason_and_expiry(self):
        Partner.objects.filter(pk=self.customer.pk).update(credit_limit=1000)
        o = self.order()                                                       # 10 × 350 × 1.18 = 4,130 > 1,000
        with self.assertRaisesMessage(ValidationError, 'credit-override authority'):
            transition_order(self.plain, o.pk, 'approve')
        with self.assertRaisesMessage(ValidationError, 'Record why'):
            transition_order(self.checker, o.pk, 'approve')
        with self.assertRaisesMessage(ValidationError, 'expiry date'):
            transition_order(self.checker, o.pk, 'approve', override_reason='Cheque received', override_until=self.today + timedelta(days=90))
        transition_order(self.checker, o.pk, 'approve', override_reason='Cheque received', override_until=self.today + timedelta(days=7))
        o.refresh_from_db()
        self.assertEqual((o.status, o.credit_override_by, o.credit_override_reason), ('Approved', self.checker, 'Cheque received'))
        self.assertIn('credit_override', o.approval_checks)
        self.assertEqual(sales.dispatch_block(o), [])                          # override still valid
        Order.objects.filter(pk=o.pk).update(credit_override_until=self.today - timedelta(days=1))
        o.refresh_from_db()
        self.assertTrue(any('Credit limit' in p for p in sales.dispatch_block(o)))   # expired → re-checked at dispatch

    def test_price_exception_and_backorder(self):
        o = self.order(rate=300, list_rate=D(350))
        with self.assertRaisesMessage(ValidationError, 'price-exception authority'):
            transition_order(self.plain, o.pk, 'approve')
        transition_order(self.checker, o.pk, 'approve')
        self.assertEqual(Order.objects.get(pk=o.pk).approval_checks['below_list'][0]['list'], '350.00')
        big = self.order(qty=120)                                              # 100 in stock, 10 reserved
        with self.assertRaisesMessage(ValidationError, 'backorder reason'):
            transition_order(self.checker, big.pk, 'approve')
        self.site.atp_required = True
        self.site.save()
        with self.assertRaisesMessage(ValidationError, 'approves only against free stock'):
            transition_order(self.checker, big.pk, 'approve', backorder_reason='Partner truck due')
        self.site.atp_required = False
        self.site.save()
        transition_order(self.checker, big.pk, 'approve', backorder_reason='Partner truck due')
        self.assertEqual(Order.objects.get(pk=big.pk).approval_checks['backorder']['reason'], 'Partner truck due')
        r = sales.atp(Order.objects.get(pk=big.pk))[0]
        self.assertEqual((r['available'], r['reserved'], r['short']), (D(100), D(10), D(30)))

    def test_hold_release_revise_cancel_and_close_reasons(self):
        o = self.order()
        transition_order(self.checker, o.pk, 'approve')
        with self.assertRaises(Exception):
            transition_order(self.plain, o.pk, 'hold', reason='x')
        with self.assertRaisesMessage(ValidationError, 'reason for the hold'):
            transition_order(self.checker, o.pk, 'hold')
        transition_order(self.checker, o.pk, 'hold', reason='Dealer asked to wait')
        o.refresh_from_db()
        self.assertEqual((o.status, o.held_from), ('On hold', 'Approved'))
        self.assertIn('on hold', sales.dispatch_block(o)[0])
        self.assertEqual(sales.atp(self.order(qty=1))[0]['reserved'], D(10))  # a held order still reserves its stock
        transition_order(self.checker, o.pk, 'release')
        self.assertEqual(Order.objects.get(pk=o.pk).status, 'Approved')
        new = sales.revise(self.maker, o.pk)
        self.assertEqual((new.status, new.revision, new.number, new.revised_from_id, new.lines.count()), ('Draft', 2, f'{o.number}-R2', o.pk, 1))
        self.assertEqual(Order.objects.get(pk=o.pk).status, 'Revised')
        with self.assertRaisesMessage(ValidationError, 'Only submitted or approved'):
            sales.revise(self.maker, o.pk)
        transition_order(self.maker, new.pk, 'submit', confirm_duplicate=True)
        with self.assertRaisesMessage(ValidationError, 'reason for cancelling'):
            transition_order(self.maker, new.pk, 'cancel')
        transition_order(self.maker, new.pk, 'cancel', reason='Customer changed brand')
        self.assertEqual(Order.objects.get(pk=new.pk).cancel_reason, 'Customer changed brand')

    def test_stage_and_screens(self):
        o = self.order()
        self.assertEqual(sales.stage(o), 'Approval pending')
        transition_order(self.checker, o.pk, 'approve')
        o.refresh_from_db()
        self.assertEqual(sales.stage(o), 'Approved')
        transition_order(self.checker, o.pk, 'post')
        o.refresh_from_db()
        self.assertEqual((sales.stage(o), sales.progress(o)[0]['sent']), ('Dispatched', D(10)))
        self.client.force_login(self.checker)
        late = self.order(due_date=self.today - timedelta(days=2), priority='urgent')
        transition_order(self.checker, late.pk, 'approve')
        for q in ['', '?status=late', '?status=urgent', '?status=all', '?status=Completed&q=' + o.number, f'?site={self.site.pk}&from=2020-01-01']:
            self.assertEqual(self.client.get(reverse('sales') + q).status_code, 200, q)
        r = self.client.get(reverse('sales') + '?status=late')
        self.assertContains(r, late.number)
        self.assertNotContains(r, o.number)
        self.assertContains(self.client.get(reverse('order_detail', args=[o.pk])), 'Delivery terms')
        pending = self.order(qty=500)
        page = self.client.get(reverse('order_detail', args=[pending.pk]))
        self.assertContains(page, 'Approval decision')
        self.assertContains(page, 'backorder_reason')
        r = self.client.post(reverse('order_action', args=[pending.pk, 'approve']), {'backorder_reason': 'Partner plant dispatch Monday'})
        self.assertEqual(Order.objects.get(pk=pending.pk).status, 'Approved')
        r = self.client.post(reverse('order_action', args=[pending.pk, 'hold']), {'reason': 'Payment dispute'})
        self.assertEqual(Order.objects.get(pk=pending.pk).status, 'On hold')
        self.assertContains(self.client.get(reverse('sales') + '?status=On+hold'), pending.number)
        r = self.client.post(reverse('order_action', args=[pending.pk, 'revise']))
        self.assertEqual(Order.objects.get(pk=pending.pk).status, 'Revised')
