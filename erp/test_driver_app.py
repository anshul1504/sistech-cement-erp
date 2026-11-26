"""Driver app: admin-linked driver logins, own-trip isolation, POD with photo and road exceptions through the API."""
import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.api_auth import issue_token
from accounts.models import ApiToken
from accounts.test_support import force_login_recent
from . import logistics as L
from .models import Site, Product, Partner, Order, OrderLine, StockBalance, Vehicle, Driver, Dispatch, Organization, AuditEvent, DispatchIncident

CHECKS = {k: True for k, _ in L.GATE_CHECKS}
JPEG = b'\xff\xd8\xff\xe0' + b'0' * 200


@override_settings(EMAIL_OTP_ENABLED=False)
class DriverAppTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'gstin': '23ABCDE1234F1Z5'})
        cls.site = Site.objects.create(code='PLT', name='Plant', kind='Plant', state='23', gstin='23ABCDE1234F1Z5', uses_weighbridge=True)
        cls.cement = Product.objects.create(code='OPC', name='OPC', category='Cement', unit='BAG', bag_weight_kg=50, gst_rate=18, hsn='2523')
        cls.dealer = Partner.objects.create(code='D1', name='Dealer One', kind='Dealer', state='23', city='Bhopal', phone='9000000001', credit_limit=10 ** 8)
        far = timezone.localdate() + timedelta(days=100)
        docs = dict(rc_valid_until=far, insurance_valid_until=far, permit_valid_until=far, fitness_valid_until=far, puc_valid_until=far)
        cls.truck = Vehicle.objects.create(registration='MP04GA1', vehicle_type='truck10', capacity_mt=30, **docs)
        cls.truck2 = Vehicle.objects.create(registration='MP04GA2', vehicle_type='truck10', capacity_mt=30, **docs)
        cls.admin = User.objects.create_superuser('dr-admin', 'dra@example.com', 'Original-Password-816!')
        cls.du = User.objects.create_user('dr-user', 'driver@example.com', 'Driver-Pass-2026!')
        cls.ou = User.objects.create_user('dr-other', 'other@example.com', 'Driver-Pass-2026!')
        cls.driver = Driver.objects.create(name='Ramesh', mobile='9876543210', licence_number='MP0420190012345', licence_valid_until=far, user=cls.du, app_enabled=True)
        cls.other = Driver.objects.create(name='Suresh', mobile='9876543211', licence_number='MP0420190012346', licence_valid_until=far, user=cls.ou, app_enabled=True)
        StockBalance.objects.create(site=cls.site, product=cls.cement, quantity=5000)

    def trip(self, truck, driver, qty=100, out=True):
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=self.admin, status='Approved', ship_to='Plot 4, Kolar Road, Bhopal')
        line = OrderLine.objects.create(order=o, product=self.cement, quantity=qty, rate=400, gst_rate=18)
        d = L.plan(self.admin, o, truck, driver, {line: qty})
        if out:
            L.gate_in(self.admin, d.pk, CHECKS); L.weigh(self.admin, d.pk, 'tare', 9000, 'scale'); L.start_loading(self.admin, d.pk)
            L.complete_loading(self.admin, d.pk, {d.lines.get().pk: qty}); L.weigh(self.admin, d.pk, 'gross', 9000 + qty * 50, 'scale')
            L.invoice(self.admin, d.pk); L.gate_out(self.admin, d.pk)
        return Dispatch.objects.get(pk=d.pk)

    def auth(self, user):
        return {'HTTP_AUTHORIZATION': f'Bearer {issue_token(user, "test")}'}

    def test_driver_sees_only_own_trips_and_details(self):
        mine, theirs = self.trip(self.truck, self.driver), self.trip(self.truck2, self.other)
        h = self.auth(self.du)
        me = self.client.get(reverse('api_me'), **h).json()
        self.assertIn('driver', me['apps'])
        self.assertEqual(me['driver']['name'], 'Ramesh')
        rows = self.client.get(reverse('api_driver_trips'), **h).json()['trips']
        self.assertEqual([r['id'] for r in rows], [mine.pk])
        self.assertTrue(rows[0]['can_update'])
        t = self.client.get(reverse('api_driver_trip', args=[mine.pk]), **h).json()['trip']
        self.assertEqual(t['ship_to'], 'Plot 4, Kolar Road, Bhopal')
        self.assertEqual(t['phone'], '9000000001')
        self.assertTrue(t['invoice'])
        self.assertEqual(self.client.get(reverse('api_driver_trip', args=[theirs.pk]), **h).status_code, 404)
        s = self.client.get(reverse('api_driver_summary'), **h).json()
        self.assertEqual(s['on_road'], 1)

    def test_pod_with_photo(self):
        d = self.trip(self.truck, self.driver)
        h = self.auth(self.du)
        url = reverse('api_driver_deliver', args=[d.pk])
        r = self.client.post(url, {'received_by': 'Mohan'}, **h)
        self.assertEqual(r.status_code, 400)
        self.assertIn('photo', r.json()['error'])
        r = self.client.post(url, {'received_by': 'Mohan', 'receiver_mobile': '98765 00000', 'shortage': '2', 'notes': '2 bags torn',
                                   'photo': SimpleUploadedFile('pod.jpg', JPEG, content_type='image/jpeg')}, **h)
        self.assertEqual(r.status_code, 200, r.content)
        d.refresh_from_db()
        self.assertEqual(d.status, 'delivered')
        self.assertEqual(d.pod_recorded_by, self.du)
        self.assertEqual(bytes(d.pod_file), JPEG)
        self.assertTrue(AuditEvent.objects.filter(reference=d.number, action='Delivered (POD)', detail__contains='driver app').exists())
        again = self.client.post(url, {'received_by': 'Mohan', 'photo': SimpleUploadedFile('pod.jpg', JPEG, content_type='image/jpeg')}, **h)
        self.assertEqual(again.status_code, 400)

    def test_bad_photo_and_other_drivers_trip_rejected(self):
        d = self.trip(self.truck, self.driver)
        url = reverse('api_driver_deliver', args=[d.pk])
        bad = self.client.post(url, {'received_by': 'M', 'photo': SimpleUploadedFile('x.jpg', b'not an image', content_type='image/jpeg')}, **self.auth(self.du))
        self.assertEqual(bad.status_code, 400)
        other = self.client.post(url, {'received_by': 'M', 'photo': SimpleUploadedFile('pod.jpg', JPEG, content_type='image/jpeg')}, **self.auth(self.ou))
        self.assertEqual(other.status_code, 404)
        self.assertEqual(Dispatch.objects.get(pk=d.pk).status, 'gate_out')

    def test_trip_at_plant_cannot_be_updated(self):
        d = self.trip(self.truck, self.driver, out=False)
        r = self.client.post(reverse('api_driver_deliver', args=[d.pk]), {'received_by': 'M', 'photo': SimpleUploadedFile('p.jpg', JPEG, content_type='image/jpeg')}, **self.auth(self.du))
        self.assertEqual(r.status_code, 400)
        self.assertIn('not on the road', r.json()['error'])

    def test_incident_reported_and_office_decides(self):
        d = self.trip(self.truck, self.driver)
        h = self.auth(self.du)
        url = reverse('api_driver_incident', args=[d.pk])
        r = self.client.post(url, json.dumps({'kind': 'diversion', 'note': 'Site closed'}), content_type='application/json', **h)
        self.assertEqual(r.status_code, 400)
        r = self.client.post(url, json.dumps({'kind': 'diversion', 'note': 'Site closed', 'location': 'Hoshangabad Rd', 'new_ship_to': 'Godown 2, Misrod'}),
                             content_type='application/json', **h)
        self.assertEqual(r.status_code, 200, r.content)
        x = DispatchIncident.objects.get(dispatch=d)
        self.assertEqual((x.kind, x.status, x.reported_by), ('diversion', 'open', self.du))
        self.assertEqual(r.json()['trip']['incidents'][0]['status'], 'open')
        from . import incidents
        incidents.decide(self.admin, x.pk, True, 'ok')
        self.assertEqual(DispatchIncident.objects.get(pk=x.pk).status, 'approved')

    def test_disabled_link_or_login_blocks_driver_api(self):
        h = self.auth(self.du)
        Driver.objects.filter(pk=self.driver.pk).update(app_enabled=False)
        self.assertEqual(self.client.get(reverse('api_driver_trips'), **h).status_code, 403)
        self.assertNotIn('driver', self.client.get(reverse('api_me'), **h).json()['apps'])
        Driver.objects.filter(pk=self.driver.pk).update(app_enabled=True, active=False)
        self.assertEqual(self.client.get(reverse('api_driver_trips'), **h).status_code, 403)
        # A staff user without a driver link cannot use the driver API either.
        self.assertEqual(self.client.get(reverse('api_driver_trips'), **self.auth(self.admin)).status_code, 403)

    def test_admin_links_driver_with_reason_and_ends_sign_ins(self):
        u = User.objects.create_user('new-driver', 'nd@example.com', 'Driver-Pass-2026!')
        free = Driver.objects.create(name='Mahesh', mobile='9876543212', licence_number='MP0420190012347', licence_valid_until=self.driver.licence_valid_until)
        issue_token(u, 'old phone')
        force_login_recent(self.client, self.admin)
        url = reverse('driver_access', args=[u.pk])
        page = self.client.get(url)
        self.assertContains(page, 'Mahesh')
        self.assertNotContains(page, 'Suresh')  # already linked to another login
        r = self.client.post(url, {'driver': self.other.pk, 'active': 'on', 'reason': 'Steal another link'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Driver.objects.get(pk=self.other.pk).user, self.ou)
        r = self.client.post(url, {'driver': free.pk, 'active': 'on', 'reason': 'New Bhopal driver'})
        self.assertRedirects(r, reverse('member_detail', args=[u.pk]), fetch_redirect_response=False)
        free.refresh_from_db()
        self.assertEqual((free.user, free.app_enabled), (u, True))
        self.assertFalse(ApiToken.objects.filter(user=u, revoked_at__isnull=True).exists())
        self.assertTrue(AuditEvent.objects.filter(action='Driver app access changed', detail__contains='New Bhopal driver').exists())
        self.assertContains(self.client.get(reverse('member_detail', args=[u.pk])), 'Mahesh')

    def test_staff_login_cannot_be_driver(self):
        staff = User.objects.create_user('staff-x', 'sx@example.com', 'Driver-Pass-2026!', is_staff=True)
        free = Driver.objects.create(name='Naresh', mobile='9876543213', licence_number='MP0420190012348', licence_valid_until=self.driver.licence_valid_until)
        force_login_recent(self.client, self.admin)
        r = self.client.post(reverse('driver_access', args=[staff.pk]), {'driver': free.pk, 'active': 'on', 'reason': 'Should not work'})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(Driver.objects.get(pk=free.pk).user)
