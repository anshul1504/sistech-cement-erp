from django.contrib.auth.models import User, Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from erp.models import AuditEvent
from .models import UserProfile, EmergencyContact

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
PASSWORD = 'Original-Password-816!'


@override_settings(EMAIL_OTP_ENABLED=True, EMAIL_OTP_SKIP_FOR_USERNAME=True)
class MyAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('acc-user', 'acc.user@example.com', PASSWORD, first_name='Ravi')
        cls.other = User.objects.create_user('acc-other', 'acc.other@example.com', PASSWORD)
        group = Group.objects.create(name='Acc sales')
        group.permissions.add(*Permission.objects.filter(codename__in=['view_sales_order', 'create_sales_order']))
        cls.user.groups.add(group)

    def sign_in(self, client, agent='Mozilla/5.0 (Windows NT 10.0) Chrome/120.0'):
        client.post(reverse('login'), {'username': 'acc-user', 'password': PASSWORD}, HTTP_USER_AGENT=agent)

    def test_overview_shows_access_summary_and_two_step_status(self):
        self.sign_in(self.client)
        page = self.client.get(reverse('profile'))
        self.assertEqual(page.context['access'], [{'module': 'Sales orders', 'actions': ['View', 'Create']}])
        self.assertEqual(page.context['two_step']['level'], 'partial')
        self.assertContains(page, 'ac•••@example.com')

    def test_edit_profile_and_photo(self):
        self.sign_in(self.client)
        response = self.client.post(reverse('profile_edit'), {'first_name': 'Ravi', 'last_name': 'Sharma', 'phone': '+91 98765 43210',
                                                              'designation': 'Plant manager', 'department': 'Production',
                                                              'photo': SimpleUploadedFile('me.png', PNG, 'image/png')})
        self.assertRedirects(response, reverse('profile'))
        self.user.refresh_from_db()
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual((self.user.last_name, profile.designation, profile.photo_type), ('Sharma', 'Plant manager', 'image/png'))
        page = self.client.get(reverse('profile'))
        self.assertContains(page, f'/accounts/profile/photo/{self.user.pk}/?v=')
        self.assertEqual(self.client.get(reverse('profile_photo', args=[self.user.pk]))['Content-Type'], 'image/png')
        self.client.post(reverse('profile_edit'), {'first_name': 'Ravi', 'remove_photo': 'on'})
        self.assertEqual(UserProfile.objects.get(user=self.user).photo_type, '')
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action='Profile updated').exists())

    def test_profile_validation_and_email_not_editable(self):
        self.sign_in(self.client)
        response = self.client.post(reverse('profile_edit'), {'first_name': ' ', 'phone': 'abc', 'email': 'hacker@example.com',
                                                              'photo': SimpleUploadedFile('x.svg', b'<svg/>', 'image/svg+xml')})
        for message in ['valid mobile number', 'Upload a PNG, JPG or WebP image']:
            self.assertContains(response, message)
        self.client.post(reverse('profile_edit'), {'first_name': 'Ravi', 'email': 'hacker@example.com'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'acc.user@example.com')

    def test_security_lists_devices_and_signs_out_one(self):
        laptop, phone = Client(), Client()
        self.sign_in(laptop, 'Mozilla/5.0 (Macintosh; Mac OS X) Chrome/120.0')
        self.sign_in(phone, 'Mozilla/5.0 (Linux; Android 14) Chrome/120.0')
        self.sign_in(self.client)
        page = self.client.get(reverse('account_security'))
        sessions = page.context['sessions']
        self.assertEqual(len(sessions), 3)
        self.assertTrue(sessions[0]['current'])
        self.assertContains(page, 'Chrome on Android')
        self.assertNotContains(page, self.client.session.session_key)
        android = next(s for s in sessions if 'Android' in s['client'])
        self.client.post(reverse('session_revoke', args=[android['id']]))
        self.assertEqual(phone.get(reverse('profile')).status_code, 302)
        self.assertEqual(laptop.get(reverse('profile')).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action='Session signed out').exists())

    def test_cannot_sign_out_current_or_someone_elses_session(self):
        from .account_views import session_id
        intruder = Client(); intruder.post(reverse('login'), {'username': 'acc-other', 'password': PASSWORD})
        self.sign_in(self.client)
        for key in [self.client.session.session_key, intruder.session.session_key]:
            self.client.post(reverse('session_revoke', args=[session_id(key)]))
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)
        self.assertEqual(intruder.get(reverse('profile')).status_code, 200)

    def test_password_change_records_date(self):
        self.sign_in(self.client)
        response = self.client.post(reverse('account_security'), {'old_password': PASSWORD, 'new_password1': 'Changed-Password-619!', 'new_password2': 'Changed-Password-619!'})
        self.assertRedirects(response, reverse('account_security'))
        self.assertEqual(UserProfile.objects.get(user=self.user).password_age_days, 0)

    def test_activity_filters_and_pagination(self):
        self.sign_in(self.client)
        for i in range(25):
            AuditEvent.objects.create(actor=self.user, action='Order created', reference=f'o:{i}')
        page = self.client.get(reverse('account_activity') + '?kind=work')
        self.assertEqual(page.context['page'].paginator.count, 25)
        self.assertEqual(len(page.context['page'].object_list), 20)
        security = self.client.get(reverse('account_activity') + '?kind=security')
        self.assertTrue(all(e.action != 'Order created' for e in security.context['page'].object_list))


