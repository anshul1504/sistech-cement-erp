"""Mobile API v1: sign-in (password, then email code when OTP applies), bearer tokens, dealer isolation, sales endpoints."""
import json
import re
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import ApiToken
from .models import DealerLedgerAccess, DealerOrderRequest, Partner, PriceRule, SiteAccess
from .test_projects import seed_projects
from .test_schemes import invoice_fixture

PASS = 'Mobile-Api-Pass-2026!'


def post(client, url, data, token=None):
    kw = {'HTTP_AUTHORIZATION': f'Bearer {token}'} if token else {}
    return client.post(url, json.dumps(data), content_type='application/json', **kw)


def get(client, url, token, **params):
    return client.get(url, params, HTTP_AUTHORIZATION=f'Bearer {token}')


@override_settings(EMAIL_OTP_ENABLED=False)
class ApiTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.customer.kind = 'Dealer'
        self.customer.save()
        self.bag.category = 'Cement'
        self.bag.save()
        PriceRule.objects.get_or_create(product=self.bag, partner=None, site=None, partner_kind='', district='', min_quantity=0,
                                        defaults=dict(rate=D('350'), valid_from=self.today - timedelta(days=5), active=True, created_by=self.maker))
        self.dealer_user = User.objects.create_user('api-dealer', 'dealer@sistech-qa.in', PASS)
        DealerLedgerAccess.objects.create(user=self.dealer_user, partner=self.customer)
        self.other = Partner.objects.create(code='API-OTHER', name='Other Dealer', kind='Dealer')
        invoice_fixture(self, [(self.bag, D('1000'))])

    def token(self, username='api-dealer'):
        r = post(self.client, reverse('api_login'), {'username': username, 'password': PASS, 'device': 'Pixel QA'})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['token']

    def test_login_token_and_logout(self):
        bad = post(self.client, reverse('api_login'), {'username': 'api-dealer', 'password': 'wrong'})
        self.assertEqual(bad.status_code, 401)
        t = self.token()
        self.assertTrue(t.startswith('sst_'))
        self.assertFalse(ApiToken.objects.filter(key_hash=t).exists())        # only the hash is stored
        me = get(self.client, reverse('api_me'), t).json()
        self.assertEqual(me['apps'], ['dealer'])
        self.assertEqual(me['dealer']['code'], self.customer.code)
        self.assertEqual(self.client.get(reverse('api_me')).status_code, 401)  # no token
        post(self.client, reverse('api_logout'), {}, t)
        self.assertEqual(get(self.client, reverse('api_me'), t).status_code, 401)

    def test_password_change_kills_token(self):
        t = self.token()
        self.dealer_user.set_password('Another-Pass-2026!x')
        self.dealer_user.save()
        self.assertEqual(get(self.client, reverse('api_me'), t).status_code, 401)

    def test_dealer_endpoints_only_show_own_account(self):
        t = self.token()
        summary = get(self.client, reverse('api_dealer_summary'), t).json()
        self.assertEqual(summary['dealer']['code'], self.customer.code)
        invs = get(self.client, reverse('api_dealer_invoices'), t).json()['invoices']
        self.assertEqual(len(invs), 1)
        ledger = get(self.client, reverse('api_dealer_ledger'), t).json()
        self.assertTrue(ledger['rows'])
        prods = get(self.client, reverse('api_dealer_products'), t).json()['products']
        self.assertEqual(prods[0]['rate'], '350.00')
        r = post(self.client, reverse('api_dealer_orders'), {'required_by': str(self.today + timedelta(days=2)), 'lines': [{'product': self.bag.pk, 'quantity': '20'}]}, t)
        self.assertTrue(r.json()['ok'], r.content)
        self.assertEqual(DealerOrderRequest.objects.get().partner, self.customer)
        foreign = DealerOrderRequest.objects.create(number='F-1', partner=self.other, requested_by=self.maker, required_by=self.today)
        self.assertEqual(post(self.client, reverse('api_dealer_order_cancel', args=[foreign.pk]), {}, t).status_code, 400)
        self.assertEqual(get(self.client, reverse('api_sales_summary'), t).status_code, 403)   # dealer is not sales staff

    def test_sales_endpoints(self):
        rep = User.objects.create_user('api-rep', 'rep@sistech-qa.in', PASS)
        rep.user_permissions.set(Permission.objects.filter(codename__in=['create_sales_order', 'view_sales_order']))
        SiteAccess.objects.create(user=rep, site=self.site)
        t = self.token('api-rep')
        self.assertEqual(get(self.client, reverse('api_me'), t).json()['apps'], ['sales'])
        self.assertTrue(get(self.client, reverse('api_sales_summary'), t).json()['ok'])
        found = get(self.client, reverse('api_sales_customers'), t, q=self.customer.name[:5]).json()['customers']
        self.assertEqual(found[0]['id'], self.customer.pk)
        self.assertTrue(get(self.client, reverse('api_sales_orders'), t).json()['ok'])
        self.assertEqual(get(self.client, reverse('api_dealer_summary'), t).status_code, 403)


@override_settings(EMAIL_OTP_ENABLED=True, EMAIL_OTP_SKIP_FOR_USERNAME=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ApiOtpTests(TestCase):
    def test_password_then_email_code(self):
        User.objects.create_user('otp-user', 'otp.user@sistech-qa.in', PASS)
        r = post(self.client, reverse('api_login'), {'username': 'otp-user', 'password': PASS})
        self.assertTrue(r.json()['otp_required'])
        self.assertNotIn('token', r.json())
        code = re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)
        self.assertEqual(post(self.client, reverse('api_verify'), {'code': '000000' if code != '000000' else '111111'}).status_code, 401)
        ok = post(self.client, reverse('api_verify'), {'code': code})
        self.assertTrue(ok.json()['token'].startswith('sst_'))
