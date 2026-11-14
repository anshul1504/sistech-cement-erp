"""Opt-in cross-module visual/interaction QA, using a disposable database."""
import importlib.util
import os
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from . import inbound as IB, payables as AP, production as PR, quality as QC, stock as ST
from .invoice_views import create_invoice
from .models import Order, OrderLine, QualitySpec, Receipt, JournalEntry
from .services import transition_order
from .test_support import seed_business_data


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Opt-in cross-module browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class BusinessBrowserTests(StaticLiveServerTestCase):
    def test_core_screens_and_receipt_retry_at_multiple_sizes(self):
        from playwright.sync_api import sync_playwright
        f = SimpleNamespace()
        seed_business_data(f)
        today = timezone.localdate()
        po = Order.objects.create(site=f.site, partner=f.supplier, kind='Purchase', created_by=f.maker)
        pol = OrderLine.objects.create(order=po, product=f.raw, quantity=100, rate=100, gst_rate=5)
        transition_order(f.maker, po.pk, 'submit'); transition_order(f.checker, po.pk, 'approve')
        grn = IB.gate_in(f.maker, po, dict(vehicle_number='MP04GA1234', challan_number='VISUAL-GRN'), {pol: D(100)})
        row = grn.lines.get()
        IB.post(f.maker, grn.pk, {row: dict(received=D(100), accepted=D(100), rejected=D(0), damaged=D(0), hold=D(0), reason='')})
        row.refresh_from_db()
        sinv = AP.create_invoice(f.maker, f.supplier, dict(invoice_number='VISUAL-SINV', invoice_date=today, supplier_gstin=f.supplier.gstin,
            billed_taxable=D(10000), billed_tax=D(500), billed_total=D(10500)), {row: (D(100), D(100), D(5))})
        recipe = PR.save_recipe(f.maker, f.bulk, [(f.raw, D(1), D(0))])
        PR.approve_recipe(f.checker, recipe.pk)
        batch = PR.create_order(f.maker, f.site, f.bulk, 100, 'A')
        PR.issue(f.maker, batch.pk, f.raw, 100, 'Visual lot', grn)
        PR.complete(f.maker, batch.pk, 100)
        spec = QualitySpec.objects.create(product=f.bulk, parameter='Release result', min_value=D(5), required_for_release=True)
        QC.record_result(f.checker, batch.pk, spec, 6); QC.release(f.checker, batch.pk)
        PR.pack(f.maker, f.site, dict(date=today, shift='A', packer='QA Packer', bulk_product=f.bulk, bag_product=f.bag, batch=batch,
            bulk_issued=D(5), bags_packed=100, bags_burst=0, spillage_kg=D(0), downtime_minutes=0, empty_bags=None, difference_reason=''))
        sale = Order.objects.create(site=f.site, partner=f.customer, kind='Sale', created_by=f.maker)
        line = OrderLine.objects.create(order=sale, product=f.bag, quantity=10, rate=10, gst_rate=18)
        transition_order(f.maker, sale.pk, 'submit'); transition_order(f.checker, sale.pk, 'approve'); transition_order(f.maker, sale.pk, 'post')
        invoice = create_invoice(f.maker, sale, [line], {})
        count = ST.start_count(f.maker, f.site)
        transfer = ST.request_transfer(f.maker, f.site, f.other, 'Verification transfer', [(f.bag, 5)])
        damage = ST.report_damage(f.maker, f.site, f.bag, 2, 'torn', 'Bay A', 'QA store')
        routes = {name: reverse(name) for name in ['dashboard', 'sales', 'purchases', 'inbound', 'production', 'quality', 'inventory', 'dispatch_board',
            'receipts', 'receipt_new', 'outstanding', 'credit_notes', 'supplier_invoices', 'debit_notes', 'products', 'partners', 'sites', 'recipes',
            'shift_logs', 'packing', 'transfers', 'damages', 'counts', 'freight_bills', 'freight_rates', 'rfqs', 'purchase_requests', 'reports', 'audit']}
        routes.update({name: reverse(name, args=args) for name, args in [
            ('grn_detail', [grn.pk]), ('sinv_detail', [sinv.pk]), ('production_detail', [batch.pk]), ('trace', [batch.pk]), ('coa', [batch.pk]),
            ('invoice_detail', [invoice.pk]), ('credit_note_new', [invoice.pk]), ('partner_ledger', [f.customer.pk]), ('order_detail', [sale.pk]),
            ('stock_card', [f.site.pk, f.bag.pk]), ('count_detail', [count.pk]), ('transfer_detail', [transfer.pk]), ('damage_detail', [damage.pk])
        ]})
        self.client.force_login(f.maker)
        out = Path(settings.BASE_DIR) / 'test-results' / 'business'
        out.mkdir(parents=True, exist_ok=True)
        before = Receipt.objects.count()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME, value=self.client.cookies[settings.SESSION_COOKIE_NAME].value, url=self.live_server_url)])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for size, width, height in [('small-phone', 320, 720), ('phone', 390, 844), ('tablet', 768, 1024), ('desktop', 1440, 900), ('desktop-collapsed', 1470, 834)]:
                page.set_viewport_size(dict(width=width, height=height))
                for name, path in routes.items():
                    with self.subTest(size=size, route=name):
                        response = page.goto(self.live_server_url + path)
                        self.assertEqual(response.status, 200)
                        page.evaluate('document.fonts.ready')
                        self.assertIn('Poppins', page.evaluate('getComputedStyle(document.body).fontFamily'))
                        if size.endswith('collapsed') and not page.locator('html').evaluate("el => el.classList.contains('sb-collapsed')"):
                            page.get_by_role('button', name='Collapse sidebar', exact=True).click()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name} overflows at {width}px')
                        page.screenshot(path=str(out / f'{name}-{size}.png'), full_page=True, animations='disabled')
            page.goto(self.live_server_url + reverse('receipt_new') + f'?partner={f.customer.pk}')
            page.locator('[name=mode]').select_option('cash')
            page.locator('[name=amount]').fill('118')
            payload = page.locator('[data-receipt-form]').evaluate('(el) => Object.fromEntries(new FormData(el))')
            page.get_by_role('button', name='Post receipt', exact=True).click()
            page.wait_for_url('**/receipts/*/')
            original_url = page.url
            response = context.request.post(self.live_server_url + reverse('receipt_new'), form=payload)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.url, original_url)
            self.assertEqual(errors, [])
            browser.close()
        self.assertEqual(Receipt.objects.count(), before + 1)
        self.assertEqual(JournalEntry.objects.filter(source__startswith='rct:').count(), 1)
