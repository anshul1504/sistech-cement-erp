"""CRM workflow regressions with fictional evidence and customers."""
from accounts.test_support import force_login_recent
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import crm
from .crm_forms import LeadForm
from .models import (CRMLead, CRMPolicy, DealerApplication, DealerDocument, DealerReview, Partner, PartnerDocument,
                     SiteAccess, SalesTarget, Order, Invoice, CreditNote, Receipt, Settlement, AuditEvent)
from .test_support import seed_business_data


def profile(**changes):
    return dict(kind='Dealer', name='Fictional Trading Company', territory='Central', contact_person='Test Contact', phone='9876543210',
        alt_phone='', email='crm@example.com', gstin='', pan='ABCDE1234F', address='Fictional billing address', city='Bhopal', district='Bhopal',
        state='23', pincode='462001', shipping_address='', credit_limit='100000', payment_terms_days=30, security_deposit='0',
        bank_name='', bank_account_number='', bank_ifsc='', notes='', **changes)


def seed_crm(target):
    seed_business_data(target)
    target.finance = User.objects.create_user('crm-finance')
    target.final = User.objects.create_user('crm-final')
    target.peer = User.objects.create_user('crm-peer')
    for actor in [target.finance, target.final, target.peer]:
        actor.user_permissions.set(Permission.objects.filter(content_type__app_label='erp'))
        SiteAccess.objects.create(user=actor, site=target.site)
    # A salesperson with own leads only; reviewers retain team scope.
    target.peer.user_permissions.remove(*Permission.objects.filter(codename__in=['view_team_crm', 'assign_crmlead']))
    target.today = timezone.localdate()


def new_lead(target, owner=None, mobile='9876543210'):
    return crm.save_lead(target.maker, dict(site=target.site, owner=owner or target.maker, business_name='Fictional Trading Company',
        contact_name='Test Contact', mobile=mobile, email='crm@example.com', kind='Dealer', source='visit', district='Bhopal',
        territory='Central', estimated_value=D(250000), next_follow_up=target.today, notes='Fictional QA prospect'))


def evidence(target, app, kind='pan_card', content=b'%PDF-1.4 fictional evidence', **data):
    return crm.upload_document(target.maker, app.pk, dict(doc_type=kind, number='QA-EVIDENCE', **data),
        dict(upload=SimpleUploadedFile('fictional.pdf', content, 'application/pdf')))


def submitted(target, lead=None):
    lead = lead or new_lead(target)
    app = crm.start_application(target.maker, lead.pk)
    crm.save_application(target.maker, app.pk, profile())
    evidence(target, app)
    evidence(target, app, 'dealership_agreement')
    return crm.submit_application(target.maker, app.pk, 'Fictional documents matched against the supplied profile')


def activated(target, app):
    crm.decide_application(target.checker, app.pk, 'kyc', 'Evidence manually reviewed')
    crm.decide_application(target.finance, app.pk, 'credit', 'Credit checked', '75000', '21')
    return crm.decide_application(target.final, app.pk, 'approve', 'Approved for activation')


