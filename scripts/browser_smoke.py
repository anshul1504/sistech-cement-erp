"""Local visual checks. Run the server first; set ERP_DEMO_PASSWORD in the shell.

Optional tooling: pip install playwright && python -m playwright install chromium
Screenshots go to ignored test-results/. No passwords or reset links are printed.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.contrib.auth.models import Group, User
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from playwright.sync_api import sync_playwright, expect

password = os.environ['ERP_DEMO_PASSWORD']
base = os.environ.get('ERP_BASE_URL', 'http://127.0.0.1:8000')
out = ROOT / 'test-results'
out.mkdir(exist_ok=True)
role_id = Group.objects.get(name='Sales executive').pk
user = User.objects.get(username='operator')
reset = f'/accounts/reset/{urlsafe_base64_encode(force_bytes(user.pk))}/{default_token_generator.make_token(user)}/'

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def capture(name, url):
        response = page.goto(base + url)
        assert response.status == 200, (name, response.status)
        page.evaluate('document.fonts.ready')
        assert page.evaluate('document.fonts.check(\'12px "Poppins"\')')
        page.screenshot(path=str(out / f'auth-{name}-desktop.png'), full_page=True, animations='disabled')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(out / f'auth-{name}-mobile.png'), full_page=True, animations='disabled')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), name
        sidebar = page.locator('#sidebar')
        if sidebar.count():
            assert sidebar.bounding_box()['x'] + sidebar.bounding_box()['width'] <= 1
        page.set_viewport_size({'width': 1440, 'height': 1000})

    for name, url in [
        ('login', '/accounts/login/'),
        ('forgot', '/accounts/forgot-password/'),
        ('sent', '/accounts/forgot-password/sent/'),
        ('new-password', reset),
        ('expired', '/accounts/reset/invalid/invalid/'),
        ('reset-complete', '/accounts/reset/complete/'),
        ('access-help', '/accounts/access-help/'),
    ]:
        capture(name, url)

    page.goto(base + '/accounts/login/')
    page.locator('#id_username').fill('admin')
    page.locator('#id_password').fill(password)
    page.get_by_role('button', name='Show password', exact=True).click()
    expect(page.locator('#id_password')).to_have_attribute('type', 'text')
    page.get_by_role('button', name='Hide password', exact=True).click()
    page.get_by_role('button', name='Sign in to workspace', exact=True).click()
    page.wait_for_url(base + '/')

    for name, url in [
        ('profile', '/accounts/profile/'),
        ('change-password', '/accounts/password/change/'),
        ('password-changed', '/accounts/password/changed/'),
        ('access-control', '/accounts/access/'),
        ('role-matrix', f'/accounts/access/roles/{role_id}/'),
        ('new-member', '/accounts/access/users/new/'),
    ]:
        capture(name, url)

    page.goto(base + '/')
    page.get_by_role('button', name='Sign out', exact=True).click()
    page.wait_for_url(base + '/accounts/signed-out/')
    capture('signed-out', '/accounts/signed-out/')
    assert not errors, errors
    browser.close()
    print('PASS: 14 pages checked at desktop/mobile sizes; Poppins, navigation, password visibility and logout verified.')
