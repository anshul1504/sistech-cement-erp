"""Fictional incentive-rule and retained-preview regressions."""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth.models import User, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import schemes
from .models import (IncentiveScheme, SchemeVersion, SchemeEvaluation, Invoice, InvoiceLine, Order, CreditNote,
                     CreditNoteLine, Receipt, Settlement, SiteAccess, JournalEntry, AuditEvent)
from .test_support import seed_business_data


def rule_data(target, **changes):
    return dict(starts_on=target.today-timedelta(days=30), ends_on=target.today, product='', partner='', customer_kind='', district='', territory='',
                basis='net_sales', dealer_cap='', notes='Fictional incentive rule', **changes)


def draft_scheme(target, code='QA-INCENTIVE', rules=None, slabs=None):
    return schemes.save_rules(target.maker, dict(code=code, name='Fictional dealer incentive', site=target.site), rules or rule_data(target),
        slabs if slabs is not None else [dict(minimum_sales='0', percentage='1'), dict(minimum_sales='1000', percentage='2')])


def approved_scheme(target, **kwargs):
    version=draft_scheme(target, **kwargs)
    schemes.submit(target.maker,version.pk,'Fictional proposed terms')
    return schemes.decide(target.checker,version.pk,True,'Independent commercial review complete')


def invoice_fixture(target, amounts=None, site=None, partner=None, on=None):
    partner,site=partner or target.customer,site or target.site
    order=Order.objects.create(site=site,partner=partner,kind='Sale',created_by=target.maker)
    amounts=amounts or [(target.bag,D(1000))]
    taxable=sum((D(a) for _,a in amounts),D(0))
    inv=Invoice.objects.create(number=f'QA-INC-{Invoice.objects.count()+1}',date=on or target.today,site=site,partner=partner,order=order,
        seller_name='Fictional company',seller_gstin='23ABCDE1234F1Z5',seller_state='23',seller_address='Fictional address',
        buyer_name=partner.name,buyer_state='23',place_of_supply='23',interstate=False,taxable=taxable,
        cgst=taxable*D('.09'),sgst=taxable*D('.09'),igst=0,round_off=0,grand_total=taxable*D('1.18'),due_date=target.today,created_by=target.maker)
    for product,amount in amounts:
        InvoiceLine.objects.create(invoice=inv,product=product,description=product.name,hsn='2523',unit=product.unit,quantity=1,rate=amount,taxable=amount,
            gst_rate=18,cgst=D(amount)*D('.09'),sgst=D(amount)*D('.09'),igst=0,total=D(amount)*D('1.18'))
    return inv


def credit_fixture(target, inv, taxable=100, line=None, on=None):
    taxable=D(taxable)
    cn=CreditNote.objects.create(number=f'QA-INC-C{CreditNote.objects.count()+1}',date=on or target.today,invoice=inv,partner=inv.partner,
        reason='return',taxable=taxable,cgst=taxable*D('.09'),sgst=taxable*D('.09'),igst=0,round_off=0,total=taxable*D('1.18'),created_by=target.maker)
    if line:
        CreditNoteLine.objects.create(credit_note=cn,invoice_line=line,quantity=D('.1'),taxable=taxable,gst_rate=18,cgst=taxable*D('.09'),sgst=taxable*D('.09'),igst=0)
    Settlement.objects.create(invoice=inv,credit_note=cn,amount=cn.total)
    return cn


def receipt_fixture(target, inv, allocated, amount=None, status='posted'):
    receipt=Receipt.objects.create(number=f'QA-INC-R{Receipt.objects.count()+1}',date=target.today,partner=inv.partner,
        amount=amount or allocated,status=status,created_by=target.maker)
    if allocated:
        Settlement.objects.create(invoice=inv,receipt=receipt,amount=allocated)
    return receipt