@override_settings(EMAIL_OTP_ENABLED=False)
class CRMTests(TestCase):
    def setUp(self):
        seed_crm(self)
        force_login_recent(self.client, self.maker)

    def test_normalized_mobile_and_duplicate_site_scope(self):
        lead = new_lead(self, mobile='+91 98765-43210')
        self.assertEqual(lead.mobile, '9876543210')
        with self.assertRaises(ValidationError): new_lead(self, mobile='09876543210')
        data = dict(site=self.site.pk, owner=self.maker.pk, business_name='Another', mobile='+91 87654-32109', kind='Dealer', source='direct', estimated_value='0')
        form = LeadForm(data, user=self.maker)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(PermissionDenied):
            crm.save_lead(self.outsider, dict(site=self.site, owner=self.outsider, mobile='9876543210'))
        with self.assertRaises(PermissionDenied):
            crm.save_lead(self.maker, dict(site=self.site, owner=self.outsider, mobile='8765432109'))

    def test_own_leads_and_site_boundaries_on_reads_writes_and_exports(self):
        lead = new_lead(self)
        app = crm.start_application(self.maker, lead.pk)
        doc = evidence(self, app)
        for actor in [self.peer, self.outsider]:
            force_login_recent(self.client, actor)
            self.assertEqual(self.client.get(reverse('crm_lead_detail', args=[lead.pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse('crm_application_detail', args=[app.pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse('crm_document_file', args=[doc.pk])).status_code, 404)
            self.assertNotContains(self.client.get(reverse('crm_leads') + '?export=csv'), lead.number)
            with self.assertRaises(PermissionDenied): crm.change_stage(actor, lead.pk, 'qualified')
        force_login_recent(self.client, self.maker)
        self.assertContains(self.client.get(reverse('crm_leads')), lead.number)

    def test_followups_loss_reopen_and_no_manual_conversion(self):
        lead = new_lead(self)
        activity = crm.record_activity(self.maker, lead.pk, dict(kind='visit', outcome='Prospect met', note='Fictional site visit', happened_on=self.today, next_follow_up=self.today+timedelta(days=2)))
        lead.refresh_from_db()
        self.assertEqual((lead.stage, lead.next_follow_up), ('contacted', activity.next_follow_up))
        with self.assertRaises(ValidationError): crm.change_stage(self.maker, lead.pk, 'won')
        with self.assertRaises(ValidationError): crm.change_stage(self.maker, lead.pk, 'lost')
        crm.change_stage(self.maker, lead.pk, 'lost', 'Prospect deferred purchase')
        with self.assertRaises(ValidationError): crm.change_stage(self.maker, lead.pk, 'contacted')
        crm.change_stage(self.maker, lead.pk, 'contacted', 'Prospect returned')
        with self.assertRaises(ValidationError):
            crm.record_activity(self.maker, lead.pk, dict(kind='call', outcome='Call', happened_on=self.today+timedelta(days=1)))

    def test_pending_application_never_creates_active_customer(self):
        before = Partner.objects.count()
        app = submitted(self)
        self.assertEqual(Partner.objects.count(), before)
        self.assertIsNone(app.partner)
        with self.assertRaises(ValidationError): crm.start_application(self.maker, app.lead_id)
        with self.assertRaises(ValidationError): crm.change_stage(self.maker, app.lead_id, 'qualified')
        with self.assertRaises(ValidationError): crm.decide_application(self.final, app.pk, 'approve', 'Too early')
        with self.assertRaises(ValidationError): crm.save_application(self.maker, app.pk, profile())
        with self.assertRaises(ValidationError): evidence(self, app)

    def test_independent_approvals_credit_caps_and_single_activation(self):
        app = submitted(self)
        with self.assertRaises(ValidationError): crm.decide_application(self.maker, app.pk, 'kyc', 'Self approval')
        with self.assertRaises(ValidationError): crm.decide_application(self.checker, app.pk, 'kyc', '')
        crm.decide_application(self.checker, app.pk, 'kyc', 'KYC checked')
        with self.assertRaises(ValidationError): crm.decide_application(self.checker, app.pk, 'credit', 'Same reviewer', '100', '1')
        for amount, days in [('100001', '30'), ('NaN', '30'), ('-1', '30'), ('100', '31'), ('100', '-1')]:
            with self.assertRaises(ValidationError): crm.decide_application(self.finance, app.pk, 'credit', 'Check bounds', amount, days)
        crm.decide_application(self.finance, app.pk, 'credit', 'Terms checked', '75000', '21')
        with self.assertRaises(ValidationError): crm.decide_application(self.finance, app.pk, 'approve', 'Self finance approval')
        result = crm.decide_application(self.final, app.pk, 'approve', 'Final approved')
        p = result.partner
        self.assertEqual((p.credit_limit, p.payment_terms_days, p.approved_application_id), (D('75000'), 21, app.pk))
        self.assertEqual(p.documents.count(), 2)
        result.lead.refresh_from_db()
        self.assertEqual((result.lead.stage, result.lead.partner_id), ('won', p.pk))
        with self.assertRaises(ValidationError): crm.decide_application(self.final, app.pk, 'approve', 'Retry')
        self.assertEqual(Partner.objects.filter(approved_application_id=app.pk).count(), 1)

    def test_document_versions_withdrawal_and_content_validation(self):
        app = crm.start_application(self.maker, new_lead(self).pk)
        first = evidence(self, app)
        second = evidence(self, app, content=b'%PDF-1.4 second fictional version')
        first.refresh_from_db()
        self.assertFalse(first.is_current)
        self.assertEqual(second.version, 2)
        self.assertEqual(bytes(first.file), b'%PDF-1.4 fictional evidence')
        with self.assertRaises(ValidationError): evidence(self, app, content=b'<html>not a PDF</html>')
        with self.assertRaises(ValidationError): evidence(self, app, content=b'%PDF-' + b'x' * (5*1024*1024))
        with self.assertRaises(ValidationError): crm.withdraw_document(self.maker, app.pk, second.pk, '')
        crm.withdraw_document(self.maker, app.pk, second.pk, 'Replaced wrong PAN evidence')
        crm.save_application(self.maker, app.pk, profile())
        with self.assertRaisesMessage(ValidationError, 'Required KYC'): crm.submit_application(self.maker, app.pk, 'Try incomplete')
        self.assertEqual(app.documents.count(), 2)

    def test_corrections_policy_and_historical_snapshots(self):
        app = submitted(self)
        old = app.reviews.get(action='Submitted')
        crm.save_policy(self.final, [], True)
        # Frozen policy from the submission still applies.
        crm.decide_application(self.checker, app.pk, 'changes', 'Address correction required')
        updated = profile(); updated['address'] = 'Corrected fictional address'
        crm.save_application(self.maker, app.pk, updated)
        with self.assertRaisesMessage(ValidationError, 'GSTIN'): crm.submit_application(self.maker, app.pk, 'New policy applies to resubmission')
        crm.save_policy(self.final, ['pan_card', 'dealership_agreement'], False)
        app = crm.submit_application(self.maker, app.pk, 'Corrected address checked')
        self.assertEqual(app.revision, 2)
        old.refresh_from_db()
        self.assertEqual(old.snapshot['profile']['address'], 'Fictional billing address')
        self.assertEqual(app.submission_snapshot['profile']['address'], 'Corrected fictional address')
        result = activated(self, app)
        review = result.reviews.get(action='Credit approved')
        self.assertEqual(review.snapshot['decision']['approved_credit'], '75000.00')

    def test_frozen_submission_detects_tampered_evidence_and_owner(self):
        app = submitted(self)
        doc = app.documents.get(doc_type='pan_card')
        DealerDocument.objects.filter(pk=doc.pk).update(file=b'%PDF-1.4 altered')
        with self.assertRaisesMessage(ValidationError, 'changed after submission'):
            crm.decide_application(self.checker, app.pk, 'kyc', 'Check changed evidence')
        # A reviewer can return invalid evidence for correction.
        crm.decide_application(self.checker, app.pk, 'changes', 'Evidence no longer matches')
        evidence(self, app)
        app = crm.submit_application(self.maker, app.pk, 'Replaced evidence')
        CRMLead.objects.filter(pk=app.lead_id).update(owner=self.peer)
        with self.assertRaisesMessage(ValidationError, 'changed after submission'):
            crm.decide_application(self.checker, app.pk, 'kyc', 'Owner changed')

    def test_expired_evidence_blocks_review_but_allows_return(self):
        app = submitted(self)
        DealerDocument.objects.filter(application=app, doc_type='pan_card').update(valid_until=self.today-timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, 'expired'): crm.decide_application(self.checker, app.pk, 'kyc', 'Expired')
        crm.decide_application(self.checker, app.pk, 'changes', 'Replace expired evidence')

    def test_contributor_cannot_become_reviewer(self):
        lead = new_lead(self)
        app = crm.start_application(self.maker, lead.pk)
        crm.save_application(self.maker, app.pk, profile())
        evidence(self, app)
        crm.upload_document(self.checker, app.pk, dict(doc_type='dealership_agreement'), dict(upload=SimpleUploadedFile('agreement.pdf', b'%PDF-1.4 agreement', 'application/pdf')))
        crm.submit_application(self.maker, app.pk, 'Ready')
        with self.assertRaisesMessage(ValidationError, 'contributor'): crm.decide_application(self.checker, app.pk, 'kyc', 'Contributed evidence')

    def test_amendment_reuses_customer_and_keeps_prior_snapshots(self):
        app = activated(self, submitted(self))
        partner = app.partner
        amendment = crm.start_application(self.maker, app.lead_id)
        requested = dict(amendment.profile); requested['credit_limit'] = '200000'; requested['name'] = 'Fictional Trading Revised'
        crm.save_application(self.maker, amendment.pk, requested)
        partner.refresh_from_db(); self.assertEqual(partner.credit_limit, D(75000))
        amendment = crm.submit_application(self.maker, amendment.pk, 'Revised credit with supporting review')
        crm.decide_application(self.checker, amendment.pk, 'kyc', 'KYC checked')
        crm.decide_application(self.finance, amendment.pk, 'credit', 'Credit revised', '150000', '21')
        result = crm.decide_application(self.final, amendment.pk, 'approve', 'Amendment approved')
        partner.refresh_from_db()
        self.assertEqual(result.partner_id, partner.pk)
        self.assertEqual(partner.credit_limit, D(150000))
        self.assertEqual(partner.documents.count(), 2)
        app.refresh_from_db(); self.assertEqual(app.submission_snapshot['profile']['name'], 'Fictional Trading Company')
        rows = crm.performance(self.maker, self.today)
        self.assertEqual(sum(r['new_dealers'] for r in rows), 1)

    def test_stale_amendment_does_not_overwrite_master(self):
        lead = new_lead(self)
        crm.link_existing(self.maker, lead.pk, self.customer, 'Confirmed pre-existing dealer')
        app = crm.start_application(self.maker, lead.pk)
        crm.save_application(self.maker, app.pk, profile())
        evidence(self, app); evidence(self, app, 'dealership_agreement')
        Partner.objects.filter(pk=self.customer.pk).update(credit_limit=123)
        with self.assertRaisesMessage(ValidationError, 'master changed'): crm.submit_application(self.maker, app.pk, 'Stale update')

    def test_duplicate_gstin_checked_before_new_customer_activation(self):
        app = crm.start_application(self.maker, new_lead(self).pk)
        data = profile(); data['gstin'] = '23ABCDE1234F1Z5'
        crm.save_application(self.maker, app.pk, data)
        evidence(self, app); evidence(self, app, 'dealership_agreement'); evidence(self, app, 'gst_certificate')
        app = crm.submit_application(self.maker, app.pk, 'Ready')
        Partner.objects.create(code='EXISTING-GST', name='Other matching dealer', kind='Dealer', gstin='23ABCDE1234F1Z5')
        with self.assertRaisesMessage(ValidationError, 'already has a customer'): crm.decide_application(self.checker, app.pk, 'kyc', 'Duplicate GST')

    def test_direct_master_customer_creation_and_credit_bypasses_blocked(self):
        from .test_partners import BASE
        response = self.client.post(reverse('partners_new'), {**BASE, 'kind':'Dealer','code':'BYPASS','name':'Bypass'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Partner.objects.filter(code='BYPASS').exists())
        response = self.client.post(reverse('partners_edit', args=[self.customer.pk]), {**BASE, 'kind':'Supplier', 'code':self.customer.code, 'name':'Changed', 'credit_limit':'999999999'})
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.kind, 'Dealer')
        self.assertEqual(self.customer.credit_limit, D(10**8))
        self.assertEqual(self.customer.name, 'Verification dealer')

    def test_approved_kyc_projection_cannot_be_deleted_through_master(self):
        app = activated(self, submitted(self))
        doc = app.partner.documents.first()
        self.client.post(reverse('partner_document_delete', args=[app.partner_id, doc.pk]))
        self.assertTrue(PartnerDocument.objects.filter(pk=doc.pk).exists())
        force_login_recent(self.client, self.outsider)
        self.assertEqual(self.client.get(reverse('partner_document_file', args=[app.partner_id, doc.pk])).status_code, 403)
        force_login_recent(self.client, self.maker)
        self.client.post(reverse('partner_delete', args=[app.partner_id]))
        self.assertTrue(Partner.objects.filter(pk=app.partner_id).exists())
        self.assertEqual(self.client.get(reverse('partner_detail', args=[app.partner_id])).status_code, 200)

    def test_scoped_download_export_and_all_screens_render(self):
        app = submitted(self)
        crm.save_target(self.maker, dict(site=self.site, owner=self.maker, month=self.today, sales_amount=D(100000), new_dealers=5, note='Monthly plan'))
        review = app.reviews.first(); doc = app.documents.first()
        for name, args in [('crm_leads',[]),('crm_lead_new',[]),('crm_lead_detail',[app.lead_id]),('crm_lead_edit',[app.lead_id]),
                           ('crm_applications',[]),('crm_application_detail',[app.pk]),('crm_application_edit',[app.pk]),
                           ('crm_review_detail',[review.pk]),('crm_performance',[]),('crm_performance_detail',[self.site.pk,self.maker.pk]),('crm_policy',[])]:
            with self.subTest(name=name): self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)
        self.assertEqual(self.client.get(reverse('crm_document_file', args=[doc.pk]))['Cache-Control'], 'private, no-store')
        CRMLead.objects.filter(pk=app.lead_id).update(business_name='=SUM(A1:A2)')
        self.assertContains(self.client.get(reverse('crm_leads')+'?export=csv'), "'=SUM(A1:A2)")
        self.assertTrue(AuditEvent.objects.filter(action='Dealer KYC downloaded').exists())
        self.assertTrue(AuditEvent.objects.filter(action='CRM pipeline exported').exists())
        self.maker.user_permissions.remove(Permission.objects.get(codename='export_crmlead'))
        self.assertEqual(self.client.get(reverse('crm_leads')+'?export=csv').status_code, 403)

    def test_target_month_upsert_and_scoped_performance(self):
        data = dict(site=self.site, owner=self.maker, month=self.today, sales_amount=D(100000), new_dealers=3, note='Plan')
        target = crm.save_target(self.maker, data)
        again = crm.save_target(self.maker, dict(data, sales_amount=D(200000)))
        self.assertEqual(target.pk, again.pk)
        self.assertEqual(again.month.day, 1)
        with self.assertRaises(PermissionDenied): crm.save_target(self.outsider, data)
        with self.assertRaises(PermissionDenied): crm.save_target(self.peer, data)
        self.assertEqual(crm.performance(self.peer, self.today), [])
        force_login_recent(self.client, self.peer)
        self.assertEqual(self.client.get(reverse('crm_performance_detail', args=[self.site.pk, self.maker.pk])).status_code, 404)

    def test_month_sales_credit_collections_and_current_overdue(self):
        order = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker)
        invoice = Invoice.objects.create(number='QA-CRM-1', date=self.today, order=order, site=self.site, partner=self.customer,
            seller_name='QA', seller_gstin='', seller_state='23', seller_address='QA', buyer_name='QA Dealer', place_of_supply='23', interstate=False,
            taxable=1000, cgst=90, sgst=90, igst=0, round_off=0, grand_total=1180, due_date=self.today-timedelta(days=1), created_by=self.maker)
        cn = CreditNote.objects.create(number='QA-CN-1', date=self.today, invoice=invoice, partner=self.customer, reason='rate', taxable=100,
            cgst=9, sgst=9, igst=0, round_off=0, total=118, created_by=self.maker)
        Settlement.objects.create(invoice=invoice, credit_note=cn, amount=118)
        posted = Receipt.objects.create(number='QA-R-1', partner=self.customer, date=self.today, amount=600, created_by=self.maker)
        Settlement.objects.create(invoice=invoice, receipt=posted, amount=500)
        Receipt.objects.create(number='QA-ADVANCE', partner=self.customer, date=self.today, amount=900, created_by=self.maker)
        row = crm.performance(self.maker, self.today)[0]
        self.assertEqual((row['sales'], row['collections'], row['overdue']), (D(900), D(500), D(562)))
        # Old invoice still contributes this month's credit/collection and current overdue.
        invoice.date=self.today.replace(day=1)-timedelta(days=1); invoice.save(update_fields=['date'])
        row = crm.performance(self.maker, self.today)[0]
        self.assertEqual(row['sales'], D(-100))
        self.assertEqual(row['collections'], D(500))

    def test_mutations_require_explicit_module_permissions(self):
        actor = User.objects.create_user('crm-viewer')
        actor.user_permissions.add(Permission.objects.get(codename='view_crmlead'))
        SiteAccess.objects.create(user=actor, site=self.site)
        lead = new_lead(self, owner=actor)
        force_login_recent(self.client, actor)
        self.assertEqual(self.client.get(reverse('crm_lead_detail', args=[lead.pk])).status_code, 200)
        for action, data in [('stage', {'stage':'qualified'}), ('activity', {'kind':'call'}), ('apply', {})]:
            self.assertEqual(self.client.post(reverse('crm_lead_action', args=[lead.pk,action]), data).status_code, 403)
        self.assertEqual(self.client.get(reverse('crm_leads')+'?export=csv').status_code, 403)
        self.assertEqual(self.client.get(reverse('crm_policy')).status_code, 403)
        self.assertEqual(self.client.get(reverse('crm_performance')).status_code, 403)

    def test_rejection_withdrawal_and_required_conditional_evidence(self):
        app = crm.start_application(self.maker, new_lead(self).pk)
        data=profile(); data.update(bank_name='QA Bank', bank_account_number='1234567890', bank_ifsc='SBIN0000001')
        crm.save_application(self.maker, app.pk, data)
        evidence(self, app); evidence(self, app, 'dealership_agreement')
        with self.assertRaisesMessage(ValidationError, 'Cancelled cheque'):
            crm.submit_application(self.maker, app.pk, 'Bank evidence missing')
        evidence(self, app, 'cancelled_cheque')
        app=crm.submit_application(self.maker, app.pk, 'Bank evidence supplied')
        crm.decide_application(self.checker, app.pk, 'reject', 'Evidence rejected')
        app.refresh_from_db(); self.assertEqual(app.status, 'rejected')
        fresh=crm.start_application(self.maker, app.lead_id)
        crm.withdraw_application(self.maker, fresh.pk, 'Prospect cancelled request')
        fresh.refresh_from_db(); self.assertEqual(fresh.status, 'rejected')
        self.assertEqual(DealerApplication.objects.filter(lead=app.lead).count(), 2)
        self.assertTrue(app.documents.exists())

    def test_legacy_evidence_change_invalidates_an_amendment(self):
        lead=new_lead(self)
        crm.link_existing(self.maker,lead.pk,self.customer,'Matched legacy dealer')
        app=crm.start_application(self.maker,lead.pk)
        crm.save_application(self.maker,app.pk,profile())
        evidence(self,app); evidence(self,app,'dealership_agreement')
        crm.submit_application(self.maker,app.pk,'Legacy evidence reviewed')
        PartnerDocument.objects.create(partner=self.customer, doc_type='other', file=b'%PDF-1.4 new legacy evidence',
            file_name='legacy.pdf', content_type='application/pdf', size=29, uploaded_by=self.maker)
        with self.assertRaisesMessage(ValidationError,'master changed'):
            crm.decide_application(self.checker,app.pk,'kyc','Changed legacy evidence')

    def test_partial_order_customer_detail_stays_available(self):
        Order.objects.create(site=self.site,partner=self.customer,kind='Sale',created_by=self.maker,status='Part dispatched')
        response=self.client.get(reverse('partner_detail',args=[self.customer.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['summary']['Part dispatched'],1)
