from django.contrib.auth.models import User, Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Organization, AuditEvent

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64
VALID = {
    'legal_name': 'Sistech Cement & TMT Pvt. Ltd.', 'trade_name': 'Sistech', 'constitution': 'private_limited',
    'display_name': 'SISTECH', 'subtitle': 'Cement & TMT Pvt. Ltd.', 'tagline': 'Solid foundation, strong future.',
    'address': 'Industrial Area', 'city': 'Bhopal', 'district': 'Bhopal', 'state': '23', 'pincode': '462001',
    'gstin': '23abcde1234f1z5', 'pan': 'abcde1234f', 'tan': '', 'cin': '', 'udyam': '', 'iec': '',
    'gst_registration_type': 'regular', 'einvoice_applicable': 'on', 'eway_bill_applicable': 'on',
    'deals_cement': 'on', 'deals_tmt': 'on', 'bis_cement_licence': '', 'bis_tmt_licence': '', 'fy_start_month': '4', 'default_bag_weight': '50',
    'bank_name': '', 'bank_branch': '', 'bank_account_name': '', 'bank_account_number': '', 'bank_ifsc': 'sbin0001234',
    'phone': '+91 98765 43210', 'alt_phone': '', 'customer_care': '1800 123 4567', 'whatsapp': '', 'email': 'info@example.com', 'website': '',
    'signatory_name': '', 'signatory_designation': '', 'terms_conditions': 'Goods once sold will not be taken back.', 'document_footer': '',
}


@override_settings(EMAIL_OTP_ENABLED=False)
class OrganizationSetupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('org-admin', 'org.admin@example.com', 'Original-Password-816!')
        cls.plain = User.objects.create_user('org-plain', 'org.plain@example.com', 'Original-Password-816!')
        cls.viewer = User.objects.create_user('org-viewer', 'org.viewer@example.com', 'Original-Password-816!')
        cls.editor = User.objects.create_user('org-editor', 'org.editor@example.com', 'Original-Password-816!')
        view = Group.objects.create(name='Org viewer'); view.permissions.add(Permission.objects.get(codename='view_organization'))
        edit = Group.objects.create(name='Org editor'); edit.permissions.add(*Permission.objects.filter(codename__in=['view_organization', 'change_organization']))
        cls.viewer.groups.add(view); cls.editor.groups.add(edit)

    def post(self, user, **extra):
        self.client.force_login(user)
        return self.client.post(reverse('organization_edit'), {**VALID, **extra})

    def test_access_rules(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse('organization')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'href="/organisation/"')
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('organization'))
        self.assertContains(response, 'Sistech Cement &amp; TMT Pvt. Ltd.')
        self.assertNotContains(response, reverse('organization_edit'))
        self.assertNotContains(response, 'Delete')
        self.assertEqual(self.client.get(reverse('organization_edit')).status_code, 403)
        self.assertEqual(self.post(self.viewer).status_code, 403)

    def test_role_with_edit_permission_can_save(self):
        response = self.post(self.editor)
        self.assertRedirects(response, reverse('organization'))
        overview = self.client.get(reverse('organization'))
        self.assertContains(overview, '23ABCDE1234F1Z5')
        self.assertContains(overview, reverse('organization_edit'))
        org = Organization.current()
        self.assertEqual((org.gstin, org.pan, org.bank_ifsc, org.updated_by), ('23ABCDE1234F1Z5', 'ABCDE1234F', 'SBIN0001234', self.editor))
        self.assertTrue(AuditEvent.objects.filter(action='Organisation updated', actor=self.editor).exists())

    def test_gstin_must_match_pan_and_state(self):
        response = self.post(self.admin, pan='ZZZZZ9999Z')
        self.assertContains(response, 'must match the PAN')
        response = self.post(self.admin, state='27')
        self.assertContains(response, 'registered state code is 27')

    def test_statutory_formats_validated(self):
        response = self.post(self.admin, pincode='12345', bank_ifsc='BAD', tan='123', phone='abc')
        for message in ['6-digit PIN', 'valid 11-character IFSC', 'valid 10-character TAN', 'valid phone number']:
            self.assertContains(response, message)

    def test_at_least_one_business_line(self):
        data = {k: v for k, v in VALID.items() if k not in ('deals_cement', 'deals_tmt')}
        self.client.force_login(self.admin)
        self.assertContains(self.client.post(reverse('organization_edit'), data), 'Select at least one business line')

    def test_logo_upload_served_and_branding_shown_to_other_users(self):
        self.post(self.admin, icon_file=SimpleUploadedFile('icon.png', PNG, 'image/png'), display_name='ACME', subtitle='Test Cement')
        org = Organization.current()
        self.assertEqual(org.icon_type, 'image/png')
        asset = self.client.get(reverse('brand_asset', args=['icon']))
        self.assertEqual((asset.status_code, asset['Content-Type'], asset['X-Content-Type-Options']), (200, 'image/png', 'nosniff'))
        self.client.force_login(self.plain)
        page = self.client.get(reverse('profile'))
        self.assertContains(page, '/brand/icon/?v=')
        self.assertContains(page, 'ACME')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('brand_asset', args=['icon'])).status_code, 200)

    def test_rejects_svg_and_non_images(self):
        svg = SimpleUploadedFile('logo.svg', b'<svg onload="alert(1)"></svg>', 'image/svg+xml')
        response = self.post(self.admin, logo_file=svg)
        self.assertContains(response, 'Upload a PNG, JPG or WebP image')
        self.assertFalse(Organization.current().logo_type)

    def test_remove_uploaded_icon(self):
        self.post(self.admin, icon_file=SimpleUploadedFile('icon.png', PNG, 'image/png'))
        self.post(self.admin, remove_icon='on')
        self.assertEqual(Organization.current().icon_type, '')
        self.assertEqual(self.client.get(reverse('brand_asset', args=['icon'])).status_code, 404)

    def test_role_matrix_offers_organisation_module(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('role_new')), 'Organisation setup')

    def test_gst_type_and_bag_weight_rules(self):
        self.assertContains(self.post(self.admin, gst_registration_type='unregistered'), 'cannot have a GSTIN')
        self.assertContains(self.post(self.admin, default_bag_weight='250'), 'between 1 and 100 kg')

    def test_new_product_uses_standard_bag_weight(self):
        self.post(self.admin, default_bag_weight='40')
        self.client.force_login(self.admin)
        form = self.client.get(reverse('products_new')).context['form']
        self.assertEqual(float(form.initial['bag_weight_kg']), 40.0)
        org = Organization.current()
        self.assertEqual((org.customer_care, org.terms_conditions), ('1800 123 4567', 'Goods once sold will not be taken back.'))



