from decimal import Decimal as D
from unittest.mock import patch
from io import StringIO
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.contrib.auth.models import User,Permission
from django.core.management import call_command
from django.urls import reverse
from accounts.test_support import force_login_recent
from .test_support import seed_business_data
from .models import Order,OrderLine,StockReservation,StockReservationEvent,StockBalance,SiteAccess
from .services import move_stock,transition_order
from . import reservations as R,sales,stock

@override_settings(EMAIL_OTP_ENABLED=False)
class ReservationTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        move_stock(self.maker,self.site,self.bag,D(100),'res:opening','Opening',unit_cost=D(300))

    def order(self,quantity=60,approve=True,extra=None):
        order=Order.objects.create(kind='Sale',site=self.site,partner=self.customer,created_by=self.maker)
        OrderLine.objects.create(order=order,product=self.bag,quantity=quantity,rate=350,gst_rate=18)
        if extra:OrderLine.objects.create(order=order,product=self.bag,quantity=extra,rate=350,gst_rate=18)
        transition_order(self.maker,order.pk,'submit',confirm_duplicate=True)
        if approve:transition_order(self.checker,order.pk,'approve',backorder_reason='Replenishment expected')
        order.refresh_from_db();return order

    def test_approval_creates_firm_commitment_and_protects_other_issues(self):
        order=self.order(70);line=order.lines.get();row=line.stock_reservation
        self.assertEqual(row.remaining,70)
        with self.assertRaisesMessage(ValidationError,'firmly reserved'):move_stock(self.maker,self.site,self.bag,-31,'other:issue','Consumption')
        move_stock(self.maker,self.site,self.bag,-20,'free:issue','Consumption')
        transition_order(self.maker,order.pk,'post')
        row.refresh_from_db();self.assertEqual((row.remaining,row.consumed),(0,70))
        self.assertEqual(StockBalance.objects.get(site=self.site,product=self.bag,status='available').quantity,10)
        self.assertEqual(row.events.filter(action='consume').count(),1)
        with self.assertRaises(ValidationError):transition_order(self.maker,order.pk,'post')

    def test_partial_backorder_allocation_and_replenishment(self):
        first=self.order(60);second=self.order(60)
        self.assertEqual(first.lines.get().stock_reservation.remaining,60)
        self.assertEqual(second.lines.get().stock_reservation.remaining,40)
        position=stock.position([self.site])[0]
        self.assertEqual((position['reserved'],position['free'],position['unallocated']),(100,0,20))
        move_stock(self.maker,self.site,self.bag,20,'res:replenish','Opening',unit_cost=300)
        R.allocate_order(self.maker,second.pk)
        self.assertEqual(second.lines.get().stock_reservation.remaining,60)
        count=StockReservationEvent.objects.count();R.allocate_order(self.maker,second.pk)
        self.assertEqual(StockReservationEvent.objects.count(),count)

    def test_repeated_product_lines_cannot_double_promise_in_atp_plant(self):
        self.site.atp_required=True;self.site.save()
        order=self.order(60,approve=False,extra=60)
        with self.assertRaisesMessage(ValidationError,'approves only against free stock'):transition_order(self.checker,order.pk,'approve')
        self.assertFalse(StockReservation.objects.exists())
        order.refresh_from_db();self.assertEqual(order.status,'Submitted')

    def test_cancel_and_revision_release_without_physical_stock_movement(self):
        order=self.order(70);count=order.lines.get().stock_reservation.events.count()
        transition_order(self.maker,order.pk,'cancel',reason='Customer withdrew demand')
        self.assertEqual(order.lines.get().stock_reservation.remaining,0)
        self.assertEqual(order.lines.get().stock_reservation.events.count(),count+1)
        another=self.order(50);new=sales.revise(self.maker,another.pk)
        self.assertEqual(another.lines.get().stock_reservation.remaining,0)
        self.assertFalse(StockReservation.objects.filter(order_line__order=new).exists())
        self.assertEqual(StockBalance.objects.get(site=self.site,product=self.bag,status='available').quantity,100)

    def test_hold_preserves_commitment_and_prevents_new_allocation(self):
        order=self.order(70);transition_order(self.maker,order.pk,'hold',reason='Waiting for customer')
        self.assertEqual(order.lines.get().stock_reservation.remaining,70)
        with self.assertRaises(ValidationError):R.allocate_order(self.maker,order.pk)
        transition_order(self.maker,order.pk,'release')
        self.assertEqual(order.lines.get().stock_reservation.remaining,70)

    def test_issue_failure_rolls_back_consumption_and_physical_balance(self):
        order=self.order(70);events=StockReservationEvent.objects.count()
        with patch('erp.gl.post_stock',side_effect=ValidationError('Closed period')):
            with self.assertRaises(ValidationError):transition_order(self.maker,order.pk,'post')
        row=order.lines.get().stock_reservation;self.assertEqual((row.remaining,row.consumed),(70,0))
        self.assertEqual(StockReservationEvent.objects.count(),events)
        self.assertEqual(StockBalance.objects.get(site=self.site,product=self.bag,status='available').quantity,100)
        order.refresh_from_db();self.assertEqual(order.status,'Approved')

    def test_wrong_product_or_order_line_cannot_consume_reservation(self):
        order=self.order(70)
        move_stock(self.maker,self.site,self.raw,10,'res:raw','Opening',unit_cost=100)
        with self.assertRaisesMessage(ValidationError,'current approved'):move_stock(self.maker,self.site,self.raw,-1,'wrong:product','Dispatch',reservation_line=order.lines.get())

    def test_legacy_audit_is_read_only_and_legacy_demand_is_identified(self):
        order=Order.objects.create(kind='Sale',status='Approved',site=self.site,partner=self.customer,created_by=self.maker)
        OrderLine.objects.create(order=order,product=self.bag,quantity=30,rate=350,gst_rate=18)
        output=StringIO();call_command('audit_reservations',stdout=output)
        self.assertIn('Untracked open sales lines: 1',output.getvalue());self.assertFalse(StockReservation.objects.exists())
        new=self.order(80)
        self.assertEqual(new.lines.get().stock_reservation.remaining,70)
        R.allocate_order(self.maker,order.pk)
        self.assertEqual(order.lines.get().stock_reservation.remaining,30)
        self.assertEqual(R.reserved(self.site,self.bag),100)

    def test_allocation_requires_site_and_stock_post_authority(self):
        order=self.order(70)
        with self.assertRaises(Order.DoesNotExist):R.allocate_order(self.outsider,order.pk)
        reader=User.objects.create_user('res-reader');reader.user_permissions.add(Permission.objects.get(codename='view_sales_order'));SiteAccess.objects.create(user=reader,site=self.site)
        with self.assertRaises(PermissionDenied):R.allocate_order(reader,order.pk)

    def test_restock_restores_consumed_commitment_and_ledger_receipt(self):
        order=self.order(70);line=order.lines.get()
        move_stock(self.maker,self.site,self.bag,-20,'res:partial','Dispatch',reservation_line=line)
        self.assertEqual(line.stock_reservation.consumed,20)
        move_stock(self.maker,self.site,self.bag,20,'res:partial:return','Return',unit_cost=300)
        R.restore(self.maker,line,20,'res:partial')
        row=StockReservation.objects.get(order_line=line)
        self.assertEqual((row.remaining,row.consumed),(70,0))
        self.assertTrue(row.events.filter(action='restore',quantity=20).exists())

    def test_web_allocation_receipt_shows_reservation_history(self):
        order=self.order(120)
        move_stock(self.maker,self.site,self.bag,20,'res:incoming','Opening',unit_cost=300)
        force_login_recent(self.client,self.maker)
        self.client.post(reverse('order_action',args=[order.pk,'allocate']))
        page=self.client.get(reverse('order_detail',args=[order.pk]))
        self.assertContains(page,'Firm stock reservations')
        self.assertEqual(order.lines.get().stock_reservation.remaining,120)

    def test_reviewed_deallocation_allows_damage_and_preserves_approved_backorder(self):
        order=self.order(100)
        with self.assertRaises(ValidationError):move_stock(self.maker,self.site,self.bag,-5,'res:damage-blocked','Damage')
        with self.assertRaises(ValidationError):R.release_order(self.maker,order.pk,'')
        R.release_order(self.maker,order.pk,'Five bags wet; inspect before reallocating')
        move_stock(self.maker,self.site,self.bag,-5,'res:damaged','Damage')
        order.refresh_from_db();self.assertEqual(order.status,'Approved')
        R.allocate_order(self.maker,order.pk)
        self.assertEqual(order.lines.get().stock_reservation.remaining,95)
        self.assertEqual(stock.position([self.site])[0]['unallocated'],5)


