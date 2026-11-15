"""Dated authority, no transitive grants, retained actual approver and rollback regressions."""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import delegation as DA
from accounts.models import ApprovalDelegation, ApprovalDelegationUse, UserProfile
from accounts.test_support import force_login_recent
from erp import gl, quotes as QT, stock as ST, treasury as TR, schemes, rewards as RW, closing
from erp.models import AuditEvent, Order, OrderLine, SiteAccess, Partner, StockMovement
from erp.services import move_stock, transition_order
from erp.test_schemes import approved_scheme, invoice_fixture
from erp.test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class DelegationTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.admin = User.objects.create_superuser('delegate-admin', 'admin@sistech.co.in', 'Verification-Password-816!')
        self.deputy = User.objects.create_user('delegate-deputy', 'deputy@sistech.co.in', 'Verification-Password-816!')
        self.deputy.user_permissions.set(Permission.objects.filter(content_type__app_label='erp').exclude(codename__in=DA.POLICIES))
        SiteAccess.objects.create(user=self.deputy, site=self.site)

    def grant(self, permission, principal=None, **changes):
        now = timezone.now()
        data = dict(principal=principal or self.checker, deputy=self.deputy, permission=permission,
                    site=self.site if DA.POLICIES.get(permission, ('Unknown', True, False))[1] else None, starts_at=now,
                    ends_at=now + timedelta(days=1), reason='Planned cover during leave')
        data.update(changes)
        return DA.create(self.admin, **data)

    def order(self, kind='Sale', maker=None, qty=10, rate=350, list_rate=None):
        order = Order.objects.create(kind=kind, site=self.site, partner=self.customer if kind == 'Sale' else self.supplier,
                                     created_by=maker or self.maker)
        OrderLine.objects.create(order=order, product=self.bag, quantity=qty, rate=rate, list_rate=list_rate, gst_rate=18)
        transition_order(maker or self.maker, order.pk, 'submit', confirm_duplicate=True)
        order.refresh_from_db()
        return order

    def test_live_dates_expire_exactly_and_no_role_assignment_is_added(self):
        now = timezone.now()
        grant = self.grant('approve_sales_order', starts_at=now + timedelta(hours=1), ends_at=now + timedelta(hours=2))
        self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.site))
        from unittest.mock import patch
        with patch('accounts.delegation.timezone.now', return_value=grant.starts_at):
            self.assertTrue(DA.can_approve(self.deputy, grant.permission, self.site))
        with patch('accounts.delegation.timezone.now', return_value=grant.ends_at):
            self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.site))
        self.assertFalse(self.deputy.has_perm('erp.approve_sales_order'))

    def test_creation_requires_admin_valid_scope_direct_authority_and_nonoverlap(self):
        with self.assertRaises(PermissionDenied):
            DA.create(self.maker, self.checker, self.deputy, 'approve_sales_order', self.site,
                      timezone.now(), timezone.now()+timedelta(days=1), 'Forbidden')
        for changes in [dict(deputy=self.checker), dict(site=None), dict(site=self.other),
                        dict(principal=self.deputy), dict(ends_at=timezone.now()-timedelta(seconds=1)),
                        dict(starts_at=timezone.now()-timedelta(days=1)), dict(reason='')]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.grant('approve_sales_order', **changes)
        grant = self.grant('approve_sales_order')
        with self.assertRaisesMessage(ValidationError, 'overlapping'):
            self.grant('approve_sales_order', principal=self.maker)
        self.grant('approve_sales_order', starts_at=grant.ends_at, ends_at=grant.ends_at+timedelta(hours=1))
        with self.assertRaises(ValidationError):
            self.grant('add_partner')  # unknown permission is never delegatable

    def test_principal_revocation_expiry_and_scope_loss_disable_authority_immediately(self):
        grant = self.grant('approve_sales_order')
        self.assertTrue(DA.can_approve(self.deputy, grant.permission, self.site))
        self.checker.user_permissions.remove(Permission.objects.get(codename=grant.permission))
        self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.site))
        self.checker.user_permissions.add(Permission.objects.get(codename=grant.permission))
        SiteAccess.objects.filter(user=self.checker, site=self.site).delete()
        self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.site))
        SiteAccess.objects.create(user=self.checker, site=self.site)
        UserProfile.objects.update_or_create(user=self.checker, defaults={'access_expires_at': timezone.now()-timedelta(seconds=1)})
        self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.site))
        UserProfile.objects.filter(user=self.checker).update(access_expires_at=None)
        User.objects.filter(pk=self.checker.pk).update(is_active=False)
        self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.site))

    def test_delegation_is_not_transitive_and_finance_scope_is_not_inherited(self):
        self.grant('approve_sales_order')
        third = User.objects.create_user('third-deputy')
        SiteAccess.objects.create(user=third, site=self.site)
        with self.assertRaises(ValidationError):
            self.grant('approve_sales_order', principal=self.deputy, deputy=third)
        self.deputy.user_permissions.remove(Permission.objects.get(codename='access_company_finance'))
        with self.assertRaises(ValidationError):
            self.grant('release_supplierpayment')
        self.assertFalse(DA.can_approve(self.deputy, 'release_supplierpayment'))

    def test_order_actual_approver_history_queue_and_revocation(self):
        from erp.insights import my_queue
        order = self.order()
        grant = self.grant('approve_sales_order')
        self.assertTrue(any(row['label'] == 'Sales orders to approve' for row in my_queue(self.deputy)))
        force_login_recent(self.client, self.deputy)
        response = self.client.get(reverse('order_detail', args=[order.pk]))
        self.assertTrue(response.context['can_approve'])
        self.client.post(reverse('order_action', args=[order.pk, 'approve']), {'backorder_reason': 'Approved partner supply'})
        order.refresh_from_db()
        self.assertEqual((order.status, order.approved_by_id), ('Approved', self.deputy.pk))
        self.assertEqual(grant.uses.get().actor_id, self.deputy.pk)
        self.assertTrue(AuditEvent.objects.filter(action='Delegated approval used', actor=self.deputy, reference=order.number).exists())
        second = self.order()
        DA.revoke(self.admin, grant.pk, 'Principal returned')
        DA.revoke(self.admin, grant.pk, 'Retry')
        with self.assertRaises(PermissionDenied):
            transition_order(self.deputy, second.pk, 'approve', backorder_reason='Supply')
        self.assertEqual(grant.uses.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='Approval delegation revoked').count(), 1)

    def test_principal_or_deputy_maker_cannot_use_delegation_and_failed_transition_rolls_back(self):
        grant = self.grant('approve_sales_order')
        for maker in [self.checker, self.deputy]:
            order = self.order(maker=maker)
            with self.assertRaisesMessage(ValidationError, 'maker-checker'):
                transition_order(self.deputy, order.pk, 'approve', backorder_reason='Supply')
        order = self.order()
        with self.assertRaises(ValidationError):
            transition_order(self.deputy, order.pk, 'approve')  # missing backorder reason
        self.assertFalse(grant.uses.exists())
        self.assertFalse(AuditEvent.objects.filter(action='Delegated approval used').exists())

    def test_price_credit_and_high_purchase_authority_are_separate(self):
        self.grant('approve_sales_order')
        order = self.order(rate=300, list_rate=350)
        with self.assertRaises(ValidationError):
            transition_order(self.deputy, order.pk, 'approve', backorder_reason='Supply')
        self.grant('approve_price_exception')
        Partner.objects.filter(pk=self.customer.pk).update(credit_limit=1)
        with self.assertRaises(ValidationError):
            transition_order(self.deputy, order.pk, 'approve', backorder_reason='Supply')
        self.grant('override_credit_sales_order')
        transition_order(self.deputy, order.pk, 'approve', override_reason='Independent temporary credit',
                         override_until=self.today+timedelta(days=1), backorder_reason='Supply')
        self.assertEqual(ApprovalDelegationUse.objects.count(), 3)
        self.grant('approve_purchase_order')
        po = self.order(kind='Purchase', qty=100000)
        with self.assertRaises(ValidationError):
            transition_order(self.deputy, po.pk, 'approve')
        self.grant('approve_high_value_purchase')
        transition_order(self.deputy, po.pk, 'approve')
        po.refresh_from_db()
        self.assertEqual(po.approved_by_id, self.deputy.pk)

    def test_quotation_and_stock_disposition(self):
        self.grant('approve_quotation')
        quote = QT.save(self.maker, dict(site=self.site, partner=self.customer, date=self.today,
            valid_until=self.today+timedelta(days=1), place_of_supply='23', freight_terms='for_destination'),
            [(self.bag, 10, 300)])
        quote.lines.update(list_rate=350)
        QT.submit(self.maker, quote.pk)
        QT.approve(self.deputy, quote.pk, True)
        move_stock(self.maker, self.site, self.bag, 10, 'delegated-stock', 'Opening', unit_cost=100)
        self.grant('decide_damagereport')
        damage = ST.report_damage(self.maker, self.site, self.bag, 2, 'wet', 'Warehouse', 'Verification storekeeper', 'Wet bags')
        ST.decide_damage(self.deputy, damage.pk, 1, 1, 'Repack one, scrap one')
        self.assertEqual(ApprovalDelegationUse.objects.count(), 2)

    def test_journal_and_payment_approvals_preserve_maker_checker(self):
        self.grant('approve_journalentry')
        voucher = gl.create_voucher(self.maker, self.today, None, 'Verification deposit', [
            (gl.acct('bank_main'), 100, 0, None, ''), (gl.acct('capital'), 0, 100, None, '')])
        gl.decide_voucher(self.deputy, voucher.pk, True)
        self.grant('approve_supplierpayment')
        self.grant('release_supplierpayment')
        asset = TR.capitalise(self.maker, dict(code='DEL-FA', name='Verification machine', category='fa_plant',
            site=self.site, supplier=self.supplier, put_to_use=self.today, cost=D(1000), salvage=D(0),
            method='slm', life_years=D(10)), 'ap')
        payment = TR.propose(self.maker, self.supplier, gl.acct('bank_main'), {('asset',asset.pk):asset.cost})
        TR.decide(self.deputy, payment.pk, True)
        TR.release(self.deputy, payment.pk, 'DEL-QA-UTR')
        payment.refresh_from_db()
        self.assertEqual(payment.paid_by_id, self.deputy.pk)
        self.assertEqual(ApprovalDelegationUse.objects.count(), 3)

    def test_scheme_accrual_and_payout(self):
        version = approved_scheme(self)
        invoice_fixture(self, [(self.bag,D(2000))])
        evaluation = schemes.calculate(self.maker, version.pk, version.starts_on, version.ends_on, uuid4())
        batch = RW.create_batch(self.maker, evaluation.pk)
        self.grant('approve_schemeentitlementbatch')
        RW.decide_batch(self.deputy, batch.pk, True)
        payout = RW.propose_payout(self.maker, self.customer, 'ledger')
        self.grant('approve_schemesettlement')
        force_login_recent(self.client, self.deputy)
        self.assertContains(self.client.get(reverse('scheme_payouts')), reverse('scheme_payout_decide', args=[payout.pk, 'approve']))
        RW.decide_payout(self.deputy, payout.pk, True)
        self.assertEqual(ApprovalDelegationUse.objects.count(), 2)

    def test_ui_is_participant_scoped_and_creation_needs_recent_admin_confirmation(self):
        grant = self.grant('approve_sales_order')
        force_login_recent(self.client, self.outsider)
        self.assertEqual(self.client.get(reverse('delegation_detail', args=[grant.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('delegation_new')).status_code, 403)
        self.assertEqual(self.client.post(reverse('delegation_revoke',args=[grant.pk]), {'reason':'Forbidden'}).status_code, 403)
        force_login_recent(self.client, self.deputy)
        self.assertEqual(self.client.get(reverse('delegation_detail',args=[grant.pk])).status_code, 200)
        self.client.force_login(self.admin)
        self.assertTrue(self.client.post(reverse('delegation_new'), {}).url.startswith(reverse('reauthenticate')))

    def test_deputy_scope_loss_and_revocation_prevent_queued_approvals(self):
        order = self.order()
        grant = self.grant('approve_sales_order')
        self.assertTrue(DA.approvable(Order.objects.all(), self.deputy, grant.permission).filter(pk=order.pk).exists())
        self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.other))
        SiteAccess.objects.filter(user=self.deputy, site=self.site).delete()
        self.assertFalse(DA.can_approve(self.deputy, grant.permission, self.site))
        self.assertFalse(DA.approvable(Order.objects.all(), self.deputy, grant.permission).exists())
        with self.assertRaises(PermissionDenied):
            DA.authorize(self.deputy, grant.permission, self.site, reference=order.number)
        self.assertFalse(grant.uses.exists())

    def test_delegated_count_posts_once_and_retains_actual_reviewer(self):
        move_stock(self.maker, self.site, self.bag, 10, 'delegated-count-opening', 'Opening', unit_cost=100)
        count = ST.start_count(self.maker, self.site)
        line = count.lines.get()
        ST.save_count(self.maker, count.pk, {line.pk:D(9)}, {line.pk:'One bag missing'}, submit=True)
        grant = self.grant('approve_stockcount')
        ST.review_count(self.deputy, count.pk, 'approve', note='Independent physical review')
        count.refresh_from_db()
        self.assertEqual(count.approved_by_id, self.deputy.pk)
        self.assertEqual(grant.uses.count(), 1)
        with self.assertRaises(ValidationError):
            ST.review_count(self.deputy, count.pk, 'approve')
        self.assertEqual(grant.uses.count(), 1)

    def test_delegated_month_close_reopen_and_principal_maker_denial(self):
        last = self.today.replace(day=1) - timedelta(days=1)
        review = closing.prepare_period(self.maker, last, 'Independent evidence',
            tax_reviewed=True, stock_reviewed=True, bank_reviewed=True)
        grant = self.grant('close_period')
        gl.set_period(self.deputy, last, True)
        gl.set_period(self.deputy, last, False, 'Authorized correction')
        self.assertEqual(grant.uses.count(), 2)
        closing.prepare_period(self.checker, last, 'Principal prepared this review',
            tax_reviewed=True, stock_reviewed=True, bank_reviewed=True)
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            gl.set_period(self.deputy, last, True)
        self.assertEqual(grant.uses.count(), 2)

    def test_delegated_year_close_and_reopening_use_same_retained_authority(self):
        start, _ = closing.year_bounds(self.today)
        start, end = closing.year_bounds(start - timedelta(days=1))
        gl.post(self.maker, 'delegated-year-income', start, None, 'Verification income',
            [(gl.acct('cash'), 500, 0, None, ''), (gl.acct('sales_cement'), 0, 500, None, '')])
        cursor = start
        while cursor <= end:
            closing.prepare_period(self.maker, cursor, 'Independent month evidence',
                tax_reviewed=True, stock_reviewed=True, bank_reviewed=True)
            gl.set_period(self.checker, cursor, True)
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        year = closing.prepare_year(self.maker, end, 'Annual review')
        grant = self.grant('approve_yearclose')
        closing.decide_year(self.deputy, year.pk, True)
        closing.request_year_reopen(self.maker, year.pk, 'Correct annual classification')
        closing.approve_year_reopen(self.deputy, year.pk, True)
        year.refresh_from_db()
        self.assertEqual(year.status, 'reopened')
        self.assertEqual(grant.uses.count(), 2)

    def test_use_references_are_not_automatic_document_read_access(self):
        order = self.order()
        grant = self.grant('approve_sales_order')
        transition_order(self.deputy, order.pk, 'approve', backorder_reason='Supply planned')
        force_login_recent(self.client, self.deputy)
        response = self.client.get(reverse('delegation_detail', args=[grant.pk]))
        self.assertNotContains(response, order.number)
        force_login_recent(self.client, self.admin)
        self.assertContains(self.client.get(reverse('delegation_detail',args=[grant.pk])), order.number)

    def test_assigned_second_site_and_finance_grants_do_not_expand_delegation_scope(self):
        SiteAccess.objects.create(user=self.checker, site=self.other)
        SiteAccess.objects.create(user=self.deputy, site=self.other)
        self.grant('approve_sales_order')
        self.assertFalse(DA.can_approve(self.deputy, 'approve_sales_order', self.other))
        with self.assertRaises(PermissionDenied):
            DA.authorize(self.deputy, 'approve_sales_order', self.other, reference='Forbidden second-site approval')
        self.grant('release_supplierpayment')
        self.deputy.user_permissions.remove(Permission.objects.get(codename='access_company_finance'))
        self.assertFalse(DA.can_approve(self.deputy, 'release_supplierpayment'))
        with self.assertRaises(PermissionDenied):
            DA.authorize(self.deputy, 'release_supplierpayment', reference='Forbidden finance approval')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_closed_finance_period_rolls_back_authority_use_and_approval(self):
        from erp.models import FinancialPeriod
        voucher = gl.create_voucher(self.maker, self.today, None, 'Verification deposit', [
            (gl.acct('bank_main'),100,0,None,''), (gl.acct('capital'),0,100,None,'')])
        grant = self.grant('approve_journalentry')
        FinancialPeriod.objects.filter(pk=gl.period_for(self.today).pk).update(status='closed')
        before = AuditEvent.objects.count()
        with self.assertRaises(ValidationError):
            gl.decide_voucher(self.deputy, voucher.pk, True)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'draft')
        self.assertFalse(grant.uses.exists())
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_payout_button_and_service_both_exclude_delegating_maker(self):
        version = approved_scheme(self)
        invoice_fixture(self, [(self.bag,D(2000))])
        evaluation = schemes.calculate(self.maker, version.pk, version.starts_on, version.ends_on, uuid4())
        batch = RW.create_batch(self.maker, evaluation.pk)
        RW.decide_batch(self.checker, batch.pk, True)
        payout = RW.propose_payout(self.checker, self.customer, 'ledger')
        self.grant('approve_schemesettlement', principal=self.checker)
        force_login_recent(self.client, self.deputy)
        self.assertNotContains(self.client.get(reverse('scheme_payouts')), reverse('scheme_payout_decide', args=[payout.pk, 'approve']))
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            RW.decide_payout(self.deputy, payout.pk, True)
        self.assertFalse(ApprovalDelegationUse.objects.exists())
