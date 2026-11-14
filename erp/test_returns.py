"""Customer-return custody, credit and exact-value regression coverage."""
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import returns as R, gl, stock
from .credits import create_credit_note
from .finance_views import credit_room
from .invoice_views import create_invoice
from .models import (ReturnRequest, CreditNote, Order, OrderLine, StockBalance, StockMovement, JournalEntry, SiteAccess,
                     AuditEvent, Complaint, FinancialPeriod)
from .services import move_stock, transition_order
from .test_support import seed_business_data


def seed_returns(target):
    seed_business_data(target)
    target.inspector=User.objects.create_user('qa-return-inspector')
    target.inspector.user_permissions.set(Permission.objects.filter(content_type__app_label='erp'))
    SiteAccess.objects.create(user=target.inspector,site=target.site)
    target.today=timezone.localdate()
    move_stock(target.maker,target.site,target.bag,D(10),'qa:rma-opening','Opening',value=D('20.03'))
    order=Order.objects.create(site=target.site,partner=target.customer,kind='Sale',created_by=target.maker)
    line=OrderLine.objects.create(order=order,product=target.bag,quantity=10,rate=10,gst_rate=18)
    transition_order(target.maker,order.pk,'submit',confirm_duplicate=True)
    transition_order(target.checker,order.pk,'approve')
    transition_order(target.maker,order.pk,'post')
    target.invoice=create_invoice(target.maker,order,[line],{})
    target.line=target.invoice.lines.get()


def requested(target, qty=10, key=None):
    return R.request_return(target.maker,target.invoice,'damaged','Fictional bags returned from QA dealer',{target.line:D(qty)},submission_key=key)


def inspected(target, good=6, damaged=4):
    r=requested(target)
    R.decide(target.checker,r.pk,True,'Return quantity and invoice verified')
    R.pickup(target.inspector,r.pk,target.today,'MP04GA1234','QA-LR')
    R.inspect(target.inspector,r.pk,{r.lines.get().pk:(D(10),D(good),D(damaged),'Not accepted by plant' if good+damaged<10 else '')})
    r.refresh_from_db()
    return r


