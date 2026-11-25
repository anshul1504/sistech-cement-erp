"""Integrated business verification on an isolated database, never real company data."""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import gl, inbound as IB, logistics as L, payables as AP, production as PR, quality as QC, treasury as TR
from .invoice_views import create_invoice
from .ledger import balance as customer_balance
from .models import (CreditNote, Driver, Invoice, JournalEntry, JournalLine, Order, OrderLine, Organization, Partner, Production,
                     Product, QualitySpec, Receipt, Site, SiteAccess, StockBalance, StockMovement, Vehicle)
from .services import move_stock, transition_order


@override_settings(EMAIL_OTP_ENABLED=False)
class BusinessCycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_support import seed_business_data
        seed_business_data(cls)

    def account(self, key):
        return sum((l.debit - l.credit for l in JournalLine.objects.filter(account__system_key=key, entry__status='posted')), D(0))

    def assert_controls(self):
        self.assertEqual(gl.trial_balance(None, timezone.localdate())['diff'], 0)
        self.assertEqual(gl.balance_sheet(timezone.localdate())['difference'], 0)
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()), gl.subledger_check())

    def purchase(self, qty=100, rate=100, hold=0):
        order = Order.objects.create(site=self.site, partner=self.supplier, kind='Purchase', created_by=self.maker, payment_terms_days=30)
        line = OrderLine.objects.create(order=order, product=self.raw, quantity=qty, rate=rate, gst_rate=5)
        transition_order(self.maker, order.pk, 'submit')
        transition_order(self.checker, order.pk, 'approve')
        grn = IB.gate_in(self.maker, order, dict(vehicle_number='MP04GA1234', challan_number=f'QA-{order.pk}'), {line: D(qty)})
        row = grn.lines.get()
        IB.post(self.maker, grn.pk, {row: dict(received=D(qty), accepted=D(qty-hold), rejected=D(0), damaged=D(0), hold=D(hold), reason='QC pending' if hold else '')})
        return order, grn, grn.lines.get()

    def supplier_invoice(self, row, qty, rate=100):
        taxable = D(qty) * D(rate)
        return AP.create_invoice(self.maker, self.supplier, dict(invoice_number=f'QA-SINV-{row.pk}', invoice_date=timezone.localdate(),
            supplier_gstin=self.supplier.gstin, billed_taxable=taxable, billed_tax=taxable*D('.05'), billed_total=taxable*D('1.05')), {row: (D(qty), D(rate), D(5))})

    def test_purchase_quality_production_sale_collection_reconciles(self):
        order, grn, row = self.purchase(hold=10)
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.raw).quantity, 90)
        with self.assertRaises(ValidationError):
            IB.release(self.maker, row.pk, 10, 0, 'Own GRN release blocked')
        IB.release(self.checker, row.pk, 10, 0, 'Lab accepted held material')
        row.refresh_from_db()
        supplier_invoice = self.supplier_invoice(row, 100)
        payment = TR.propose(self.maker, self.supplier, gl.acct('bank_main'), {('sinv', supplier_invoice.pk): D(10500)})
        TR.decide(self.checker, payment.pk, True)
        TR.release(self.checker, payment.pk, 'QA-UTR-PAY')
        with self.assertRaises(ValidationError):
            TR.release(self.checker, payment.pk, 'QA-UTR-PAY')
        self.assertEqual(self.account('ap'), 0)
        self.assertEqual(self.account('grni'), 0)
        recipe = PR.save_recipe(self.maker, self.bulk, [(self.raw, D(1), D(0))])
        PR.approve_recipe(self.checker, recipe.pk)
        batch = PR.create_order(self.maker, self.site, self.bulk, 100, 'A')
        PR.issue(self.maker, batch.pk, self.raw, 100, 'Accepted GRN lot', grn)
        PR.complete(self.maker, batch.pk, 100)
        self.assertFalse(StockBalance.objects.filter(site=self.site, product=self.bulk, quantity__gt=0).exists())
        spec = QualitySpec.objects.create(product=self.bulk, parameter='Verification result', unit='unit', min_value=5, required_for_release=True)
        QC.record_result(self.checker, batch.pk, spec, 6)
        QC.release(self.checker, batch.pk)
        with self.assertRaises(ValidationError):
            QC.release(self.checker, batch.pk)
        self.assertEqual(self.account('wip'), 0)
        sale = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker)
        line = OrderLine.objects.create(order=sale, product=self.bulk, quantity=40, rate=200, gst_rate=18, hsn='2523')
        transition_order(self.maker, sale.pk, 'submit')
        transition_order(self.checker, sale.pk, 'approve')
        transition_order(self.maker, sale.pk, 'post')
        invoice = create_invoice(self.maker, sale, [line], {})
        self.client.force_login(self.maker)
        response = self.client.post(reverse('receipt_new'), dict(partner=self.customer.pk, date=str(timezone.localdate()), amount='9440', mode='neft', reference='QA-COLLECT'))
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.outstanding, 0)
        self.assertEqual(customer_balance(self.customer), 0)
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.bulk).quantity, 60)
        self.assertEqual(self.account('cogs'), 4000)
        self.assertEqual(QC.trace(batch)['issues'][0].grn_id, grn.pk)
        self.assert_controls()

    def test_sales_return_restores_original_dispatch_cost_after_cost_change(self):
        move_stock(self.maker, self.site, self.bag, D(100), 'qa:opening', 'Opening', unit_cost=D(100))
        sale = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker)
        line = OrderLine.objects.create(order=sale, product=self.bag, quantity=40, rate=200, gst_rate=18)
        transition_order(self.maker, sale.pk, 'submit'); transition_order(self.checker, sale.pk, 'approve'); transition_order(self.maker, sale.pk, 'post')
        invoice = create_invoice(self.maker, sale, [line], {})
        move_stock(self.maker, self.site, self.bag, D(100), 'qa:later-stock', 'Opening', unit_cost=D(200))
        self.client.force_login(self.maker)
        response = self.client.post(reverse('credit_note_new', args=[invoice.pk]), {'reason': 'return', f'qty_{invoice.lines.get().pk}': '10'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.account('cogs'), 3000)
        self.assertEqual(self.account('inventory_fg'), 27000)
        self.assertEqual(CreditNote.objects.get().total, 2360)
        self.assert_controls()

    def test_supplier_return_reconciles_after_average_cost_changes(self):
        _, grn, row = self.purchase()
        self.supplier_invoice(row, 100)
        move_stock(self.maker, self.site, self.raw, D(100), 'qa:later-raw', 'Opening', unit_cost=D(200))
        AP.create_debit_note(self.maker, grn, 'return', 'Return ten billed units', {row: D(10)}, 'MP04GA4321')
        self.assertEqual(self.account('grni'), 0)
        self.assertEqual(AP.balance(self.supplier)['balance'], 9450)
        self.assert_controls()

    def test_first_costed_receipt_does_not_revalue_uncosted_opening_stock(self):
        StockBalance.objects.create(site=self.site, product=self.raw, quantity=10)
        move_stock(self.maker, self.site, self.raw, D(10), 'qa:costed', 'Receipt', unit_cost=D(100))
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.raw).value, 1000)
        self.assert_controls()

    def test_business_services_reject_other_site_even_with_all_module_permissions(self):
        with self.assertRaises(PermissionDenied):
            PR.create_order(self.outsider, self.site, self.bulk, 10, 'A')
        with self.assertRaises(PermissionDenied):
            move_stock(self.outsider, self.site, self.raw, D(10), 'qa:unauthorized', 'Opening', unit_cost=100)
        order = Order.objects.create(site=self.site, partner=self.supplier, kind='Purchase', created_by=self.maker, status='Approved')
        line = OrderLine.objects.create(order=order, product=self.raw, quantity=10, rate=100)
        with self.assertRaises(PermissionDenied):
            IB.gate_in(self.outsider, order, dict(vehicle_number='MP04GA1234', challan_number='OTHER-SITE'), {line: 10})
        self.assertFalse(StockMovement.objects.exists())

    def test_loading_stock_failure_rolls_back_status_and_every_line(self):
        self.site.uses_weighbridge, self.site.stock_post_event = True, 'load_complete'
        self.site.save()
        other_bag = Product.objects.create(code='QA-BAG2', name='Second bag', category='Cement', unit='BAG', bag_weight_kg=50)
        move_stock(self.maker, self.site, self.bag, D(10), 'qa:bag1', 'Opening', unit_cost=100)
        move_stock(self.maker, self.site, other_bag, D(10), 'qa:bag2', 'Opening', unit_cost=100)
        sale = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker, status='Approved')
        first = OrderLine.objects.create(order=sale, product=self.bag, quantity=10, rate=200)
        second = OrderLine.objects.create(order=sale, product=other_bag, quantity=10, rate=200)
        far = timezone.localdate() + timedelta(days=100)
        truck = Vehicle.objects.create(registration='MP04GA1234', vehicle_type='truck10', capacity_mt=25, owner='customer', rc_valid_until=far,
                                      insurance_valid_until=far, permit_valid_until=far, fitness_valid_until=far, puc_valid_until=far)
        driver = Driver.objects.create(name='QA Driver', mobile='9876543210', licence_number='QA-LIC', licence_valid_until=far)
        dispatch = L.plan(self.maker, sale, truck, driver, {first: 10, second: 10})
        L.gate_in(self.maker, dispatch.pk, {k: True for k, _ in L.GATE_CHECKS})
        L.weigh(self.maker, dispatch.pk, 'tare', 9000, 'scale')
        L.start_loading(self.maker, dispatch.pk)
        from unittest.mock import patch
        original=L.move_stock
        def fail_second(user,site,product,*args,**kwargs):
            if product.pk==other_bag.pk:raise ValidationError('Simulated stock issue failure after advice')
            return original(user,site,product,*args,**kwargs)
        with patch('erp.logistics.move_stock',side_effect=fail_second):
            with self.assertRaises(ValidationError):
                L.complete_loading(self.maker, dispatch.pk, {l.pk: 10 for l in dispatch.lines.all()})
        dispatch.refresh_from_db()
        self.assertEqual(dispatch.status, 'loading')
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.bag).quantity, 10)
        self.assertFalse(StockMovement.objects.filter(source__startswith=f'dispatch:{dispatch.pk}:').exists())
        self.assert_controls()

    def test_receipt_form_retry_posts_cash_only_once(self):
        self.client.force_login(self.maker)
        key = str(uuid4())
        data = dict(partner=self.customer.pk, date=str(timezone.localdate()), amount='500', mode='cash', submission_key=key)
        first = self.client.post(reverse('receipt_new'), data)
        second = self.client.post(reverse('receipt_new'), data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first.url, second.url)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(self.account('cash'), 500)
        self.assert_controls()

    def test_purchase_return_before_invoice_does_not_create_false_payable(self):
        _, grn, row = self.purchase()
        note = AP.create_debit_note(self.maker, grn, 'return', 'Five accepted units returned before billing', {row: D(5)}, 'MP04GA4321')
        self.assertEqual(note.total, 0)
        row.refresh_from_db()
        self.supplier_invoice(row, 95)
        self.assertEqual(self.account('grni'), 0)
        self.assertEqual(AP.balance(self.supplier)['balance'], D(9975))
        self.assert_controls()

    def test_credit_note_retry_and_changed_receipt_payload_are_not_reposted(self):
        move_stock(self.maker, self.site, self.bag, D(100), 'qa:retry-opening', 'Opening', unit_cost=100)
        sale = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker)
        line = OrderLine.objects.create(order=sale, product=self.bag, quantity=40, rate=200, gst_rate=18)
        transition_order(self.maker, sale.pk, 'submit'); transition_order(self.checker, sale.pk, 'approve'); transition_order(self.maker, sale.pk, 'post')
        invoice = create_invoice(self.maker, sale, [line], {})
        self.client.force_login(self.maker)
        data = {'reason': 'return', f'qty_{invoice.lines.get().pk}': '10', 'submission_key': str(uuid4())}
        url = reverse('credit_note_new', args=[invoice.pk])
        first, retry = self.client.post(url, data), self.client.post(url, data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.url, retry.url)
        self.assertEqual(CreditNote.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(kind='Return').count(), 1)
        key = str(uuid4())
        data = dict(partner=self.customer.pk, date=str(timezone.localdate()), amount='500', mode='cash', submission_key=key)
        self.assertEqual(self.client.post(reverse('receipt_new'), data).status_code, 302)
        self.assertContains(self.client.post(reverse('receipt_new'), {**data, 'amount': '600'}), 'already submitted with different details')
        self.assertEqual(Receipt.objects.count(), 1)
        self.assert_controls()

    def test_cross_site_packing_batch_and_nonfinite_lab_result_are_rejected(self):
        batch = Production.objects.create(site=self.other, product=self.bulk, created_by=self.maker, planned_quantity=1, actual_quantity=1, batch='QA-OTHER-BATCH', status='Released')
        with self.assertRaisesMessage(ValidationError, 'at this plant'):
            PR.pack(self.maker, self.site, dict(bulk_product=self.bulk, bag_product=self.bag, batch=batch, bags_packed=20, bulk_issued=D(1)))
        batch = Production.objects.create(site=self.site, product=self.bulk, created_by=self.maker, planned_quantity=1, actual_quantity=1, batch='QA-HELD-BATCH', status='Quality hold')
        spec = QualitySpec.objects.create(product=self.bulk, parameter='Finite result', min_value=D(5))
        with self.assertRaises(ValidationError):
            QC.record_result(self.checker, batch.pk, spec, 'NaN')
        self.assertFalse(batch.results.exists())

    def test_released_batch_traces_through_weighbridge_invoice_and_pod(self):
        move_stock(self.maker, self.site, self.raw, D(10), 'qa:trace-raw', 'Opening', unit_cost=100)
        recipe = PR.save_recipe(self.maker, self.bulk, [(self.raw, D(1), D(0))])
        PR.approve_recipe(self.checker, recipe.pk)
        batch = PR.create_order(self.maker, self.site, self.bulk, 10, 'A')
        PR.issue(self.maker, batch.pk, self.raw, 10, 'QA raw lot')
        PR.complete(self.maker, batch.pk, 10)
        spec = QualitySpec.objects.create(product=self.bulk, parameter='Release result', min_value=D(5), required_for_release=True)
        QC.record_result(self.checker, batch.pk, spec, 6)
        QC.release(self.checker, batch.pk)
        self.site.uses_weighbridge = True
        self.site.save()
        sale = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker)
        line = OrderLine.objects.create(order=sale, product=self.bulk, quantity=5, rate=200, gst_rate=18)
        transition_order(self.maker, sale.pk, 'submit'); transition_order(self.checker, sale.pk, 'approve')
        far = timezone.localdate() + timedelta(days=100)
        truck = Vehicle.objects.create(registration='MP04GA1234', vehicle_type='truck10', capacity_mt=25, owner='customer', rc_valid_until=far,
            insurance_valid_until=far, permit_valid_until=far, fitness_valid_until=far, puc_valid_until=far)
        driver = Driver.objects.create(name='Trace driver', mobile='9876543210', licence_number='QA-TRACE-LIC', licence_valid_until=far)
        dispatch = L.plan(self.maker, sale, truck, driver, {line: 5})
        L.gate_in(self.maker, dispatch.pk, {k: True for k, _ in L.GATE_CHECKS})
        L.weigh(self.maker, dispatch.pk, 'tare', 10000, 'scale')
        L.start_loading(self.maker, dispatch.pk)
        dl = dispatch.lines.get()
        L.complete_loading(self.maker, dispatch.pk, {dl.pk: 5}, batches={dl.pk: batch.pk})
        L.weigh(self.maker, dispatch.pk, 'gross', 15000, 'scale')
        invoice = L.invoice(self.maker, dispatch.pk)
        L.gate_out(self.maker, dispatch.pk)
        with self.assertRaises(ValidationError):
            L.gate_out(self.maker, dispatch.pk)
        L.deliver(self.maker, dispatch.pk, 'QA receiver')
        trace = QC.trace(batch)
        self.assertEqual(trace['shipments'][0]['invoice'].pk, invoice.pk)
        self.assertEqual(trace['customers'], [self.customer.name])
        self.assertEqual(self.account('cogs'), 500)
        self.assert_controls()

    def test_future_recipe_does_not_replace_current_recipe_before_its_date(self):
        current = PR.save_recipe(self.maker, self.bulk, [(self.raw, D(1), D(0))])
        PR.approve_recipe(self.checker, current.pk)
        future_day = timezone.localdate() + timedelta(days=10)
        future = PR.save_recipe(self.maker, self.bulk, [(self.raw, D(2), D(0))], effective_from=future_day)
        PR.approve_recipe(self.checker, future.pk)
        self.assertEqual(PR.create_order(self.maker, self.site, self.bulk, 10, 'A').recipe_id, current.pk)
        self.assertEqual(PR.create_order(self.maker, self.site, self.bulk, 10, 'A', planned_date=future_day).recipe_id, future.pk)

    def test_transfer_source_and_destination_authority_are_separate(self):
        from . import stock as ST
        move_stock(self.maker, self.site, self.bag, D(10), 'qa:transfer', 'Opening', unit_cost=100)
        transfer = ST.request_transfer(self.maker, self.site, self.other, 'Other plant requirement', [(self.bag, 5)])
        with self.assertRaises(PermissionDenied):
            ST.decide_transfer(self.outsider, transfer.pk, True)
        ST.decide_transfer(self.checker, transfer.pk, True)
        line = transfer.lines.get()
        ST.dispatch_transfer(self.maker, transfer.pk, {line.pk: 5}, 'MP04GA1234')
        with self.assertRaises(PermissionDenied):
            ST.receive_transfer(self.checker, transfer.pk, {line.pk: 5}, {})
        ST.receive_transfer(self.outsider, transfer.pk, {line.pk: 5}, {})
        self.assertEqual(StockBalance.objects.get(site=self.other, product=self.bag).quantity, 5)
        self.assert_controls()

    def test_closed_period_receipt_failure_is_visible_and_atomic(self):
        from . import closing
        last = timezone.localdate().replace(day=1) - timedelta(days=1)
        closing.prepare_period(self.maker, last, 'Verification cutoff review', tax_reviewed=True, bank_reviewed=True, stock_reviewed=True)
        gl.set_period(self.checker, last, True)
        self.client.force_login(self.maker)
        response = self.client.post(reverse('receipt_new'), dict(partner=self.customer.pk, date=str(last), amount='100', mode='cash', submission_key=str(uuid4())))
        self.assertContains(response, 'closed for posting')
        self.assertFalse(Receipt.objects.exists())
        self.assertFalse(JournalEntry.objects.filter(source__startswith='rct:').exists())

    def test_company_receipts_need_explicit_scope_not_just_view_permission(self):
        viewer = User.objects.create_user('qa-receipt-viewer')
        viewer.user_permissions.add(Permission.objects.get(content_type__app_label='erp', codename='view_receipt'))
        SiteAccess.objects.create(user=viewer, site=self.site)
        self.client.force_login(viewer)
        for route in ['receipts', 'outstanding']:
            self.assertEqual(self.client.get(reverse(route)).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'href="/receipts/"')

    def test_large_quantity_average_rounding_does_not_change_stock_value(self):
        move_stock(self.maker, self.site, self.raw, D(100000), 'qa:precision-1', 'Opening', unit_cost=D('1.0001'))
        move_stock(self.maker, self.site, self.raw, D(100000), 'qa:precision-2', 'Opening', unit_cost=D('1.0002'))
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.raw).value, D('200030.00'))
        self.assert_controls()
        move_stock(self.maker, self.site, self.raw, D(-200000), 'qa:precision-out', 'Consumption')
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.raw).value, 0)
        self.assertEqual(self.account('inventory_rm'), 0)
        self.assert_controls()

    def test_production_packing_and_transfer_preserve_every_paisa(self):
        from . import stock as ST
        move_stock(self.maker, self.site, self.raw, D(100000), 'qa:chain-1', 'Opening', unit_cost=D('1.0001'))
        move_stock(self.maker, self.site, self.raw, D(100000), 'qa:chain-2', 'Opening', unit_cost=D('1.0002'))
        recipe = PR.save_recipe(self.maker, self.bulk, [(self.raw, D(1), D(0))])
        PR.approve_recipe(self.checker, recipe.pk)
        batch = PR.create_order(self.maker, self.site, self.bulk, 200000, 'A')
        issue = PR.issue(self.maker, batch.pk, self.raw, 200000)
        self.assertEqual(issue.value, D('200030.00'))
        PR.complete(self.maker, batch.pk, 200000)
        spec = QualitySpec.objects.create(product=self.bulk, parameter='Precision release', min_value=D(5), required_for_release=True)
        QC.record_result(self.checker, batch.pk, spec, 6); QC.release(self.checker, batch.pk)
        self.assertEqual(self.account('wip'), 0)
        PR.pack(self.maker, self.site, dict(date=timezone.localdate(), shift='A', packer='Precision packer', bulk_product=self.bulk, bag_product=self.bag,
            batch=batch, bulk_issued=D(100000), bags_packed=2000000, bags_burst=0, spillage_kg=D(0), downtime_minutes=0, empty_bags=None))
        self.assertEqual(self.account('wip'), 0)
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.bag).value, D('100015.00'))
        transfer = ST.request_transfer(self.maker, self.site, self.other, 'Precision transfer', [(self.bag, 2000000)])
        ST.decide_transfer(self.checker, transfer.pk, True)
        line = transfer.lines.get()
        ST.dispatch_transfer(self.maker, transfer.pk, {line.pk: 2000000}, 'MP04GA1234')
        ST.receive_transfer(self.outsider, transfer.pk, {line.pk: 1500000}, {line.pk: 250000})
        SiteAccess.objects.create(user=self.checker, site=self.other)
        ST.resolve_transfer(self.checker, transfer.pk, 'Shortage verified for transporter claim')
        self.assertEqual(self.account('stock_transit'), 0)
        self.assertEqual(self.account('transit_loss'), D('12501.88'))
        self.assert_controls()

    def test_full_sales_return_restores_actual_value_not_rounded_unit_cost(self):
        move_stock(self.maker, self.site, self.bag, D(100000), 'qa:return-1', 'Opening', unit_cost=D('1.0001'))
        move_stock(self.maker, self.site, self.bag, D(100000), 'qa:return-2', 'Opening', unit_cost=D('1.0002'))
        sale = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker)
        line = OrderLine.objects.create(order=sale, product=self.bag, quantity=200000, rate=2, gst_rate=18)
        transition_order(self.maker, sale.pk, 'submit'); transition_order(self.checker, sale.pk, 'approve'); transition_order(self.maker, sale.pk, 'post')
        invoice = create_invoice(self.maker, sale, [line], {})
        self.assertEqual(self.account('cogs'), D('200030.00'))
        self.client.force_login(self.maker)
        for _ in range(2):
            response = self.client.post(reverse('credit_note_new', args=[invoice.pk]), {'reason': 'return', f'qty_{invoice.lines.get().pk}': '100000'})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.account('cogs'), 0)
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.bag).value, D('200030.00'))
        self.assert_controls()