PDF = b'%PDF-1.4\n%test\n'


@override_settings(EMAIL_OTP_ENABLED=False)
class OrganizationDocumentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('doc-admin', 'doc.admin@example.com', 'Original-Password-816!')
        cls.viewer = User.objects.create_user('doc-viewer', 'doc.viewer@example.com', 'Original-Password-816!')
        cls.plain = User.objects.create_user('doc-plain', 'doc.plain@example.com', 'Original-Password-816!')
        view = Group.objects.create(name='Doc viewer'); view.permissions.add(Permission.objects.get(codename='view_organization'))
        cls.viewer.groups.add(view)

    def upload(self, user, content=PDF, name='gst.pdf', **extra):
        self.client.force_login(user)
        data = {'doc_type': 'gst_certificate', 'number': '23ABCDE1234F1Z5', 'title': '', 'notes': '', 'issued_on': '', 'valid_until': '',
                'upload': SimpleUploadedFile(name, content, 'application/pdf'), **extra}
        return self.client.post(reverse('organization_document_new'), data)

    def test_upload_view_download_delete_with_audit(self):
        from .models import OrganizationDocument
        response = self.upload(self.admin)
        doc = OrganizationDocument.objects.get()
        self.assertRedirects(response, reverse('organization_document', args=[doc.pk]))
        detail = self.client.get(reverse('organization_document', args=[doc.pk]))
        self.assertContains(detail, '<iframe')
        self.assertContains(detail, 'Uploaded')
        self.assertEqual((doc.content_type, doc.display_title, doc.uploaded_by), ('application/pdf', 'GST registration certificate', self.admin))
        inline = self.client.get(reverse('organization_document_file', args=[doc.pk]))
        self.assertEqual((inline['Content-Type'], inline['X-Content-Type-Options']), ('application/pdf', 'nosniff'))
        self.assertTrue(inline['Content-Disposition'].startswith('inline'))
        self.assertTrue(self.client.get(reverse('organization_document_file', args=[doc.pk]) + '?download=1')['Content-Disposition'].startswith('attachment'))
        self.client.post(reverse('organization_document_delete', args=[doc.pk]))
        self.assertFalse(OrganizationDocument.objects.exists())
        actions = set(AuditEvent.objects.values_list('action', flat=True))
        self.assertTrue({'Organisation document uploaded', 'Organisation document deleted'} <= actions)

    def test_permissions(self):
        from .models import OrganizationDocument
        self.upload(self.admin)
        doc = OrganizationDocument.objects.get()
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse('organization_documents')).status_code, 403)
        self.assertEqual(self.client.get(reverse('organization_document_file', args=[doc.pk])).status_code, 403)
        self.client.force_login(self.viewer)
        page = self.client.get(reverse('organization_documents'))
        self.assertContains(page, doc.display_title)
        self.assertNotContains(page, reverse('organization_document_new'))
        self.assertNotContains(page, reverse('organization_document_edit', args=[doc.pk]))
        self.assertEqual(self.client.get(reverse('organization_document', args=[doc.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('organization_document_new')).status_code, 403)
        self.assertEqual(self.client.get(reverse('organization_document_edit', args=[doc.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('organization_document_file', args=[doc.pk])).status_code, 200)
        self.assertEqual(self.upload(self.viewer).status_code, 403)
        self.assertEqual(self.client.post(reverse('organization_document_delete', args=[doc.pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('organization_document_file', args=[doc.pk])).status_code, 302)

    def test_rejects_unsupported_files_and_bad_dates(self):
        self.assertContains(self.upload(self.admin, content=b'<html>x</html>', name='x.pdf'), 'Upload a PDF, PNG, JPG or WebP file')
        self.assertContains(self.upload(self.admin, issued_on='2026-05-01', valid_until='2026-04-01'), 'cannot end before the issue date')

    def test_expiry_status_and_filters(self):
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.localdate()
        self.upload(self.admin, doc_type='factory_licence', valid_until=(today - timedelta(days=1)).isoformat())
        self.upload(self.admin, doc_type='pcb_consent', valid_until=(today + timedelta(days=10)).isoformat())
        self.upload(self.admin, doc_type='pan_card')
        page = self.client.get(reverse('organization_documents'))
        self.assertEqual(page.context['counts'], {'total': 3, 'expired': 1, 'expiring': 1})
        self.assertContains(page, '10 days left')
        expired = self.client.get(reverse('organization_documents') + '?status=expired')
        self.assertEqual([d.doc_type for d in expired.context['documents']], ['factory_licence'])
        self.assertContains(self.client.get(reverse('organization')), '1 expired')

    def test_edit_details_keeps_file_and_replace_file(self):
        from .models import OrganizationDocument
        self.upload(self.admin)
        doc = OrganizationDocument.objects.get()
        base = {'doc_type': 'gst_certificate', 'title': 'GST certificate – head office', 'number': '23ABCDE1234F1Z5', 'notes': '', 'issued_on': '', 'valid_until': '', 'site': ''}
        self.client.post(reverse('organization_document_edit', args=[doc.pk]), base)
        doc.refresh_from_db()
        self.assertEqual((doc.title, bytes(doc.file)), ('GST certificate – head office', PDF))
        png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20
        self.client.post(reverse('organization_document_edit', args=[doc.pk]), {**base, 'upload': SimpleUploadedFile('scan.png', png, 'image/png')})
        doc.refresh_from_db()
        self.assertEqual((doc.content_type, doc.file_name), ('image/png', 'scan.png'))
        self.assertTrue(AuditEvent.objects.filter(action='Organisation document updated', detail__contains='file replaced').exists())

    def test_search_filter(self):
        self.upload(self.admin, number='FACT-778', doc_type='factory_licence')
        self.upload(self.admin, number='PAN-1')
        page = self.client.get(reverse('organization_documents') + '?q=FACT')
        self.assertEqual([d.number for d in page.context['documents']], ['FACT-778'])

    def test_overview_masks_bank_account(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('organization_edit'), {**VALID, 'bank_account_number': '123456789012'})
        page = self.client.get(reverse('organization'))
        self.assertContains(page, '••••••••9012')
        self.assertNotContains(page, '123456789012')