@override_settings(EMAIL_OTP_ENABLED=False)
class EmergencyContactTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('ec-user', 'ec.user@example.com', PASSWORD)
        cls.other = User.objects.create_user('ec-other', 'ec.other@example.com', PASSWORD)

    def add(self, name, **extra):
        return self.client.post(reverse('contact_new'), {'name': name, 'relationship': 'spouse', 'phone': '+91 98765 43210', 'alt_phone': '', 'address': '', **extra})

    def test_crud_and_primary_rules(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.add('Asha'), reverse('account_contacts'))
        asha = EmergencyContact.objects.get(name='Asha')
        self.assertTrue(asha.is_primary)  # first contact becomes primary
        self.add('Vikram', is_primary='on')
        asha.refresh_from_db()
        self.assertFalse(asha.is_primary)
        vikram = EmergencyContact.objects.get(name='Vikram')
        self.client.post(reverse('contact_edit', args=[vikram.pk]), {'name': 'Vikram S', 'relationship': 'sibling', 'phone': '9876543210', 'is_primary': 'on'})
        vikram.refresh_from_db()
        self.assertEqual((vikram.name, vikram.relationship), ('Vikram S', 'sibling'))
        self.client.post(reverse('contact_delete', args=[vikram.pk]))
        asha.refresh_from_db()
        self.assertTrue(asha.is_primary)  # primary moves to the remaining contact
        self.assertContains(self.client.get(reverse('account_contacts')), 'Asha')
        self.assertEqual(AuditEvent.objects.filter(actor=self.user, action__startswith='Emergency contact').count(), 4)

    def test_validation_limit_and_isolation(self):
        self.client.force_login(self.user)
        self.assertContains(self.add('Bad', phone='12'), 'valid phone number')
        for i in range(5):
            self.add(f'Person {i}')
        self.assertRedirects(self.add('Sixth'), reverse('account_contacts'))
        self.assertEqual(EmergencyContact.objects.filter(user=self.user).count(), 5)
        mine = EmergencyContact.objects.filter(user=self.user).first()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('contact_edit', args=[mine.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('contact_delete', args=[mine.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('account_contacts')), mine.name)


@override_settings(EMAIL_OTP_ENABLED=False)
class LegacySessionTests(TestCase):
    def test_untracked_sessions_grouped_and_signed_out_together(self):
        user = User.objects.create_user('legacy-user', 'legacy@example.com', PASSWORD)
        old = Client(); old.force_login(user)
        legacy = old.session  # simulate a session created before device tracking existed
        legacy.pop('signed_in_at', None); legacy.pop('client', None); legacy.save()
        self.client.post(reverse('login'), {'username': 'legacy-user', 'password': PASSWORD})
        page = self.client.get(reverse('account_security'))
        self.assertEqual((len(page.context['sessions']), len(page.context['legacy_sessions'])), (1, 1))
        self.assertContains(page, '1 older session')
        self.assertContains(page, 'Current password')
        self.client.post(reverse('sign_out_legacy_sessions'))
        self.assertEqual(old.get(reverse('profile')).status_code, 302)
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)
