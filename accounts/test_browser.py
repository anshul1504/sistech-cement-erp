"""Opt-in browser QA on an isolated test DB with in-memory email delivery."""
import importlib.util
import io
import os
import re
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.contrib.auth.models import User, Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core import mail
from django.core.management import call_command
from django.test import override_settings

@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Opt-in Playwright visual QA')
@override_settings(EMAIL_OTP_ENABLED=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BrowserFlowTests(StaticLiveServerTestCase):
    def test_industrial_theme_and_two_step_login(self):
        from playwright.sync_api import sync_playwright, expect
        password = 'Visual-Test-Password-714!'
        call_command('seed_demo', password=password, stdout=io.StringIO())
        admin = User.objects.get(username='admin')
        admin.email = 'admin@example.com'
        admin.save(update_fields=['email'])
        role_id = Group.objects.get(name='Sales executive').pk
        out = settings.BASE_DIR / 'test-results'
        out.mkdir(exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def capture(name, path):
                response = page.goto(self.live_server_url + path)
                self.assertEqual(response.status, 200)
                page.evaluate('document.fonts.ready')
                page.screenshot(path=str(out / f'industrial-{name}-desktop.png'), full_page=True, animations='disabled')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.screenshot(path=str(out / f'industrial-{name}-mobile.png'), full_page=True, animations='disabled')
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name)
                page.set_viewport_size({'width': 1440, 'height': 1000})

            capture('login', '/accounts/login/')
            capture('forgot', '/accounts/forgot-password/')
            page.goto(self.live_server_url + '/accounts/login/')
            page.locator('#id_username').fill('admin')
            page.locator('#id_password').fill(password)
            page.get_by_role('button', name='Sign in to workspace', exact=True).click()
            page.wait_for_url(self.live_server_url + '/accounts/otp/verify/')
            capture('otp', '/accounts/otp/verify/')
            code = re.search(r'code is: (\d{6})', mail.outbox[-1].body).group(1)
            page.locator('#id_code').fill(code)
            page.get_by_role('button', name='Verify & sign in', exact=True).click()
            page.wait_for_url(self.live_server_url + '/')
            for name, path in [
                ('dashboard', '/'), ('sales', '/sales/'), ('inventory', '/inventory/'),
                ('production', '/production/'), ('quality', '/quality/'),
                ('access', '/accounts/access/'), ('roles', f'/accounts/access/roles/{role_id}/'),
                ('member', '/accounts/access/users/new/'), ('profile', '/accounts/profile/'),
                ('password', '/accounts/password/change/'),
            ]:
                capture(name, path)
            page.goto(self.live_server_url + '/')
            page.get_by_role('button', name='Sign out', exact=True).click()
            page.wait_for_url(self.live_server_url + '/accounts/signed-out/')
            self.assertFalse(errors)
            browser.close()
