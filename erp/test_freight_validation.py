"""Invalid monetary input and stale trip instances cannot create freight claims."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import User,Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.test_support import force_login_recent
from . import freight as F,test_freight as fixture
from .models import FreightBill,FreightBillLine,AuditEvent,Site,SiteAccess


@override_settings(EMAIL_OTP_ENABLED=False)
class FreightValidationTests(TestCase):
    trip=fixture.FreightTests.trip

    @classmethod
    def setUpTestData(cls):fixture.FreightTests.setUpTestData.__func__(cls)

    def setUp(self):self.dispatch=self.trip(self.truck,self.driver,100)

    def create(self,**changes):
        values=dict(user=self.admin,transporter=self.trans,bill_number='VALIDATION-FREIGHT',bill_date=timezone.localdate(),
            rows={self.dispatch:dict(claimed=self.dispatch.freight_amount,extra=0,days=0,reason='')},advance=0)
        values.update(changes);return F.create_bill(**values)

    def assert_no_claim(self):
        self.assertFalse(FreightBill.objects.exists());self.assertFalse(FreightBillLine.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(action='Freight bill entered').exists())
        self.assertTrue(F.billable_trips(self.trans).filter(pk=self.dispatch.pk).exists())

    def test_nonfinite_malformed_negative_and_overprecision_money_leave_no_claim(self):
        for field in ['claimed','extra']:
            for value in ['NaN','sNaN','Infinity','-Infinity','not-a-number','-0.01','1.001','1e99']:
                with self.subTest(field=field,value=value):
                    row=dict(claimed=100,extra=0,days=0,reason='Fictional extra charge');row[field]=value
                    with self.assertRaises(ValidationError):self.create(rows={self.dispatch:row})
                    self.assert_no_claim()

    def test_invalid_advance_and_detention_days_are_validated_before_writes(self):
        for value in ['NaN','Infinity','bad','-1','0.001','1e99']:
            with self.subTest(advance=value):
                with self.assertRaises(ValidationError):self.create(advance=value)
                self.assert_no_claim()
        for days in [-1,'1.5','NaN','Infinity',32768,True]:
            with self.subTest(days=days):
                with self.assertRaises(ValidationError):self.create(rows={self.dispatch:dict(claimed=100,extra=0,days=days,reason='')})
                self.assert_no_claim()

    def test_cached_contract_rate_and_state_do_not_override_database_trip(self):
        self.dispatch.freight_amount=Decimal('999999.00')
        bill=self.create(rows={self.dispatch:dict(claimed=10000,extra=2000,days=1,reason='Detention')})
        line=bill.lines.get()
        self.assertEqual(line.contracted,Decimal('9000.00'));self.assertEqual(line.allowed_extra,Decimal('1500.00'))
        self.assertEqual(bill.payable,Decimal('10500.00'))

    def test_forged_cached_site_does_not_bypass_persisted_site_boundary(self):
        site=Site.objects.create(code='VAL-OTHER',name='Other site',kind='Depot')
        user=User.objects.create_user('freight-validation-site')
        user.user_permissions.add(Permission.objects.get(codename='add_freightbill'));SiteAccess.objects.create(user=user,site=site)
        self.dispatch.site=site
        with self.assertRaises(PermissionDenied):self.create(user=user)
        self.assert_no_claim()

    def test_cached_eligible_state_cannot_reuse_already_billed_trip(self):
        first=self.create();before=first.payable
        with self.assertRaises(ValidationError):self.create(bill_number='OTHER-CLAIM')
        self.assertEqual(FreightBill.objects.count(),1);self.assertEqual(first.lines.count(),1)
        first.refresh_from_db();self.assertEqual(first.payable,before)

    def test_bill_date_reference_and_rejected_reference_collisions_are_clean_errors(self):
        for changes in [dict(bill_date=None),dict(bill_date='invalid'),dict(bill_date=timezone.localdate()+timedelta(days=1)),dict(bill_number=' '),dict(bill_number='X'*41)]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValidationError):self.create(**changes)
                self.assert_no_claim()
        first=self.create();F.decide(self.checker,first.pk,False,'Wrong evidence')
        for reference in ['VALIDATION-FREIGHT','validation-freight']:
            with self.assertRaises(ValidationError):self.create(bill_number=reference)
        self.assertEqual(FreightBill.objects.count(),1)
        self.assertTrue(F.billable_trips(self.trans).filter(pk=self.dispatch.pk).exists())

    def test_total_overflow_rolls_back_all_new_claim_lines(self):
        from .models import Dispatch
        amount=Decimal('9999999999.99')
        # Individually valid persisted trip amounts can exceed the header's money capacity.
        trips=Dispatch.objects.bulk_create([Dispatch(number=f'OVERFLOW-{i}',token=i+2,site=self.site,order=self.dispatch.order,
            vehicle=self.truck,driver=self.driver,transporter=self.trans,created_by=self.admin,status='delivered',
            freight_payable_by='company',freight_amount=amount) for i in range(101)])
        with self.assertRaises(ValidationError):
            self.create(rows={trip:dict(claimed=amount,extra=0,days=0,reason='') for trip in trips})
        self.assert_no_claim()
        self.assertEqual(F.billable_trips(self.trans).filter(pk__in=[trip.pk for trip in trips]).count(),101)

    def test_zero_and_exact_paise_values_preserve_existing_cap_and_advance_rules(self):
        bill=self.create(rows={self.dispatch:dict(claimed='100.01',extra='0.00',days='0',reason='')},advance='0.01')
        self.assertEqual(bill.payable,Decimal('100.00'));self.assertEqual(bill.claimed,Decimal('100.01'))
        self.assertEqual(bill.lines.get().detention_days,0)

    def test_http_nonfinite_advance_is_form_error_without_creating_claim(self):
        force_login_recent(self.client,self.admin)
        response=self.client.post(reverse('freight_bill_new',args=[self.trans.pk]),dict(bill_number='HTTP-NAN',bill_date=str(timezone.localdate()),advance='NaN',**{
            f'pick_{self.dispatch.pk}':'on',f'claimed_{self.dispatch.pk}':'9000',f'days_{self.dispatch.pk}':'0',f'extra_{self.dispatch.pk}':'0'}))
        self.assertEqual(response.status_code,200);self.assertContains(response,'Advance:')
        self.assert_no_claim()

    def test_http_nonfinite_claim_is_form_error_without_consuming_trip(self):
        force_login_recent(self.client,self.admin)
        response=self.client.post(reverse('freight_bill_new',args=[self.trans.pk]),dict(bill_number='HTTP-NAN-CLAIM',bill_date=str(timezone.localdate()),advance='0',**{
            f'pick_{self.dispatch.pk}':'on',f'claimed_{self.dispatch.pk}':'NaN',f'days_{self.dispatch.pk}':'0',f'extra_{self.dispatch.pk}':'0'}))
        self.assertEqual(response.status_code,200);self.assertContains(response,'Claimed freight:')
        self.assert_no_claim()
