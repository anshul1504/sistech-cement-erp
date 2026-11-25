"""Channel network: C&F → promoter/agent → dealer → retailer → mason. Level rules, audit, tree, rollups, permissions."""
from decimal import Decimal

from django.contrib.auth.models import User, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import network as net
from .models import Partner, Influencer, AuditEvent
from .test_projects import seed_projects
from .test_schemes import invoice_fixture


@override_settings(EMAIL_OTP_ENABLED=False)
class NetworkTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.customer.kind = 'Dealer'
        self.customer.save()
        self.admin = User.objects.create_superuser('net-admin', 'a@example.test', 'x')
        P = lambda code, kind: Partner.objects.create(code=code, name=code.title(), kind=kind)
        self.cf, self.promoter, self.retailer = P('CF-ONE', 'CF'), P('SP-ONE', 'SalesPromoter'), P('RT-ONE', 'Retailer')
        self.other_dealer = P('DL-TWO', 'Dealer')

    def link(self, child, parent):
        return net.set_parent(self.admin, child, parent)

    def test_level_rules(self):
        self.link(self.promoter, self.cf)
        self.link(self.customer, self.promoter)
        self.link(self.retailer, self.customer)
        self.assertEqual(net.chain(Partner.objects.get(pk=self.retailer.pk)), [self.customer, self.promoter, self.cf])
        self.assertEqual(net.descendant_ids(self.cf), {self.promoter.pk, self.customer.pk, self.retailer.pk})
        bad = [(self.cf, self.promoter), (self.retailer, self.cf), (self.customer, self.other_dealer), (self.promoter, self.customer), (self.customer, self.customer)]
        for child, parent in bad:
            with self.assertRaises(ValidationError, msg=f'{child.kind} under {parent.kind}'):
                self.link(child, parent)
        self.cf.active = False
        self.cf.save()
        with self.assertRaises(ValidationError):
            self.link(self.other_dealer, self.cf)

    def test_change_is_audited_and_needs_permission(self):
        self.link(self.customer, self.cf)
        ev = AuditEvent.objects.get(action='Upline changed', reference=f'partner:{self.customer.pk}')
        self.assertIn('company direct → Cf-One', ev.detail)
        viewer = User.objects.create_user('viewer')
        viewer.user_permissions.add(Permission.objects.get(codename='view_partner'))
        with self.assertRaises(PermissionDenied):
            net.set_parent(User.objects.get(pk=viewer.pk), self.customer, None)
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse('partner_upline', args=[self.customer.pk]), {'parent': ''}).status_code, 403)
        self.assertEqual(Partner.objects.get(pk=self.customer.pk).parent, self.cf)

    def test_upline_view_and_validation_message(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('partner_upline', args=[self.retailer.pk]), {'parent': self.cf.pk}, follow=True)
        self.assertContains(r, 'can sit under Dealer / Distributor')
        self.assertIsNone(Partner.objects.get(pk=self.retailer.pk).parent)
        self.client.post(reverse('partner_upline', args=[self.retailer.pk]), {'parent': self.customer.pk, 'note': 'Shifted to new dealer'})
        self.assertEqual(Partner.objects.get(pk=self.retailer.pk).parent, self.customer)
        self.assertTrue(AuditEvent.objects.filter(detail__contains='Shifted to new dealer').exists())
        self.client.post(reverse('partner_upline', args=[self.retailer.pk]), {'parent': ''})
        self.assertIsNone(Partner.objects.get(pk=self.retailer.pk).parent)

    def test_people_page_tree_and_gaps(self):
        self.link(self.promoter, self.cf)
        self.link(self.customer, self.promoter)
        self.link(self.retailer, self.customer)
        Influencer.objects.create(code='M1', kind='Mason', name='Ramesh Mason', mobile='9000000001', district='Bhopal', primary_dealer=self.customer)
        Influencer.objects.create(code='M2', kind='Mason', name='Loose Mason', mobile='9000000002', district='Bhopal')
        self.client.force_login(self.admin)
        r = self.client.get(reverse('people'))
        for text in ['C&amp;F', 'Sales Promoter / Handling Agent / Transporter', 'Retailer', 'Mason', 'Cf-One', 'Sp-One', 'Directly with the company', 'Dl-Two',
                     '1 deal with the company directly', '1 not linked to a dealer or retailer', '1 retailer', '1 mason']:
            self.assertContains(r, text)
        found = self.client.get(reverse('people'), {'q': 'dl-two'})
        self.assertContains(found, 'Dl-Two')
        self.assertNotContains(found, 'Sp-One')
        lst = self.client.get(reverse('partners'), {'upline': self.customer.pk})
        self.assertContains(lst, 'Rt-One')
        self.assertContains(lst, f'Under {self.customer.name}')
        gaps = self.client.get(reverse('partners'), {'people_group': 'Dealer', 'upline': 'none'})
        self.assertContains(gaps, 'Dl-Two')
        self.assertNotContains(gaps, self.customer.name + '</strong>')

    def test_partner_card_rolls_up_network_sales(self):
        self.link(self.customer, self.cf)
        invoice_fixture(self, [(self.bag, Decimal('1000'))])   # invoice to self.customer
        self.client.force_login(self.admin)
        r = self.client.get(reverse('partner_detail', args=[self.cf.pk]))
        self.assertContains(r, 'Channel network')
        self.assertContains(r, '1 Dealer')
        card = net.network_card(self.admin, self.cf)
        self.assertGreater(card['sales']['network']['value'], 0)
        self.assertEqual(card['sales']['total_value'], card['sales']['network']['value'])
        dealer_page = self.client.get(reverse('partner_detail', args=[self.customer.pk]))
        self.assertContains(dealer_page, 'Move under')
        self.assertContains(dealer_page, 'Cf-One')

    def test_used_upline_cannot_be_deleted_and_type_change_clears_invalid_upline(self):
        from .partner_views import is_used
        self.link(self.promoter, self.cf)
        self.assertTrue(is_used(self.cf))
        self.promoter.kind = 'Supplier'
        self.promoter.save()
        net.clear_if_invalid(self.admin, Partner.objects.get(pk=self.promoter.pk))
        self.assertIsNone(Partner.objects.get(pk=self.promoter.pk).parent)
