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
@override_settings(EMAIL_OTP_ENABLED=True, EMAIL_OTP_SKIP_FOR_USERNAME=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BrowserFlowTests(StaticLiveServerTestCase):
    def test_industrial_theme_and_two_step_login(self):
        from playwright.sync_api import sync_playwright, expect
        password = 'Visual-Test-Password-714!'
        call_command('seed_demo', password=password, stdout=io.StringIO())
        admin = User.objects.get(username='admin')
        admin.email = 'admin@example.com'
        admin.save(update_fields=['email'])
        from erp.models import Site
        from django.utils import timezone
        from datetime import timedelta
        deputy_id = User.objects.get(username='operator').pk
        delegation_site_id = Site.objects.filter(siteaccess__user_id=deputy_id).first().pk
        role_id = Group.objects.get(name='Sales executive').pk
        out = settings.BASE_DIR / 'test-results'
        out.mkdir(exist_ok=True)
        from concurrent.futures import ThreadPoolExecutor
        executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(executor.shutdown)
        def db_call(fn):
            return executor.submit(fn).result()
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
            page.get_by_role('button', name='Sign in to Sistech ERP', exact=True).click()
            page.wait_for_url(self.live_server_url + '/accounts/otp/verify/')
            capture('otp', '/accounts/otp/verify/')
            code = re.search(r'code is: (\d{6})', mail.outbox[-1].body).group(1)
            page.locator('#id_code').fill(code)
            # Completing six digits auto-submits; wait for the authenticated redirect.
            page.wait_for_url(self.live_server_url + '/')
            for name, path in [
                ('dashboard', '/'), ('sales', '/sales/'), ('inventory', '/inventory/'),
                ('production', '/production/'), ('quality', '/quality/'),
                ('access', '/accounts/access/'), ('roles', f'/accounts/access/roles/{role_id}/'),
                ('delegations', '/accounts/access/delegations/'), ('delegation-new', '/accounts/access/delegations/new/'),
                ('member', '/accounts/access/users/new/'), ('profile', '/accounts/profile/'),
                ('password', '/accounts/password/change/'),
            ]:
                capture(name, path)
                if name == 'member':
                    expect(page.get_by_label('Access expires at', exact=True)).to_be_visible()
                    expect(page.locator('#id_access_expires_at')).to_have_attribute('type', 'datetime-local')
            # Expire the server-side proof, keeping the real password+OTP session.
            from django.contrib.sessions.backends.db import SessionStore
            from accounts.reauth import SESSION_KEY, TTL_SECONDS
            cookie = next(c for c in page.context.cookies() if c['name'] == settings.SESSION_COOKIE_NAME)
            def expire_confirmation():
                session = SessionStore(session_key=cookie['value'])
                proof = session[SESSION_KEY]
                proof['at'] -= TTL_SECONDS + 1
                session[SESSION_KEY] = proof
                session.save()
            db_call(expire_confirmation)
            page.goto(self.live_server_url + '/accounts/access/roles/new/')
            page.locator('#role-name').fill('Confirmed browser role')
            page.get_by_role('button', name='Create role', exact=True).click()
            page.wait_for_url(re.compile(r'.*/accounts/confirm-identity/.*'))
            self.assertFalse(db_call(lambda: Group.objects.filter(name='Confirmed browser role').exists()))
            for width in (320, 390, 768, 1440):
                page.set_viewport_size({'width': width, 'height': 900})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'confirmation {width}')
                self.assertIn('Poppins', page.locator('body').evaluate('(el) => getComputedStyle(el).fontFamily'))
            page.locator('#id_password').fill(password)
            page.get_by_role('button', name='Confirm identity', exact=True).click()
            page.wait_for_url(self.live_server_url + '/accounts/access/roles/new/')
            self.assertFalse(db_call(lambda: Group.objects.filter(name='Confirmed browser role').exists()))
            page.locator('#role-name').fill('Confirmed browser role')
            page.get_by_role('button', name='Create role', exact=True).click()
            page.wait_for_url(re.compile(r'.*/accounts/access/roles/\d+/'))
            self.assertTrue(db_call(lambda: Group.objects.filter(name='Confirmed browser role').exists()))
            page.goto(self.live_server_url + '/accounts/access/delegations/new/')
            page.locator('#id_principal').select_option(str(admin.pk))
            page.locator('#id_deputy').select_option(str(deputy_id))
            page.locator('#id_permission').select_option('approve_sales_order')
            page.locator('#id_site').select_option(str(delegation_site_id))
            page.locator('#id_ends_at').fill((timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'))
            page.locator('#id_reason').fill('Planned browser verification cover')
            page.get_by_role('button', name='Record delegation', exact=True).click()
            page.wait_for_url(re.compile(r'.*/accounts/access/delegations/\d+/'))
            delegation_path = page.url.removeprefix(self.live_server_url)
            for width in (320, 390, 768, 1440):
                page.set_viewport_size({'width': width, 'height': 900})
                for path in ('/accounts/access/delegations/', '/accounts/access/delegations/new/', delegation_path):
                    response = page.goto(self.live_server_url + path)
                    self.assertEqual(response.status, 200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'delegation {path} {width}')
                    ids = page.locator('[id]').evaluate_all('(els) => els.map(el => el.id)')
                    self.assertEqual(len(ids), len(set(ids)))
            page.goto(self.live_server_url + delegation_path)
            page.locator('#revoke-reason').fill('Verification cover finished')
            page.get_by_role('button', name='Revoke delegation', exact=True).click()
            page.wait_for_load_state('networkidle')
            expect(page.get_by_text('Delegation revoked; earlier approvals remain in history.', exact=True)).to_be_visible()
            page.goto(self.live_server_url + '/')
            page.get_by_role('button', name='Account menu', exact=True).click()
            page.get_by_role('button', name='Sign out', exact=True).click()
            page.wait_for_url(self.live_server_url + '/accounts/signed-out/')
            self.assertFalse(errors)
            browser.close()