@override_settings(EMAIL_OTP_ENABLED=False)
class DispatchReservationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from . import test_logistics as fixture
        fixture.LogisticsTests.setUpTestData.__func__(cls)
    def setUp(self):
        from . import test_logistics as fixture
        fixture.LogisticsTests.setUp(self)

    def test_other_order_firm_stock_cannot_be_promised_to_new_truck(self):
        from . import logistics as L
        from .models import Dispatch
        R.allocate_order(self.admin,self.order.pk)
        other=Order.objects.create(kind='Sale',site=self.site,partner=self.dealer,created_by=self.admin,status='Approved')
        line=OrderLine.objects.create(order=other,product=self.cement,quantity=600,rate=380,gst_rate=18)
        with self.assertRaisesMessage(ValidationError,'allocate sufficient free stock'):L.plan(self.admin,other,self.truck,self.driver,{line:500})
        self.assertFalse(Dispatch.objects.exists())
        self.assertFalse(StockReservation.objects.filter(order_line=line).exists())

    def test_planned_unissued_quantity_is_deducted_from_own_commitment(self):
        from . import logistics as L
        from .models import Dispatch
        other=Order.objects.create(kind='Sale',site=self.site,partner=self.dealer,created_by=self.admin,status='Approved')
        OrderLine.objects.create(order=other,product=self.cement,quantity=700,rate=380,gst_rate=18)
        R.allocate_order(self.admin,other.pk)
        L.plan(self.admin,self.order,self.truck,self.driver,{self.line:250})
        truck=type(self.truck).objects.get(pk=self.truck.pk);truck.pk=None;truck.registration='MP04GA5678';truck.save()
        with self.assertRaisesMessage(ValidationError,'allocate sufficient free stock'):L.plan(self.admin,self.order,truck,self.driver,{self.line:100})
        self.assertEqual(Dispatch.objects.count(),1)
        with self.assertRaisesMessage(ValidationError,'active truck advice'):R.release_order(self.admin,self.order.pk,'Unsafe release')