@override_settings(EMAIL_OTP_ENABLED=False)
class SchemeTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today=timezone.localdate()
        self.client.force_login(self.maker)

    def calc(self,version,key=None,starts=None,ends=None):
        return schemes.calculate(self.maker,version.pk,starts or version.starts_on,ends or version.ends_on,key or uuid4())

    def test_draft_validation_atomic_creation_and_slab_bounds(self):
        for slabs in [[],[dict(minimum_sales='0',percentage='101')],[dict(minimum_sales='-1',percentage='2')],
                      [dict(minimum_sales='100',percentage='2'),dict(minimum_sales='100.00',percentage='3')],
                      [dict(minimum_sales=str(i),percentage='1') for i in range(21)]]:
            with self.subTest(slabs=slabs), self.assertRaises(ValidationError): draft_scheme(self,slabs=slabs)
            self.assertFalse(IncentiveScheme.objects.exists())
        rules=rule_data(self); rules['ends_on']=rules['starts_on']-timedelta(days=1)
        with self.assertRaises(ValidationError): draft_scheme(self,rules=rules)
        self.assertFalse(IncentiveScheme.objects.exists())

    def test_independent_review_all_contributors_and_locked_decisions(self):
        version=draft_scheme(self)
        schemes.submit(self.maker,version.pk,'Proposed')
        with self.assertRaisesMessage(ValidationError,'contributor'): schemes.decide(self.maker,version.pk,True,'Self review')
        with self.assertRaises(ValidationError): schemes.decide(self.checker,version.pk,True,'')
        schemes.decide(self.checker,version.pk,True,'Reviewed')
        with self.assertRaises(ValidationError): schemes.save_rules(self.maker,{},rule_data(self),[],version.pk)
        with self.assertRaises(ValidationError): schemes.submit(self.maker,version.pk,'Retry')
        with self.assertRaises(ValidationError): schemes.decide(self.checker,version.pk,True,'Retry')
        self.assertTrue(AuditEvent.objects.filter(action='Scheme approved').exists())

    def test_editor_cannot_approve_even_after_another_editor(self):
        version=draft_scheme(self)
        rows=list(version.slabs.values('minimum_sales','percentage'))
        schemes.save_rules(self.checker,{},rule_data(self),rows,version.pk)
        schemes.save_rules(self.maker,{},rule_data(self),rows,version.pk)
        schemes.submit(self.maker,version.pk,'Ready')
        with self.assertRaisesMessage(ValidationError,'contributor'): schemes.decide(self.checker,version.pk,True,'Previously edited')

    def test_scope_enforced_by_services_and_http(self):
        version=approved_scheme(self)
        evaluation=self.calc(version)
        for call in [lambda: schemes.revise(self.outsider,version.scheme_id), lambda: schemes.submit(self.outsider,version.pk,'Foreign'),
                     lambda: schemes.calculate(self.outsider,version.pk,version.starts_on,version.ends_on,uuid4()),
                     lambda: schemes.check_sources(self.outsider,evaluation.pk)]:
            with self.assertRaises(PermissionDenied): call()
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(reverse('schemes')),version.scheme.code)
        for route,args in [('scheme_detail',[version.pk]),('scheme_edit',[version.pk]),('scheme_evaluation',[evaluation.pk])]:
            self.assertEqual(self.client.get(reverse(route,args=args)).status_code,404)
        self.assertEqual(self.client.get(reverse('scheme_evaluation',args=[evaluation.pk])+'?export=csv').status_code,404)
        self.assertEqual(self.client.post(reverse('scheme_action',args=[version.pk,'calculate']),{}).status_code,404)

    def test_read_only_permissions_do_not_allow_mutation_or_export(self):
        viewer=User.objects.create_user('scheme-viewer')
        viewer.user_permissions.set(Permission.objects.filter(codename__in=['view_incentivescheme','view_schemeevaluation']))
        SiteAccess.objects.create(user=viewer,site=self.site)
        version=approved_scheme(self); result=self.calc(version)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('scheme_detail',args=[version.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('scheme_new')).status_code,403)
        self.assertEqual(self.client.post(reverse('scheme_action',args=[version.pk,'calculate']),{}).status_code,403)
        self.assertEqual(self.client.get(reverse('scheme_evaluation',args=[result.pk])+'?export=csv').status_code,403)
        self.assertEqual(self.client.post(reverse('scheme_check_sources',args=[result.pk])).status_code,403)

    def test_version_revision_no_overlap_and_single_open_draft(self):
        version=approved_scheme(self)
        before=schemes.rule_snapshot(version)
        draft=schemes.revise(self.maker,version.scheme_id)
        self.assertEqual((draft.revision,draft.starts_on),(2,version.ends_on+timedelta(days=1)))
        self.assertEqual(draft.slabs.count(),2)
        self.assertEqual(schemes.rule_snapshot(SchemeVersion.objects.get(pk=version.pk)),before)
        with self.assertRaises(ValidationError): schemes.revise(self.maker,version.scheme_id)
        with self.assertRaisesMessage(ValidationError,'overlap'):
            schemes.save_rules(self.maker,{},rule_data(self),list(draft.slabs.values('minimum_sales','percentage')),draft.pk)
        self.assertEqual(SchemeVersion.objects.get(pk=draft.pk).starts_on,draft.starts_on)

    def test_rejection_preserves_rules_and_allows_new_draft(self):
        version=draft_scheme(self)
        schemes.submit(self.maker,version.pk,'Ready')
        schemes.decide(self.checker,version.pk,False,'Policy not accepted')
        revision=schemes.revise(self.maker,version.scheme_id)
        self.assertEqual(revision.revision,2)
        self.assertEqual(version.slabs.count(),2)
        with self.assertRaises(ValidationError): self.calc(SchemeVersion.objects.get(pk=version.pk))

    def test_calculation_dates_and_approval_gate(self):
        version=draft_scheme(self)
        with self.assertRaises(ValidationError): self.calc(version)
        schemes.submit(self.maker,version.pk,'Ready'); version=schemes.decide(self.checker,version.pk,True,'Reviewed')
        for starts,ends in [(version.starts_on-timedelta(days=1),version.ends_on),(version.starts_on,self.today+timedelta(days=1)),(self.today,version.starts_on)]:
            with self.assertRaises(ValidationError): self.calc(version,starts=starts,ends=ends)
        self.assertFalse(SchemeEvaluation.objects.exists())

    def test_highest_slab_on_whole_base_excluding_gst_and_cap(self):
        invoice_fixture(self,[(self.bag,D(1000))])
        rules=rule_data(self); rules['dealer_cap']='15.00'
        result=self.calc(approved_scheme(self,rules=rules))
        row=result.snapshot['dealers'][0]
        self.assertEqual((row['eligible'],row['percentage'],row['incentive']),('1000.00','2.00','15.00'))
        self.assertEqual(result.total,D(15))

    def test_zero_cap_and_below_first_slab(self):
        invoice_fixture(self)
        rules=rule_data(self); rules['dealer_cap']='0'
        result=self.calc(approved_scheme(self,rules=rules))
        self.assertEqual(result.total,D(0))
        version=approved_scheme(self,code='QA-SECOND',slabs=[dict(minimum_sales='2000',percentage='3')])
        result=self.calc(version)
        self.assertIsNone(result.snapshot['dealers'][0]['threshold'])
        self.assertEqual(result.total,D(0))

    def test_product_scope_and_line_credit_deductions(self):
        inv=invoice_fixture(self,[(self.bag,D(1000)),(self.bulk,D(9000))])
        credit_fixture(self,inv,100,inv.lines.get(product=self.bag))
        credit_fixture(self,inv,1000,inv.lines.get(product=self.bulk))
        rules=rule_data(self); rules['product']=self.bag
        result=self.calc(approved_scheme(self,rules=rules))
        row=result.snapshot['dealers'][0]
        self.assertEqual((row['eligible'],row['incentive']),('900.00','9.00'))
        self.assertEqual(row['invoices'][0]['credits'],'100.00')

    def test_unallocated_product_credit_fails_instead_of_guessing(self):
        inv=invoice_fixture(self)
        credit_fixture(self,inv,100)
        rules=rule_data(self); rules['product']=self.bag
        with self.assertRaisesMessage(ValidationError,'no line allocation'): self.calc(approved_scheme(self,rules=rules))
        self.assertFalse(SchemeEvaluation.objects.exists())

    def test_returns_after_invoice_period_reduce_current_calculation(self):
        inv=invoice_fixture(self,on=self.today-timedelta(days=10))
        credit_fixture(self,inv,100,inv.lines.first())
        result=self.calc(approved_scheme(self),ends=self.today-timedelta(days=5))
        self.assertEqual(result.total,D(9))
        self.assertEqual(result.snapshot['dealers'][0]['invoices'][0]['credit_notes'][0]['date'],str(self.today))

    def test_customer_type_district_territory_and_site_match_together(self):
        self.customer.district='Bhopal'; self.customer.territory='Central Zone'; self.customer.save()
        invoice_fixture(self); invoice_fixture(self,site=self.other)
        rules=rule_data(self); rules.update(partner=self.customer,customer_kind='Dealer',district='  BHOPAL ',territory='central   zone')
        version=approved_scheme(self,rules=rules)
        self.assertEqual(self.calc(version).total,D(20))
        self.customer.territory='Other'; self.customer.save()
        self.assertEqual(self.calc(version).total,D(0))
        rules['customer_kind']='Distributor'
        with self.assertRaises(ValidationError): draft_scheme(self,code='BAD-SCOPE',rules=rules)

    def test_paid_mode_requires_whole_invoice_settlement_not_advances(self):
        inv=invoice_fixture(self)
        credit_fixture(self,inv,100,inv.lines.first())
        receipt_fixture(self,inv,D(500),D(700))
        rules=rule_data(self); rules['basis']='settled'
        version=approved_scheme(self,rules=rules)
        result=self.calc(version)
        self.assertEqual(result.total,D(0))
        receipt_fixture(self,inv,D(562))
        self.assertEqual(self.calc(version).total,D(9))
        self.assertEqual(Receipt.objects.first().amount,D(562))

    def test_bounced_receipt_invalidates_new_preview_keeps_old_one(self):
        inv=invoice_fixture(self)
        receipt=receipt_fixture(self,inv,D(1180))
        rules=rule_data(self); rules['basis']='settled'
        version=approved_scheme(self,rules=rules)
        old=self.calc(version)
        self.assertTrue(schemes.check_sources(self.maker,old.pk))
        receipt.status='bounced';receipt.save(update_fields=['status'])
        receipt.settlements.all().delete()
        self.assertFalse(schemes.check_sources(self.maker,old.pk))
        self.assertEqual(self.calc(version).total,D(0))
        old.refresh_from_db(); self.assertEqual(old.total,D(20))
        self.assertEqual(old.snapshot['dealers'][0]['invoices'][0]['allocations'][0]['status'],'posted')

    def test_submission_retry_returns_same_evidence_and_changed_payload_fails(self):
        inv=invoice_fixture(self)
        version=approved_scheme(self)
        key=uuid4(); original=self.calc(version,key)
        credit_fixture(self,inv,100,inv.lines.first())
        retry=self.calc(version,key)
        self.assertEqual(retry.pk,original.pk)
        self.assertEqual(retry.total,D(20))
        with self.assertRaises(ValidationError): self.calc(version,key,ends=self.today-timedelta(days=1))
        self.assertEqual(SchemeEvaluation.objects.count(),1)
        self.assertEqual(self.calc(version).total,D(9))

    def test_no_accounting_or_payment_side_effects(self):
        invoice_fixture(self)
        journals=JournalEntry.objects.count(); receipts=Receipt.objects.count(); settlements=Settlement.objects.count(); credits=CreditNote.objects.count()
        self.calc(approved_scheme(self))
        self.assertEqual((JournalEntry.objects.count(),Receipt.objects.count(),Settlement.objects.count(),CreditNote.objects.count()),(journals,receipts,settlements,credits))

    def test_multi_dealer_slabs_are_not_pooled_across_customers(self):
        from .models import Partner
        other=Partner.objects.create(code='QA-D2',name='Other QA dealer',kind='Dealer')
        invoice_fixture(self,[(self.bag,D(500))]); invoice_fixture(self,[(self.bag,D(500))],partner=other)
        result=self.calc(approved_scheme(self))
        self.assertEqual(result.total,D(10))
        self.assertEqual([r['percentage'] for r in result.snapshot['dealers']],['1.00','1.00'])

    def test_http_pages_csv_escaping_and_export_audit(self):
        self.customer.name='=SUM(A1:A2)';self.customer.save()
        invoice_fixture(self)
        version=approved_scheme(self); result=self.calc(version)
        for name,args in [('schemes',[]),('scheme_new',[]),('scheme_detail',[version.pk]),('scheme_evaluation',[result.pk])]:
            with self.subTest(name=name): self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200)
        response=self.client.get(reverse('scheme_evaluation',args=[result.pk])+'?export=csv')
        self.assertContains(response,"'=SUM(A1:A2)")
        self.assertTrue(AuditEvent.objects.filter(action='Scheme preview exported').exists())
        evidence=self.client.get(reverse('scheme_evaluation',args=[result.pk])+'?export=json')
        self.assertEqual(evidence.json()['source_digest'],result.source_digest)
        self.assertEqual(evidence.json()['snapshot'],result.snapshot)
        self.assertEqual(evidence['Cache-Control'],'private, no-store')
        self.assertTrue(AuditEvent.objects.filter(action='Scheme evidence exported').exists())
        self.assertContains(self.client.post(reverse('scheme_check_sources',args=[result.pk]),follow=True),'match this retained calculation')

    def test_real_form_creates_draft_and_calculation_post_retries(self):
        data={'scheme-code':'WEB-INC','scheme-name':'Web QA Scheme','scheme-site':self.site.pk,**rule_data(self),
              'slabs-TOTAL_FORMS':1,'slabs-INITIAL_FORMS':0,'slabs-MIN_NUM_FORMS':0,'slabs-MAX_NUM_FORMS':20,
              'slabs-0-minimum_sales':'0','slabs-0-percentage':'2'}
        response=self.client.post(reverse('scheme_new'),data)
        self.assertEqual(response.status_code,302)
        version=SchemeVersion.objects.get(scheme__code='WEB-INC')
        schemes.submit(self.maker,version.pk,'Ready'); schemes.decide(self.checker,version.pk,True,'Reviewed')
        invoice_fixture(self)
        payload=dict(starts_on=version.starts_on,ends_on=version.ends_on,submission_key=uuid4())
        first=self.client.post(reverse('scheme_action',args=[version.pk,'calculate']),payload)
        second=self.client.post(reverse('scheme_action',args=[version.pk,'calculate']),payload)
        self.assertEqual(first.url,second.url)
        self.assertEqual(SchemeEvaluation.objects.count(),1)

    def test_rounding_is_half_up_to_paise(self):
        invoice_fixture(self,[(self.bag,D('1.50'))])
        result=self.calc(approved_scheme(self))
        self.assertEqual(result.total,D('.02'))

    def test_later_return_recalculation_keeps_original_amount(self):
        inv=invoice_fixture(self)
        version=approved_scheme(self)
        result=self.calc(version)
        credit_fixture(self,inv,100,inv.lines.first())
        self.assertFalse(schemes.check_sources(self.maker,result.pk))
        fresh=self.calc(version)
        self.assertEqual((result.total,fresh.total),(D(20),D(9)))
        result.refresh_from_db()
        self.assertEqual(result.snapshot['dealers'][0]['invoices'][0]['credit_notes'],[])

    def test_no_matching_sales_still_retains_a_zero_preview(self):
        version=approved_scheme(self)
        result=self.calc(version)
        self.assertEqual(result.total,D(0))
        self.assertEqual(result.snapshot['dealers'],[])
        self.assertTrue(schemes.check_sources(self.maker,result.pk))

    def test_same_key_cannot_be_reused_by_another_actor(self):
        version=approved_scheme(self)
        result=self.calc(version)
        with self.assertRaises(ValidationError):
            schemes.calculate(self.checker,version.pk,version.starts_on,version.ends_on,result.submission_key)
        self.assertEqual(SchemeEvaluation.objects.count(),1)
