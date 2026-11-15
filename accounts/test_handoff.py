"""Handoffs preserve authority, independent review, source evidence and replay semantics."""
from datetime import timedelta
from io import StringIO
from uuid import uuid4

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import approval_work as AW, work_handoff as H
from accounts.models import ApprovalRouting, WorkHandoffRun, WorkHandoffItem, UserProfile
from accounts.test_support import force_login_recent
from erp import complaints as CM, support as S
from erp.models import AuditEvent, Order, OrderLine, SiteAccess, SalesProject, CRMLead, BeatPlan
from erp.services import transition_order
from erp.test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class HandoffTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today=timezone.localdate()
        self.password='Handoff-Verification-816!'
        self.admin=User.objects.create_superuser('handoff-admin','admin@sistech.co.in',self.password)
        self.backup=User.objects.create_user('handoff-backup','backup@sistech.co.in',self.password)
        self.backup.user_permissions.set(Permission.objects.filter(content_type__app_label='erp'))
        SiteAccess.objects.create(user=self.backup,site=self.site)

    def order(self,maker=None):
        order=Order.objects.create(kind='Sale',site=self.site,partner=self.customer,created_by=maker or self.maker)
        OrderLine.objects.create(order=order,product=self.bag,quantity=10,rate=350,gst_rate=18)
        transition_order(maker or self.maker,order.pk,'submit',confirm_duplicate=True)
        order.refresh_from_db()
        return order

    def ticket(self):
        ticket=S.raise_ticket(self.maker,'Verification support','Verification help required for this screen')
        S.assign(self.admin,ticket.pk,self.checker)
        ticket.refresh_from_db()
        return ticket

    def transfer(self,source=None,recipient=None):
        source,recipient=source or self.checker,recipient or self.backup
        rows,stamp=H.preview(source,recipient)
        return H.handoff(self.admin,source,recipient,'Planned cover',uuid4(),stamp)

    def test_assignment_requires_existing_scope_authority_and_independence(self):
        order=self.order()
        for person in [self.maker,self.outsider]:
            with self.assertRaises(ValidationError):
                H.assign_approval(self.admin,'sales_order',order.pk,person,'Independent review',uuid4())
        plain=User.objects.create_user('read-only')
        plain.user_permissions.add(Permission.objects.get(codename='view_sales_order'))
        SiteAccess.objects.create(user=plain,site=self.site)
        with self.assertRaises(ValidationError):
            H.assign_approval(self.admin,'sales_order',order.pk,plain,'No authority',uuid4())
        with self.assertRaises(PermissionDenied):
            H.assign_approval(self.checker,'sales_order',order.pk,self.backup,'Forbidden admin action',uuid4())
        self.assertFalse(ApprovalRouting.objects.exists())

    def test_assignment_retries_do_not_grant_permissions_or_decide_source(self):
        order=self.order()
        key=uuid4()
        before=set(self.checker.user_permissions.values_list('pk',flat=True))
        first=H.assign_approval(self.admin,'sales_order',order.pk,self.checker,'Reviewer cover',key)
        retry=H.assign_approval(self.admin,'sales_order',order.pk,self.checker,'Reviewer cover',key)
        self.assertEqual(first.pk,retry.pk)
        self.assertEqual(WorkHandoffItem.objects.count(),1)
        with self.assertRaises(ValidationError):
            H.assign_approval(self.admin,'sales_order',order.pk,self.backup,'Changed recipient',key)
        order.refresh_from_db()
        self.assertEqual((order.status,order.approved_by_id),('Submitted',None))
        self.assertEqual(set(self.checker.user_permissions.values_list('pk',flat=True)),before)

    def test_disable_reroutes_assigned_approval_after_security_commit(self):
        order=self.order()
        H.assign_approval(self.admin,'sales_order',order.pk,self.checker,'Assigned checker',uuid4())
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':self.backup})
        force_login_recent(self.client,self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(reverse('member_status',args=[self.checker.pk]))
        self.assertEqual(response.status_code,302)
        self.checker.refresh_from_db()
        self.assertFalse(self.checker.is_active)
        routing=ApprovalRouting.objects.get()
        self.assertEqual(routing.assignee_id,self.backup.pk)
        run=WorkHandoffRun.objects.filter(automatic=True).get()
        self.assertEqual((run.transferred,run.blocked),(1,0))
        self.assertIsNone(run.actor_id)
        self.assertIsNotNone(run.source_event_id)
        order.refresh_from_db()
        self.assertEqual(order.status,'Submitted')
        force_login_recent(self.client,self.backup)
        self.assertContains(self.client.get(reverse('approval_inbox')),order.number)
        transition_order(self.backup,order.pk,'approve',backorder_reason='Scheduled supply')
        self.assertEqual(H.assigned_pending(self.backup),[])
        order.refresh_from_db()
        self.assertEqual(order.approved_by_id,self.backup.pk)
        self.assertEqual(order.created_by_id,self.maker.pk)

    def test_missing_backup_is_attention_and_retry_after_configuration_is_safe(self):
        ticket=self.ticket()
        User.objects.filter(pk=self.checker.pk).update(is_active=False)
        first=H.automatic_handoff(self.checker.pk)
        retry=H.automatic_handoff(self.checker.pk)
        self.assertEqual((first.status,first.blocked),('attention',1))
        self.assertEqual(first.pk,retry.pk)
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.checker.pk)
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':self.backup})
        run=H.automatic_handoff(self.checker.pk)
        self.assertNotEqual(run.pk,first.pk)
        self.assertEqual(run.transferred,1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.backup.pk)
        self.assertIsNone(H.automatic_handoff(self.checker.pk))

    def test_expiry_cleanup_and_retry_command_handoff_pending_work(self):
        ticket=self.ticket()
        UserProfile.objects.update_or_create(user=self.checker,defaults=dict(work_backup=self.backup,
            access_expires_at=timezone.now()-timedelta(seconds=1)))
        with self.captureOnCommitCallbacks(execute=True):
            call_command('expire_temporary_access',stdout=StringIO())
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.backup.pk)
        count=WorkHandoffRun.objects.count()
        call_command('reassign_unavailable_work',stdout=StringIO())
        self.assertEqual(WorkHandoffRun.objects.count(),count)

    def test_stale_preview_and_key_payload_changes_do_not_move_work(self):
        ticket=self.ticket()
        rows,stamp=H.preview(self.checker,self.backup)
        ticket.subject='Changed while preview open'
        ticket.save()
        with self.assertRaisesMessage(ValidationError,'changed'):
            H.handoff(self.admin,self.checker,self.backup,'Cover',uuid4(),stamp)
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.checker.pk)
        key=uuid4()
        _,stamp=H.preview(self.checker,self.backup)
        first=H.handoff(self.admin,self.checker,self.backup,'Cover',key,stamp)
        retry=H.handoff(self.admin,self.checker,self.backup,'Cover',key,stamp)
        self.assertEqual(first.pk,retry.pk)
        with self.assertRaises(ValidationError):
            H.handoff(self.admin,self.checker,self.backup,'Changed reason',key,stamp)

    def test_partial_handoff_keeps_closed_evidence_and_flags_missing_scope(self):
        ticket=self.ticket()
        closed=S.raise_ticket(self.maker,'Closed verification','Closed support evidence must stay retained')
        S.assign(self.admin,closed.pk,self.checker)
        S.resolve(self.admin,closed.pk,'Completed')
        project=SalesProject.objects.create(number='HAND-P1',site=self.site,owner=self.checker,
            name='Pending opportunity',location='Bhopal',district='Bhopal',contact_name='Verification',
            mobile='9876543210',created_by=self.maker,dedupe_key='HAND-P1')
        run=self.transfer(recipient=self.outsider)
        self.assertEqual((run.transferred,run.blocked),(1,1))
        ticket.refresh_from_db();closed.refresh_from_db();project.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.outsider.pk)
        self.assertEqual(closed.assigned_to_id,self.checker.pk)
        self.assertEqual(project.owner_id,self.checker.pk)
        self.assertEqual(project.created_by_id,self.maker.pk)

    def test_complaint_and_capa_former_owners_stay_disqualified_after_handoff(self):
        from erp.test_schemes import invoice_fixture
        invoice=invoice_fixture(self,[(self.bag,350)])
        complaint=CM.log(self.maker,self.customer,'quality','Verification complaint',invoice)
        CM.investigate(self.checker,complaint.pk,'Independent investigation')
        capa=CM.add_capa(self.maker,complaint,'Storage action','Wet floor','Use pallets',self.checker,self.today+timedelta(days=1))
        run=self.transfer()
        self.assertEqual(run.transferred,2)
        complaint.refresh_from_db();capa.refresh_from_db()
        self.assertEqual((complaint.owner_id,capa.owner_id),(self.backup.pk,self.backup.pk))
        with self.assertRaises(ValidationError):
            CM.decide(self.checker,complaint.pk,'reject',0,'none','Former investigator cannot decide')
        CM.implement(self.backup,capa.pk,'Action completed')
        for person in [self.checker,self.backup]:
            with self.assertRaises(ValidationError):
                CM.verify(person,capa.pk,True,'Not independent')
        CM.verify(self.admin,capa.pk,True,'Independent effectiveness evidence')
        self.assertEqual(capa.created_by_id,self.maker.pk)

    def test_handoff_does_not_rewrite_submitted_crm_or_approved_route_evidence(self):
        lead=CRMLead.objects.create(number='HAND-LEAD',site=self.site,owner=self.checker,business_name='Pending lead',
            mobile='9876543210',created_by=self.maker)
        plan=BeatPlan.objects.create(number='HAND-BEAT',site=self.site,owner=self.checker,on=self.today,
            title='Approved route',status='approved',created_by=self.maker)
        run=self.transfer()
        self.assertEqual((run.transferred,run.blocked),(0,2))
        lead.refresh_from_db();plan.refresh_from_db()
        self.assertEqual(lead.owner_id,self.checker.pk)
        self.assertEqual((plan.owner_id,plan.status),(self.checker.pk,'approved'))

    def test_admin_pages_recent_confirmation_and_private_inbox(self):
        order=self.order()
        H.assign_approval(self.admin,'sales_order',order.pk,self.checker,'Review assignment',uuid4())
        force_login_recent(self.client,self.outsider)
        for name in ['approval_board','work_handoff']:
            self.assertEqual(self.client.get(reverse(name)).status_code,403)
        self.assertNotContains(self.client.get(reverse('approval_inbox')),order.number)
        force_login_recent(self.client,self.checker)
        self.assertContains(self.client.get(reverse('approval_inbox')),order.number)
        self.checker.user_permissions.remove(Permission.objects.get(codename='approve_sales_order'))
        self.assertNotContains(self.client.get(reverse('approval_inbox')),order.number)
        self.client.force_login(self.admin)
        response=self.client.post(reverse('approval_assign',args=['sales_order',order.pk]),{})
        self.assertTrue(response.url.startswith(reverse('reauthenticate')))
        force_login_recent(self.client,self.admin)
        for name in ['approval_board','work_handoff']:
            self.assertEqual(self.client.get(reverse(name)).status_code,200)

    def test_catalog_urls_are_valid_and_creator_routes_are_hidden(self):
        order=self.order()
        task=AW.source('sales_order',order.pk)
        self.assertEqual(AW.source_url(task),reverse('order_detail',args=[order.pk]))
        self.assertFalse(AW.eligible(self.maker,task)[0])
        self.assertTrue(AW.eligible(self.checker,task)[0])
        self.assertIsNone(AW.source('unknown',order.pk))

    def test_automatic_handoff_never_uses_a_backup_who_is_the_source_maker(self):
        order=self.order()
        H.assign_approval(self.admin,'sales_order',order.pk,self.checker,'Review assignment',uuid4())
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':self.maker})
        User.objects.filter(pk=self.checker.pk).update(is_active=False)
        run=H.automatic_handoff(self.checker.pk)
        self.assertEqual((run.transferred,run.blocked),(0,1))
        self.assertEqual(ApprovalRouting.objects.get().assignee_id,self.checker.pk)
        self.assertEqual(H.unavailable_backlog()[0]['attention'],1)
        order.refresh_from_db()
        self.assertEqual((order.status,order.approved_by_id),('Submitted',None))

    def test_completed_sources_drop_out_of_live_backlog_without_rewriting_history(self):
        order=self.order()
        receipt=H.assign_approval(self.admin,'sales_order',order.pk,self.checker,'Review assignment',uuid4())
        transition_order(self.backup,order.pk,'approve',backorder_reason='Scheduled supply')
        User.objects.filter(pk=self.checker.pk).update(is_active=False)
        self.assertIsNone(H.automatic_handoff(self.checker.pk))
        self.assertEqual(H.unavailable_backlog(),[])
        self.assertEqual(receipt.items.get().source_user_id,None)
        routing=ApprovalRouting.objects.get()
        self.assertEqual(routing.assignee_id,self.checker.pk)
        order.refresh_from_db()
        self.assertEqual(order.approved_by_id,self.backup.pk)

    def test_recipient_scope_change_after_preview_requires_fresh_review(self):
        ticket=self.ticket()
        _,stamp=H.preview(self.checker,self.backup)
        self.backup.user_permissions.remove(Permission.objects.get(codename='handle_supportticket'))
        with self.assertRaisesMessage(ValidationError,'changed'):
            H.handoff(self.admin,self.checker,self.backup,'Cover',uuid4(),stamp)
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.checker.pk)
        self.assertFalse(WorkHandoffRun.objects.exists())

    def test_project_and_collection_owners_change_without_rewriting_values_or_authors(self):
        from erp import collect
        project=SalesProject.objects.create(number='HAND-POS',site=self.site,owner=self.checker,
            name='Pending opportunity',location='Bhopal',district='Bhopal',contact_name='Verification',
            mobile='9876543210',created_by=self.maker,dedupe_key='HAND-POS',estimated_value=12345)
        followup=collect.record(self.maker,self.customer,'promise','Payment expected',
            promised_amount=100,promised_date=self.today+timedelta(days=1),owner=self.checker)
        run=self.transfer()
        self.assertEqual(run.transferred,2)
        project.refresh_from_db();followup.refresh_from_db()
        self.assertEqual((project.owner_id,followup.owner_id),(self.backup.pk,self.backup.pk))
        self.assertEqual((project.estimated_value,followup.promised_amount),(12345,100))
        self.assertEqual((project.created_by_id,followup.created_by_id),(self.maker.pk,self.maker.pk))

    def test_backup_policy_is_admin_only_and_cannot_point_to_self_or_expired_user(self):
        from accounts.forms import MemberForm
        data=dict(first_name='Checker',last_name='',email=self.checker.email,is_active='on',
                  work_backup=self.checker.pk)
        form=MemberForm(data,instance=self.checker)
        self.assertFalse(form.is_valid())
        self.assertIn('work_backup',form.errors)
        UserProfile.objects.update_or_create(user=self.backup,defaults={'access_expires_at':timezone.now()-timedelta(seconds=1)})
        form=MemberForm(dict(data,work_backup=self.backup.pk),instance=self.checker)
        self.assertFalse(form.is_valid())
        self.assertIn('work_backup',form.errors)

    def test_actual_investigator_and_long_title_capa_implementer_cannot_review_their_work(self):
        from erp.test_schemes import invoice_fixture
        complaint=CM.log(self.maker,self.customer,'short','Verification short delivery',
                         invoice_fixture(self,[(self.bag,350)]),owner=self.checker)
        CM.investigate(self.backup,complaint.pk,'Investigation by another staff member',owner=self.checker)
        with self.assertRaises(ValidationError):
            CM.decide(self.backup,complaint.pk,'reject',0,'none','Cannot decide own findings')
        capa=CM.add_capa(self.maker,complaint,'X'*150,'Cause','Corrective action',self.checker,self.today+timedelta(days=1))
        CM.implement(self.backup,capa.pk,'Implemented by another staff member')
        with self.assertRaises(ValidationError):
            CM.verify(self.backup,capa.pk,True,'Cannot verify own implementation')
        references=AuditEvent.objects.filter(action__startswith='CAPA').values_list('reference',flat=True)
        self.assertTrue(all(len(reference)<=100 for reference in references))

    def test_live_catalog_screens_and_review_stage_links(self):
        from erp import schemes, closing, gl
        from erp.models import FinancialYearClose
        from erp.test_schemes import approved_scheme
        version=approved_scheme(self)
        revised=schemes.revise(self.maker,version.scheme_id)
        schemes.submit(self.maker,revised.pk,'Review revised rules')
        task=AW.source('scheme_rules',revised.pk)
        self.assertEqual(AW.source_url(task),reverse('scheme_detail',args=[revised.pk]))
        previous_month=self.today.replace(day=1)-timedelta(days=1)
        period=gl.period_for(previous_month)
        self.assertIsNone(AW.source('period_close',period.pk))
        closing.prepare_period(self.maker,previous_month,'Verification month review',
            tax_reviewed=True,stock_reviewed=True,bank_reviewed=True)
        task=AW.source('period_close',period.pk)
        self.assertFalse(AW.eligible(self.maker,task)[0])
        self.assertTrue(AW.eligible(self.checker,task)[0])
        current_start,_=closing.year_bounds(self.today)
        start,end=closing.year_bounds(current_start-timedelta(days=1))
        year=FinancialYearClose.objects.create(start=start,end=end,prepared_by=self.maker,note='Verification annual proposal')
        self.assertIsNotNone(AW.source('year_close',year.pk))
        self.assertIsNone(AW.source('year_reopen',year.pk))
        force_login_recent(self.client,self.admin)
        for kind in AW.CATALOG:
            response=self.client.get(reverse('approval_board'),{'kind':kind})
            self.assertEqual(response.status_code,200,kind)
        year.status='reopen_requested'
        year.reopen_requested_by=self.checker
        year.save()
        task=AW.source('year_reopen',year.pk)
        self.assertIsNotNone(task)
        self.assertFalse(AW.eligible(self.checker,task)[0])

    def test_preview_is_bound_to_the_selected_recipient_even_with_equal_authority(self):
        ticket=self.ticket()
        _,stamp=H.preview(self.checker,self.backup)
        with self.assertRaisesMessage(ValidationError,'changed'):
            H.handoff(self.admin,self.checker,self.admin,'Changed recipient',uuid4(),stamp)
        ticket.refresh_from_db()
        self.assertEqual(ticket.assigned_to_id,self.checker.pk)
        self.assertFalse(WorkHandoffRun.objects.exists())
