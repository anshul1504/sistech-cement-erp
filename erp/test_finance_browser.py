"""Opt-in finance screen QA using an isolated database and disposable accounts."""
import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from . import gl, treasury as TR
from .models import Partner, Site


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Opt-in finance browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class FinanceBrowserTests(StaticLiveServerTestCase):
    def test_finance_pages_at_phone_tablet_and_desktop_sizes(self):
        from playwright.sync_api import sync_playwright
        user = User.objects.create_superuser('finance-browser', 'finance@example.com', 'Browser-Test-Password-816!')
        site = Site.objects.create(code='FIN', name='Finance test plant', kind='Plant')
        supplier = Partner.objects.create(code='S-FIN', name='Finance test supplier', kind='Supplier', bank_account_number='1234567890', bank_ifsc='SBIN0000001')
        gl.ensure_chart()
        bank = gl.acct('bank_main')
        today = timezone.localdate()
        asset = TR.capitalise(user, dict(code='MILL-001', name='Grinding mill test asset', category='fa_plant', site=site, supplier=supplier,
                                        put_to_use=today, cost=Decimal(120000), salvage=Decimal(0), method='slm', life_years=Decimal(10)), 'ap')
        pay = TR.propose(user, supplier, bank, {('asset', asset.pk): Decimal(120000)})
        entry = gl.post(user, 'browser:capital', today, site, 'Capital deposit for browser verification',
                        [(bank, Decimal(1000), 0, None, 'TEST-REF'), (gl.acct('capital'), 0, Decimal(1000), None, '')])
        TR.import_statement(user, bank, f'{today},Capital,TEST-REF,,1000,1000\n{today},Bank charges,,10,,990\n')
        TR.auto_match(user, bank)
        self.client.force_login(user)
        routes = {route: reverse(route) for route in ['journals', 'voucher_new', 'trial_balance', 'profit_loss', 'balance_sheet', 'chart', 'periods',
                                                      'payments', 'bank_rec', 'gst_summary', 'fixed_assets', 'cash_flow']}
        routes['journals_empty'] = reverse('journals') + '?q=no-matching-test-voucher'
        routes.update(journal_detail=reverse('journal_detail', args=[entry.pk]), account_ledger=reverse('account_ledger', args=[bank.pk]),
                      payment_new=reverse('payment_new', args=[supplier.pk]), payment_detail=reverse('payment_detail', args=[pay.pk]))
        output = Path(settings.BASE_DIR) / 'test-results' / 'finance'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': self.live_server_url}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for size, width, height in [('small-phone', 320, 720), ('phone', 390, 844), ('tablet', 768, 1024), ('desktop', 1440, 900)]:
                page.set_viewport_size({'width': width, 'height': height})
                for name, path in routes.items():
                    with self.subTest(size=size, route=name):
                        response = page.goto(self.live_server_url + path)
                        self.assertEqual(response.status, 200)
                        page.evaluate('document.fonts.ready')
                        nav = page.locator('[data-section-navigation]')
                        if nav.count():
                            if width < 768:
                                self.assertFalse(nav.evaluate('(el) => el.open'))
                                nav.locator('summary').click()
                            self.assertTrue(page.locator('.acc-tabs').evaluate('(el) => el.scrollWidth <= el.clientWidth'), 'Finance links are clipped')
                            self.assertTrue(page.get_by_role('link', name='Fixed assets', exact=True).is_visible())
                            if width < 768:
                                nav.locator('summary').click()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name} overflows at {width}px')
                        page.screenshot(path=str(output / f'{name}-{size}.png'), full_page=True, animations='disabled')
            self.assertEqual(errors, [])
            browser.close()