@override_settings(EMAIL_OTP_ENABLED=False)
class ReturnTests(TestCase):
    def setUp(self):
        seed_returns(self)
        self.client.force_login(self.maker)

    def test_full_return_segregates_damaged_stock_at_original_exact_cost(self):
        r=inspected(self)
        position=stock.position([self.site])[0]
        self.assertEqual((position['available'],position['hold']),(0,10))
        self.assertEqual(CreditNote.objects.count(),0)
        credited=R.credit(self.checker,r.pk)
        cn=credited.credit_note
        self.assertEqual((cn.taxable,cn.total),(D(100),D(118)))
        good=StockBalance.objects.get(site=self.site,product=self.bag,status='available')
        bad=StockBalance.objects.get(site=self.site,product=self.bag,status='damaged')
        self.assertEqual((good.quantity,bad.quantity),(6,4))
        self.assertEqual(good.value+bad.value,D('20.03'))
        self.assertEqual(stock.position([self.site])[0]['hold'],0)
        self.invoice.refresh_from_db();self.assertEqual(self.invoice.outstanding,0)
        self.assertEqual(gl.trial_balance(None,self.today)['diff'],0)
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        journals=JournalEntry.objects.count()
        self.assertEqual(R.credit(self.checker,r.pk).credit_note_id,cn.pk)
        self.assertEqual(JournalEntry.objects.count(),journals)
        self.assertEqual(R.returnable(self.line),0)

    def test_maker_checker_credit_requires_independent_inspection(self):
        r=requested(self)
        with self.assertRaises(ValidationError): R.decide(self.maker,r.pk,True,'Self approval')
        with self.assertRaises(ValidationError): R.credit(self.checker,r.pk)
        R.decide(self.checker,r.pk,True,'Approved')
        R.inspect(self.inspector,r.pk,{r.lines.get().pk:(D(10),D(10),D(0),'')})
        for actor in [self.maker,self.inspector]:
            with self.assertRaises(ValidationError): R.credit(actor,r.pk)
        self.assertFalse(CreditNote.objects.exists())

    def test_site_checks_cover_all_service_actions(self):
        r=inspected(self)
        for call in [lambda:R.request_return(self.outsider,self.invoice,'damaged','Foreign',{self.line:D(1)}),
                     lambda:R.decide(self.outsider,r.pk,False,'Foreign'),lambda:R.pickup(self.outsider,r.pk,self.today,'MP04GA1234'),
                     lambda:R.inspect(self.outsider,r.pk,{}),lambda:R.credit(self.outsider,r.pk),lambda:R.cancel(self.outsider,r.pk,'Foreign')]:
            with self.assertRaises(PermissionDenied): call()
        self.client.force_login(self.outsider)
        for name,args in [('return_detail',[r.pk]),('return_action',[r.pk,'credit'])]:
            response=self.client.post(reverse(name,args=args)) if name=='return_action' else self.client.get(reverse(name,args=args))
            self.assertEqual(response.status_code,404)
        self.assertNotContains(self.client.get(reverse('returns')),r.number)
        self.assertFalse(CreditNote.objects.exists())

    def test_permissions_independent_of_site_grants(self):
        viewer=User.objects.create_user('qa-return-viewer')
        viewer.user_permissions.add(Permission.objects.get(codename='view_returnrequest'))
        SiteAccess.objects.create(user=viewer,site=self.site)
        r=requested(self)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('return_detail',args=[r.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('return_new')).status_code,403)
        for action in ['approve','inspect','credit','cancel']:
            self.assertEqual(self.client.post(reverse('return_action',args=[r.pk,action]),{'reason':'test'}).status_code,403)

    def test_pending_returns_reserve_quantity_against_manual_credit_notes(self):
        r=requested(self,8)
        self.assertEqual(R.returnable(self.line),2)
        self.assertEqual(credit_room(self.line)[1],2)
        with self.assertRaises(ValidationError): requested(self,3)
        with self.assertRaises(ValidationError): create_credit_note(self.checker,self.invoice,'return',[(self.line,D(3),D(30))])
        self.assertFalse(CreditNote.objects.exists())
        R.cancel(self.maker,r.pk,'Dealer retained the bags')
        self.assertEqual(R.returnable(self.line),10)
        create_credit_note(self.checker,self.invoice,'return',[(self.line,D(2),D(20))])
        self.assertEqual(R.returnable(self.line),8)

    def test_request_key_retry_and_changed_payload(self):
        key=uuid4();r=requested(self,10,key)
        self.assertEqual(requested(self,10,key).pk,r.pk)
        with self.assertRaises(ValidationError): requested(self,9,key)
        self.assertEqual(ReturnRequest.objects.count(),1)

    def test_quantity_precision_nonfinite_and_foreign_lines_fail_without_writes(self):
        for value in ['NaN','Infinity','-1','0.0001','1e30']:
            with self.subTest(value=value),self.assertRaises(ValidationError):
                R.request_return(self.maker,self.invoice,'damaged','Bad number',{self.line:value})
        self.assertFalse(ReturnRequest.objects.exists())
        r=requested(self)
        R.decide(self.checker,r.pk,True,'Approved')
        for value in ['NaN','Infinity','-1','0.0001','11']:
            with self.subTest(value=value),self.assertRaises(ValidationError):
                R.inspect(self.inspector,r.pk,{r.lines.get().pk:(value,D(0),D(0),'')})
        r.refresh_from_db();self.assertEqual(r.status,'approved')

    def test_all_unaccepted_goods_close_without_stock_or_credit(self):
        r=inspected(self,0,0)
        self.assertEqual(r.status,'closed')
        self.assertEqual(r.lines.get().not_accepted,10)
        self.assertEqual(R.returnable(self.line),10)
        self.assertEqual(stock.position([self.site]),[])
        with self.assertRaises(ValidationError): R.credit(self.checker,r.pk)
        self.assertFalse(CreditNote.objects.exists())

    def test_partial_inspection_releases_only_unaccepted_reservation(self):
        r=inspected(self,5,2)
        self.assertEqual(R.returnable(self.line),3)
        self.assertEqual(stock.position([self.site])[0]['hold'],7)
        R.credit(self.checker,r.pk)
        self.assertEqual(R.returnable(self.line),3)
        r2=requested(self,3)
        R.decide(self.checker,r2.pk,True,'Remaining bags verified')
        R.inspect(self.inspector,r2.pk,{r2.lines.get().pk:(D(3),D(1),D(2),'')})
        R.credit(self.checker,r2.pk)
        values=sum((b.value for b in StockBalance.objects.filter(site=self.site,product=self.bag)),D(0))
        self.assertEqual(values,D('20.03'))

    def test_pickup_dates_and_post_arrival_cancellation(self):
        r=requested(self);R.decide(self.checker,r.pk,True,'Approved')
        for on,vehicle in [(self.today+timedelta(days=1),'MP04GA1234'),(self.today-timedelta(days=1),'MP04GA1234'),(self.today,'!')]:
            with self.assertRaises(ValidationError): R.pickup(self.inspector,r.pk,on,vehicle)
        R.pickup(self.inspector,r.pk,self.today,'mp04 ga1234')
        with self.assertRaises(ValidationError): R.cancel(self.maker,r.pk,'Cannot discard custody')
        R.inspect(self.inspector,r.pk,{r.lines.get().pk:(D(10),D(10),D(0),'')})
        with self.assertRaises(ValidationError): R.cancel(self.checker,r.pk,'Cannot discard quarantine')

    def test_inspection_counts_and_reasons(self):
        r=requested(self);R.decide(self.checker,r.pk,True,'Approved');pk=r.lines.get().pk
        for results in [{},{pk:(D(10),D(8),D(3),'')},{pk:(D(10),D(1),D(0),'')}]:
            with self.assertRaises(ValidationError): R.inspect(self.inspector,r.pk,results)
        self.assertEqual(r.lines.get().received,0)

    def test_prior_discount_limits_refund_without_losing_return_quantity(self):
        create_credit_note(self.checker,self.invoice,'rate',[(self.line,D(0),D(20))])
        r=inspected(self,10,0);cn=R.credit(self.checker,r.pk).credit_note
        self.assertEqual((cn.taxable,cn.lines.get().quantity),(80,10))
        self.assertEqual(StockBalance.objects.get(site=self.site,product=self.bag,status='available').value,D('20.03'))

    def test_closed_period_rolls_back_credit_stock_and_return_state(self):
        r=inspected(self)
        gl.period_for(self.today)
        FinancialPeriod.objects.filter(start__lte=self.today,end__gte=self.today).update(status='closed')
        before=JournalEntry.objects.count()
        with self.assertRaises(ValidationError): R.credit(self.checker,r.pk)
        r.refresh_from_db();self.assertEqual(r.status,'inspected')
        self.assertFalse(CreditNote.objects.exists())
        self.assertEqual(StockBalance.objects.get(site=self.site,product=self.bag,status='available').quantity,0)
        self.assertEqual(JournalEntry.objects.count(),before)

    def test_complaint_link_must_match_invoice_and_permission(self):
        complaint=Complaint.objects.create(number='QA-COMPLAINT',partner=self.customer,invoice=self.invoice,category='quality',description='QA',due_on=self.today,created_by=self.maker)
        r=R.request_return(self.maker,self.invoice,'quality','QA complaint',{self.line:D(1)},complaint)
        self.assertEqual(r.complaint_id,complaint.pk)
        complaint.invoice=None;complaint.save(update_fields=['invoice'])
        with self.assertRaises(ValidationError): R.request_return(self.maker,self.invoice,'quality','Unlinked',{self.line:D(1)},complaint)

    def test_new_form_retry_error_handling_and_views(self):
        response=self.client.get(reverse('return_new')+f'?invoice_id={self.invoice.pk}')
        self.assertEqual(response.status_code,200)
        key=response.context['submission_key']
        payload={'invoice':self.invoice.number,'save':'1','reason':'damaged','description':'QA browser return',f'qty_{self.line.pk}':'10','submission_key':key}
        first=self.client.post(reverse('return_new'),payload)
        second=self.client.post(reverse('return_new'),payload)
        self.assertEqual(first.status_code,302);self.assertEqual(first.url,second.url)
        self.assertEqual(ReturnRequest.objects.count(),1)
        for url in [reverse('returns'),first.url,reverse('return_new')+'?invoice_id=bad']:
            self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(reverse('return_new'),dict(payload,complaint='bad')).status_code,200)

    def test_shared_credit_service_rejects_scope_amounts_and_damaged_bypass(self):
        for actor in [self.outsider]:
            with self.assertRaises(PermissionDenied): create_credit_note(actor,self.invoice,'return',[(self.line,D(1),D(10))])
        for rows in [[(self.line,D(11),D(110))],[(self.line,D(1),D(101))],[(self.line,D('NaN'),D(10))],[(self.line,D(1),D(10))]*2]:
            with self.assertRaises(ValidationError): create_credit_note(self.checker,self.invoice,'return',rows)
        with self.assertRaises(ValidationError): create_credit_note(self.checker,self.invoice,'return',[(self.line,D(1),D(10))],damaged={self.line.pk:D(1)})
        self.assertFalse(CreditNote.objects.exists())

    def test_work_queue_and_arrival_filter_match_available_actions(self):
        from .insights import my_queue
        r=inspected(self)
        self.assertFalse(any(item['label']=='Returns waiting for credit note' for item in my_queue(self.inspector)))
        self.assertTrue(any(item['label']=='Returns waiting for credit note' for item in my_queue(self.checker)))
        self.client.force_login(self.checker)
        self.assertNotContains(self.client.get(reverse('returns')+'?status=arrival'),r.number)

    def test_rejection_preserves_history_and_unreserves_goods(self):
        r=requested(self)
        with self.assertRaises(ValidationError): R.decide(self.checker,r.pk,False,'')
        R.decide(self.checker,r.pk,False,'Insufficient evidence')
        self.assertEqual(R.returnable(self.line),10)
        self.assertTrue(AuditEvent.objects.filter(reference=r.number,action='Return rejected').exists())
        with self.assertRaises(ValidationError): R.inspect(self.inspector,r.pk,{})
