from decimal import Decimal as D
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.contrib.auth.models import User,Permission
from django.db import IntegrityError,transaction
from django.utils import timezone
from django.urls import reverse
from accounts.test_support import force_login_recent
from accounts import delegation as Delegation
from accounts.models import ApprovalDelegationUse
from . import test_non_cash_offsets as fixture
from . import treasury as T,gl,closing
from .models import SupplierPayment,JournalEntry,FinancialPeriod

@override_settings(EMAIL_OTP_ENABLED=False)
class VendorRefundTests(TestCase):
    trip=fixture.OffsetTests.trip
    credit=fixture.OffsetTests.credit
    @classmethod
    def setUpTestData(cls):fixture.OffsetTests.setUpTestData.__func__(cls)

    def refund(self):
        bill,trip,paid=self.credit()
        refund=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):1600},mode='refund',note='Vendor agreed to return recognized recovery')
        return refund,bill,paid

    def test_refund_receipt_reconciles_and_reverse_reopens_credit(self):
        refund,bill,_=self.refund();self.assertEqual(refund.amount,-1600)
        self.assertFalse(T.open_items(self.trans))
        T.decide(self.checker,refund.pk,True);T.receive_refund(self.checker,refund.pk,'INCOMING-001','Bank credit independently verified')
        refund.refresh_from_db();self.assertEqual(refund.status_label,'Refund received');self.assertEqual(refund.paid_by_id,self.checker.pk)
        entry=JournalEntry.objects.get(source=f'pay:{refund.pk}')
        self.assertEqual(entry.lines.get(account__system_key='bank_main').debit,D(1600))
        self.assertEqual(entry.lines.get(account__system_key='ap_freight').credit,D(1600))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        self.assertTrue(all(not row['diff'] for row in closing.historical_checks(timezone.localdate())))
        self.assertFalse(closing.source_gaps(timezone.localdate()))
        T.reverse_refund(self.checker,refund.pk,'Bank credit reversed')
        self.assertEqual(T.open_items(self.trans)[0]['amount'],-1600)
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        self.assertFalse(closing.source_gaps(timezone.localdate()))
        refund.refresh_from_db();self.assertEqual(refund.returned_by_id,self.checker.pk)
        self.assertEqual(refund.proposal_note,'Vendor agreed to return recognized recovery')
        self.assertEqual(refund.reference,'INCOMING-001')

    def test_maker_cannot_receive_or_reverse_and_no_duplicate_post(self):
        refund,_,_=self.refund();T.decide(self.checker,refund.pk,True)
        with self.assertRaisesMessage(ValidationError,'maker-checker'):T.receive_refund(self.admin,refund.pk,'IN-1','Own receipt')
        T.receive_refund(self.checker,refund.pk,'IN-1','Verified')
        with self.assertRaises(ValidationError):T.receive_refund(self.checker,refund.pk,'IN-1','Retry')
        with self.assertRaisesMessage(ValidationError,'maker-checker'):T.reverse_refund(self.admin,refund.pk,'Own reversal')
        self.assertEqual(JournalEntry.objects.filter(source=f'pay:{refund.pk}').count(),1)

    def test_credit_backing_and_closed_period_hold_receipt(self):
        refund,_,paid=self.refund();T.decide(self.checker,refund.pk,True)
        period=gl.period_for(refund.date);FinancialPeriod.objects.filter(pk=period.pk).update(status='closed')
        with self.assertRaises(ValidationError):T.receive_refund(self.checker,refund.pk,'IN-CLOSED','Verified')
        FinancialPeriod.objects.filter(pk=period.pk).update(status='open')
        T.fail(self.checker,paid.pk,'Original outgoing payment returned')
        with self.assertRaisesMessage(ValidationError,'no longer backed'):T.receive_refund(self.checker,refund.pk,'IN-BLOCK','Invalidated backing')
        refund.refresh_from_db();self.assertEqual(refund.status,'approved');self.assertFalse(refund.reference)

    def test_outgoing_export_and_actions_cannot_process_refund(self):
        refund,_,_=self.refund();T.decide(self.checker,refund.pk,True)
        self.assertEqual(len(T.bank_file(SupplierPayment.objects.filter(pk=refund.pk)).splitlines()),1)
        for fn,args in [(T.release,('UTR',)),(T.settle,('Finding',)),(T.fail,('Bank return',)),(T.reverse_offset,('Wrong mode',))]:
            with self.assertRaises(ValidationError):fn(self.checker,refund.pk,*args)
        with self.assertRaises(IntegrityError),transaction.atomic():SupplierPayment.objects.filter(pk=refund.pk).update(amount=1600)

    def test_incoming_reference_not_reused_after_reversal(self):
        refund,bill,_=self.refund();T.decide(self.checker,refund.pk,True);T.receive_refund(self.checker,refund.pk,'CASE-REF','Verified')
        T.reverse_refund(self.checker,refund.pk,'Reversed receipt')
        new=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):1600},mode='refund',note='New receipt expected')
        T.decide(self.checker,new.pk,True)
        with self.assertRaisesMessage(ValidationError,'already recorded'):T.receive_refund(self.checker,new.pk,'case-ref','Duplicate reference')

    def test_delegated_receipt_rolls_back_posting_failure(self):
        refund,_,_=self.refund();T.decide(self.checker,refund.pk,True)
        deputy=User.objects.create_user('refund-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_supplierpayment','access_company_finance']))
        now=timezone.now();grant=Delegation.create(self.admin,self.checker,deputy,'release_supplierpayment',None,now,now+timedelta(days=1),'Incoming refund cover')
        with patch('erp.treasury.gl.post',side_effect=ValidationError('Closed posting')):
            with self.assertRaises(ValidationError):T.receive_refund(deputy,refund.pk,'IN-ROLLBACK','Verified')
        self.assertFalse(ApprovalDelegationUse.objects.filter(delegation=grant).exists())
        refund.refresh_from_db();self.assertEqual(refund.status,'approved')
        T.receive_refund(deputy,refund.pk,'IN-ACTOR','Verified')
        refund.refresh_from_db();self.assertEqual(refund.paid_by_id,deputy.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant).exists())

    def test_web_refund_receipt_and_reversal(self):
        refund,_,_=self.refund();T.decide(self.checker,refund.pk,True)
        force_login_recent(self.client,self.checker);url=reverse('payment_detail',args=[refund.pk])
        self.assertContains(self.client.get(url),'Record refund received')
        self.client.post(reverse('payment_action',args=[refund.pk,'receive_refund']),{'reference':'WEB-IN','note':'Bank credit reviewed'})
        self.assertContains(self.client.get(url),'Refund received')
        self.client.post(reverse('payment_action',args=[refund.pk,'reverse_refund']),{'reason':'Bank reversal confirmed'})
        refund.refresh_from_db();self.assertEqual(refund.status,'failed')

    def test_refund_only_vendors_are_visible_and_reservation_is_exclusive(self):
        refund,bill,_=self.refund();T.decide(self.checker,refund.pk,False,'Recreate')
        self.assertTrue(any(item['payee']==self.trans for item in T.payees()))
        new=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):1600},mode='refund',note='New refund reservation')
        with self.assertRaises(ValidationError):T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):1},mode='refund',note='Double use')
        with self.assertRaises(IntegrityError),transaction.atomic():SupplierPayment.objects.filter(pk=new.pk).update(bank=None)

    def test_receipt_requires_bank_reference_and_current_bank(self):
        refund,_,_=self.refund();T.decide(self.checker,refund.pk,True)
        for reference,note in [('', 'Finding'),('x'*41,'Finding'),('IN','')]:
            with self.assertRaises(ValidationError):T.receive_refund(self.checker,refund.pk,reference,note)
        type(refund.bank).objects.filter(pk=refund.bank_id).update(active=False)
        with self.assertRaisesMessage(ValidationError,'active receiving bank'):T.receive_refund(self.checker,refund.pk,'IN-ACTIVE','Verified')
        self.assertFalse(JournalEntry.objects.filter(source=f'pay:{refund.pk}').exists())

    def test_historical_refund_before_later_reversal(self):
        yesterday=timezone.localdate()-timedelta(days=1);instant=timezone.now()-timedelta(days=1)
        with patch('django.utils.timezone.localdate',return_value=yesterday),patch('django.utils.timezone.now',return_value=instant):
            refund,_,_=self.refund();T.decide(self.checker,refund.pk,True);T.receive_refund(self.checker,refund.pk,'IN-HISTORY','Prior-day bank credit')
        T.reverse_refund(self.checker,refund.pk,'Today reversal')
        for on in [yesterday,timezone.localdate()]:
            self.assertTrue(all(not row['diff'] for row in closing.historical_checks(on)))
            self.assertFalse(closing.source_gaps(on))
        self.assertEqual(next(row['sub'] for row in closing.historical_checks(yesterday) if row['name']=='Transporter payables'),0)
        self.assertEqual(next(row['sub'] for row in closing.historical_checks(timezone.localdate()) if row['name']=='Transporter payables'),-1600)

    def test_standalone_supplier_debit_note_refund(self):
        from types import SimpleNamespace,MethodType
        from . import test_payables as fixture,payables as AP
        f=SimpleNamespace();fixture.PayablesTests.setUpTestData.__func__(f)
        f.receive=MethodType(fixture.PayablesTests.receive,f);f.header=MethodType(fixture.PayablesTests.header,f)
        line=f.receive('C-1',20,20)
        AP.create_invoice(f.ap,f.sup,f.header('REFUND-SUPPLIER',180000,9000),{line:(D(20),D(9000),D(5))})
        debit=AP.create_debit_note(f.ap,line.grn,'quality','Approved vendor refund',{line:10})
        refund=T.propose(f.ap,f.sup,gl.acct('bank_main'),{('dn',debit.pk):94500},mode='refund',note='Vendor returns quality deduction')
        T.decide(f.fc,refund.pk,True);T.receive_refund(f.fc,refund.pk,'IN-SUPPLIER','Bank credit confirmed')
        self.assertEqual(AP.balance(f.sup)['balance'],D(189000))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        T.reverse_refund(f.fc,refund.pk,'Vendor credit reversed')
        self.assertEqual(AP.balance(f.sup)['balance'],D(94500))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))

    def test_actual_bank_value_date_drives_cutoff_and_proposed_date_is_audited(self):
        from .models import AuditEvent
        yesterday=timezone.localdate()-timedelta(days=1);instant=timezone.now()-timedelta(days=1)
        with patch('django.utils.timezone.localdate',return_value=yesterday),patch('django.utils.timezone.now',return_value=instant):
            refund,_,_=self.refund();T.decide(self.checker,refund.pk,True)
        T.receive_refund(self.checker,refund.pk,'IN-VALUE-DATE','Today bank value confirmed',timezone.localdate())
        refund.refresh_from_db();self.assertEqual(refund.date,timezone.localdate())
        self.assertIn(str(yesterday),AuditEvent.objects.get(reference=refund.number,action='Payment proposed').detail)
        before=next(row for row in closing.historical_checks(yesterday) if row['name']=='Transporter payables')
        after=next(row for row in closing.historical_checks(timezone.localdate()) if row['name']=='Transporter payables')
        self.assertEqual((before['sub'],before['diff']),(-1600,0));self.assertEqual((after['sub'],after['diff']),(0,0))

    def test_invalid_receipt_value_date_rolls_back_reference_and_allocations(self):
        refund,_,_=self.refund();T.decide(self.checker,refund.pk,True)
        for on in [timezone.localdate()+timedelta(days=1),timezone.localdate()-timedelta(days=1)]:
            with self.assertRaises(ValidationError):T.receive_refund(self.checker,refund.pk,'IN-INVALID-DATE','Invalid date',on)
        refund.refresh_from_db();self.assertEqual(refund.status,'approved');self.assertFalse(refund.reference)
        self.assertFalse(JournalEntry.objects.filter(source=f'pay:{refund.pk}').exists())
