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
from accounts import delegation as Delegation,approval_work as Work
from accounts.models import ApprovalDelegationUse
from . import test_freight as fixture
from . import freight as F,logistics as L,freight_recovery as R,treasury as T,gl,closing
from .models import SupplierPayment,JournalEntry,FinancialPeriod,Partner,PaymentAllocation
import uuid

@override_settings(EMAIL_OTP_ENABLED=False)
class OffsetTests(TestCase):
    trip=fixture.FreightTests.trip
    @classmethod
    def setUpTestData(cls):fixture.FreightTests.setUpTestData.__func__(cls)

    def credit(self):
        trip=self.trip(self.truck,self.driver,100)
        bill=F.create_bill(self.admin,self.trans,'OFFSET-1',timezone.localdate(),{trip:dict(claimed=9000,days=0,extra=0,reason='')})
        F.decide(self.checker,bill.pk,True,'Verified')
        pay=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):9000},mode='cheque')
        T.decide(self.checker,pay.pk,True);T.release(self.checker,pay.pk,'TEST-ORIGINAL')
        L.deliver(self.admin,trip.pk,'Receiver',shortage=4,notes='Four missing bags')
        req=R.propose(self.admin,bill.pk,timezone.localdate(),'POD recovery',uuid.uuid4());R.decide(self.checker,req.pk,True,'Verified')
        return bill,trip,pay

    def setup_offset(self,cross=False):
        old,trip,original=self.credit()
        if cross:
            asset=T.capitalise(self.admin,dict(code='OFFSET-ASSET',name='Fictional equipment',category='fa_plant',site=self.site,supplier=self.trans,
                put_to_use=timezone.localdate(),cost=D(1600),salvage=D(0),method='slm',life_years=D(10)),'ap')
            picks={('frb',old.pk):1600,('asset',asset.pk):1600}
        else:
            other=self.trip(self.truck,self.driver,100)
            bill=F.create_bill(self.admin,self.trans,'OFFSET-2',timezone.localdate(),{other:dict(claimed=1600,days=0,extra=0,reason='')})
            F.decide(self.checker,bill.pk,True,'Lower actual claim verified')
            picks={('frb',old.pk):1600,('frb',bill.pk):1600}
        pay=T.propose(self.admin,self.trans,None,picks,mode='offset',note='Approved payable against recognized vendor credit')
        return pay,old,original,picks

    def test_same_control_account_settles_without_bank_or_empty_journal(self):
        pay,old,_,_=self.setup_offset()
        self.assertEqual(pay.amount,0);self.assertIsNone(pay.bank_id)
        self.assertFalse(T.open_items(self.trans))
        with self.assertRaisesMessage(ValidationError,'maker-checker'):T.decide(self.admin,pay.pk,True)
        T.decide(self.checker,pay.pk,True)
        T.settle(self.checker,pay.pk,'Independent source offset verified')
        pay.refresh_from_db();self.assertEqual(pay.status_label,'Settled without cash');self.assertEqual(pay.paid_by_id,self.checker.pk)
        self.assertFalse(JournalEntry.objects.filter(source=f'pay:{pay.pk}').exists())
        self.assertFalse(closing.source_gaps(timezone.localdate()))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        self.assertTrue(all(not row['diff'] for row in closing.historical_checks(timezone.localdate())))
        with self.assertRaises(ValidationError):T.settle(self.checker,pay.pk,'Retry')

    def test_cross_control_offset_posts_and_reverses_without_bank(self):
        pay,_,_,_=self.setup_offset(cross=True)
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        T.decide(self.checker,pay.pk,True);T.settle(self.checker,pay.pk,'Cross control offset confirmed')
        journal=JournalEntry.objects.get(source=f'pay:{pay.pk}')
        self.assertEqual(journal.lines.count(),2)
        self.assertEqual(set(journal.lines.values_list('account__system_key',flat=True)),{'ap','ap_freight'})
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        T.reverse_offset(self.checker,pay.pk,'Offset agreement rescinded')
        pay.refresh_from_db();self.assertEqual(pay.status_label,'Settlement reversed');self.assertEqual(pay.returned_on,timezone.localdate())
        self.assertTrue(JournalEntry.objects.filter(reversal_of=journal).exists())
        self.assertFalse(closing.source_gaps(timezone.localdate()))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        self.assertTrue(all(not row['diff'] for row in closing.historical_checks(timezone.localdate())))
        self.assertEqual(len(T.open_items(self.trans)),2)
        self.assertEqual(pay.returned_by_id,self.checker.pk)
        self.assertEqual(pay.reversal_reason,'Offset agreement rescinded')
        self.assertEqual(pay.settlement_note,'Cross control offset confirmed')
        self.assertEqual(pay.proposal_note,'Approved payable against recognized vendor credit')
        with self.assertRaises(ValidationError):T.reverse_offset(self.checker,pay.pk,'Retry')

    def test_no_bank_release_or_failure_or_export_for_offset(self):
        pay,_,_,_=self.setup_offset();T.decide(self.checker,pay.pk,True)
        with self.assertRaisesMessage(ValidationError,'explicit non-cash'):T.release(self.checker,pay.pk,'FAKE-UTR')
        self.assertEqual(len(T.bank_file(SupplierPayment.objects.filter(pk=pay.pk)).splitlines()),1)
        T.settle(self.checker,pay.pk,'Verified')
        with self.assertRaisesMessage(ValidationError,'explicit settlement reversal'):T.fail(self.checker,pay.pk,'Bank failure')
        self.assertFalse(pay.reference)

    def test_same_account_reversal_reopens_sources_with_no_journal(self):
        pay,_,_,_=self.setup_offset();T.decide(self.checker,pay.pk,True);T.settle(self.checker,pay.pk,'Verified')
        T.reverse_offset(self.checker,pay.pk,'Agreement cancelled')
        self.assertFalse(JournalEntry.objects.filter(source=f'pay:{pay.pk}').exists())
        self.assertEqual(sorted(row['amount'] for row in T.open_items(self.trans)),[D(-1600),D(1600)])
        self.assertFalse(closing.source_gaps(timezone.localdate()))
        self.assertTrue(any(item['payee']==self.trans for item in T.payees()))

    def test_invalid_cash_bank_amount_and_reason_are_rejected(self):
        old,_,_=self.credit()
        # Credit alone and nonzero selected totals cannot masquerade as a zero-cash settlement.
        for bank,picks,note in [(None,{('frb',old.pk):1600},'Credit only'),(gl.acct('bank_main'),{('frb',old.pk):1600},'Bank selected'),(None,{('frb',old.pk):1600},'')]:
            with self.assertRaises(ValidationError):T.propose(self.admin,self.trans,bank,picks,mode='offset',note=note)
        self.assertEqual(SupplierPayment.objects.count(),1)

    def test_current_pod_change_and_failed_backing_block_settlement(self):
        pay,old,original,_=self.setup_offset();T.decide(self.checker,pay.pk,True)
        T.fail(self.checker,original.pk,'Original bank payment returned')
        with self.assertRaisesMessage(ValidationError,'no longer backed'):T.settle(self.checker,pay.pk,'Invalidated credit')
        pay.refresh_from_db();self.assertEqual(pay.status,'approved')
        T.decide(self.checker,pay.pk,False,'Reallocate')

    def test_closed_period_blocks_no_journal_settlement_and_reversal(self):
        pay,_,_,_=self.setup_offset();T.decide(self.checker,pay.pk,True)
        period=gl.period_for(timezone.localdate());FinancialPeriod.objects.filter(pk=period.pk).update(status='closed')
        with self.assertRaises(ValidationError):T.settle(self.checker,pay.pk,'Closed period')
        FinancialPeriod.objects.filter(pk=period.pk).update(status='open');T.settle(self.checker,pay.pk,'Reopened and verified')
        FinancialPeriod.objects.filter(pk=period.pk).update(status='closed')
        with self.assertRaises(ValidationError):T.reverse_offset(self.checker,pay.pk,'Closed period reversal')
        pay.refresh_from_db();self.assertEqual(pay.status,'paid')

    def test_delegated_settlement_rollback_and_routing(self):
        pay,_,_,_=self.setup_offset(cross=True);T.decide(self.checker,pay.pk,True)
        deputy=User.objects.create_user('offset-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_supplierpayment','access_company_finance']))
        now=timezone.now();grant=Delegation.create(self.admin,self.checker,deputy,'release_supplierpayment',None,now,now+timedelta(days=1),'Settlement cover')
        task=Work.source('payment_release',pay.pk);self.assertTrue(Work.eligible(deputy,task)[0])
        with patch('erp.treasury.gl.post',side_effect=ValidationError('Posting failed')):
            with self.assertRaises(ValidationError):T.settle(deputy,pay.pk,'Verified')
        pay.refresh_from_db();self.assertEqual(pay.status,'approved');self.assertFalse(ApprovalDelegationUse.objects.filter(delegation=grant).exists())
        T.settle(deputy,pay.pk,'Independently verified')
        pay.refresh_from_db();self.assertEqual(pay.paid_by_id,deputy.pk);self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant).exists())

    def test_database_rejects_zero_cash_bank_payment_and_offset_with_bank(self):
        pay,_,_,_=self.setup_offset()
        for updates in [dict(mode='cheque'),dict(bank=gl.acct('bank_main')),dict(amount=1)]:
            with self.assertRaises(IntegrityError),transaction.atomic():SupplierPayment.objects.filter(pk=pay.pk).update(**updates)

    def test_web_creation_settlement_and_reversal(self):
        pay,_,_,picks=self.setup_offset();T.decide(self.checker,pay.pk,False,'Recreate via web')
        force_login_recent(self.client,self.admin)
        data={'mode':'offset','date':timezone.localdate(),'note':'Vendor offset agreement'}
        for (kind,pk),amount in picks.items():data[f'pick_{kind}_{pk}']='on';data[f'amt_{kind}_{pk}']=amount
        response=self.client.post(reverse('payment_new',args=[self.trans.pk]),data)
        new=SupplierPayment.objects.exclude(pk=pay.pk).filter(mode='offset').get()
        self.assertRedirects(response,reverse('payment_detail',args=[new.pk]))
        force_login_recent(self.client,self.checker)
        self.client.post(reverse('payment_action',args=[new.pk,'approve']))
        self.assertContains(self.client.get(reverse('payment_detail',args=[new.pk])),'Settle without cash')
        self.client.post(reverse('payment_action',args=[new.pk,'settle']),{'note':'Independently confirmed'})
        self.assertContains(self.client.get(reverse('payment_detail',args=[new.pk])),'Settled without cash')
        self.client.post(reverse('payment_action',args=[new.pk,'reverse_offset']),{'reason':'Agreement withdrawn'})
        new.refresh_from_db();self.assertEqual(new.status,'failed')

    def test_zero_cash_settlement_requires_current_read_and_finance_scope(self):
        pay,_,_,_=self.setup_offset();T.decide(self.checker,pay.pk,True)
        user=User.objects.create_user('offset-no-scope');user.user_permissions.set(Permission.objects.filter(codename__in=['view_supplierpayment','release_supplierpayment']))
        with self.assertRaises(PermissionDenied):T.settle(user,pay.pk,'No finance scope')
        user.user_permissions.add(Permission.objects.get(codename='access_company_finance'));user.user_permissions.remove(Permission.objects.get(codename='view_supplierpayment'))
        user=User.objects.get(pk=user.pk)
        with self.assertRaises(PermissionDenied):T.settle(user,pay.pk,'No module read')
        deputy=User.objects.create_user('offset-read-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_supplierpayment','access_company_finance']))
        now=timezone.now()
        with self.assertRaises(ValidationError):Delegation.create(self.admin,user,deputy,'release_supplierpayment',None,now,now+timedelta(days=1),'Principal has no read access')
        with self.assertRaisesMessage(ValidationError,'maker-checker'):T.settle(self.admin,pay.pk,'Own settlement')

    def test_offset_dates_and_exact_paise_and_duplicate_reservations(self):
        pay,_,_,picks=self.setup_offset()
        with self.assertRaises(ValidationError):T.propose(self.admin,self.trans,None,picks,mode='offset',note='Duplicate reservation')
        T.decide(self.checker,pay.pk,False,'Validate new selections')
        with self.assertRaisesMessage(ValidationError,'cannot precede'):T.propose(self.admin,self.trans,None,picks,mode='offset',on=timezone.localdate()-timedelta(days=1),note='Backdated offset')
        bad=dict(picks);key=next(iter(bad));bad[key]='1600.001'
        with self.assertRaisesMessage(ValidationError,'exact paise'):T.propose(self.admin,self.trans,None,bad,mode='offset',note='Overprecision')

    def test_pending_pod_correction_holds_preapproved_non_cash_offset(self):
        from . import pod
        pay,old,_,_=self.setup_offset();T.decide(self.checker,pay.pk,True)
        trip=old.lines.get().dispatch
        values=dict(received_by=trip.received_by,shortage='3',notes='Receiver recount')
        correction=pod.propose(self.admin,trip.pk,values,'Recount pending',uuid.uuid4(),pod.digest(pod.snapshot(trip)))
        with self.assertRaisesMessage(ValidationError,'reconcile'):T.settle(self.checker,pay.pk,'Pending source')
        pod.decide(self.checker,correction.pk,'reject','Original quantity confirmed')
        T.settle(self.checker,pay.pk,'Original POD reconfirmed')

    def test_missing_cross_control_journal_is_close_blocker_and_reversal_denied(self):
        pay,_,_,_=self.setup_offset(cross=True);T.decide(self.checker,pay.pk,True);T.settle(self.checker,pay.pk,'Verified')
        JournalEntry.objects.filter(source=f'pay:{pay.pk}').update(status='draft')
        self.assertIn(f'pay:{pay.pk}',[item['source'] for item in closing.source_gaps(timezone.localdate())])
        with self.assertRaisesMessage(ValidationError,'missing or already reversed'):T.reverse_offset(self.checker,pay.pk,'Invalid journal')
        pay.refresh_from_db();self.assertEqual(pay.status,'paid')

    def test_historical_cutoff_retains_offset_before_later_reversal(self):
        yesterday=timezone.localdate()-timedelta(days=1);instant=timezone.now()-timedelta(days=1)
        with patch('django.utils.timezone.localdate',return_value=yesterday),patch('django.utils.timezone.now',return_value=instant):
            pay,_,_,_=self.setup_offset(cross=True)
            T.decide(self.checker,pay.pk,True);T.settle(self.checker,pay.pk,'Prior day settlement')
        T.reverse_offset(self.checker,pay.pk,'Today reversal')
        earlier=closing.historical_checks(yesterday);current=closing.historical_checks(timezone.localdate())
        for rows in [earlier,current]:self.assertTrue(all(not row['diff'] for row in rows))
        self.assertEqual(next(row['sub'] for row in earlier if row['name']=='Supplier payables'),0)
        self.assertEqual(next(row['sub'] for row in current if row['name']=='Supplier payables'),1600)
        self.assertFalse(closing.source_gaps(yesterday));self.assertFalse(closing.source_gaps(timezone.localdate()))

    def test_standalone_supplier_debit_note_offsets_invoice_without_cash(self):
        from types import SimpleNamespace,MethodType
        from . import test_payables as ap_fixture,payables as AP
        f=SimpleNamespace();ap_fixture.PayablesTests.setUpTestData.__func__(f)
        f.receive=MethodType(ap_fixture.PayablesTests.receive,f);f.header=MethodType(ap_fixture.PayablesTests.header,f)
        line=f.receive('C-1',20,20)
        invoice=AP.create_invoice(f.ap,f.sup,f.header('OFFSET-AP-1',180000,9000),{line:(D(20),D(9000),D(5))})
        debit=AP.create_debit_note(f.ap,line.grn,'quality','Approved quality deduction',{line:10})
        pay=T.propose(f.ap,f.sup,None,{('sinv',invoice.pk):94500,('dn',debit.pk):94500},mode='offset',note='Invoice against quality credit')
        T.decide(f.fc,pay.pk,True);T.settle(f.fc,pay.pk,'Invoice and debit note verified')
        self.assertEqual(T.open_items(f.sup)[0]['amount'],D(94500))
        self.assertEqual(AP.balance(f.sup)['balance'],D(94500))
        self.assertFalse(JournalEntry.objects.filter(source=f'pay:{pay.pk}').exists())
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        T.reverse_offset(f.fc,pay.pk,'Agreement withdrawn')
        self.assertEqual(sorted(item['amount'] for item in T.open_items(f.sup)),[D(-94500),D(189000)])
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
