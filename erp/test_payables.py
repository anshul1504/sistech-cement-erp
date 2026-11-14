from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import inbound as IB
from . import payables as AP
from .models import Site, Product, Partner, Order, OrderLine, StockBalance, SupplierInvoice, DebitNote, Organization
from .services import transition_order

D = Decimal
TODAY = timezone.localdate


@override_settings(EMAIL_OTP_ENABLED=False)
class PayablesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'match_qty_tolerance_pct': 0, 'match_price_tolerance_pct': 0})
        cls.site = Site.objects.create(code='DEP', name='Depot', kind='Depot', state='23')
        cls.coal = Product.objects.create(code='COAL', name='Coal', category='Fuel', unit='MT', gst_rate=5)
        cls.sup = Partner.objects.create(code='S1', name='Coal Co', kind='Supplier', state='23', gstin='23AAACC1234C1Z5', payment_terms_days=30)
        cls.ap = User.objects.create_superuser('ap-clerk', 'a@example.com', 'Original-Password-816!')
        cls.fc = User.objects.create_superuser('ap-fc', 'f@example.com', 'Original-Password-816!')
        o = Order.objects.create(kind='Purchase', site=cls.site, partner=cls.sup, created_by=cls.ap, payment_terms_days=45)
        OrderLine.objects.create(order=o, product=cls.coal, quantity=200, rate=9000, gst_rate=5)
        transition_order(cls.ap, o.pk, 'submit'); transition_order(cls.fc, o.pk, 'approve')
        cls.po = o

    def receive(self, challan, qty, accepted, rejected=0):
        g = IB.gate_in(self.ap, self.po, {'vehicle_number': f'MP04GA{challan[-1]}000', 'challan_number': challan}, {self.po.lines.get(): qty})
        line = g.lines.get()
        IB.post(self.ap, g.pk, {line: {'received': D(qty), 'accepted': D(accepted), 'rejected': D(rejected), 'damaged': D(0), 'hold': D(0), 'reason': 'wet' if rejected else ''}})
        return type(line).objects.get(pk=line.pk)

    def header(self, number, taxable, tax, total=None, gstin='23AAACC1234C1Z5', on=None):
        return {'invoice_number': number, 'invoice_date': on or TODAY(), 'supplier_gstin': gstin, 'billed_taxable': D(taxable), 'billed_tax': D(tax),
                'billed_total': D(total if total is not None else D(taxable) + D(tax))}

    def stock(self):
        return StockBalance.objects.get(site=self.site, product=self.coal).quantity

    def test_three_way_match_duplicates_and_exceptions(self):
        l1 = self.receive('C-1', 50, 48, 2)
        inv = AP.create_invoice(self.ap, self.sup, self.header('INV/001', 432000, 21600), {l1: (D(48), D(9000), D(5))})
        self.assertEqual((inv.status, inv.payable, inv.due_date), ('matched', D('453600'), TODAY() + timedelta(days=45)))
        self.assertEqual(AP.open_to_bill(l1), 0)
        with self.assertRaisesMessage(ValidationError, 'already entered'):
            AP.create_invoice(self.ap, self.sup, self.header('inv-001', 1, 0), {l1: (D(1), D(1), D(5))})
        l2 = self.receive('C-2', 50, 50)
        with self.assertRaisesMessage(ValidationError, 'possible duplicate'):
            AP.create_invoice(self.ap, self.sup, self.header('INV/002', 432000, 21600), {l2: (D(48), D(9000), D(5))})
        # Over-billing: 52 MT at ₹9,100 against 50 accepted at ₹9,000
        bad = AP.create_invoice(self.ap, self.sup, self.header('INV/003', 473200, 23660), {l2: (D(52), D(9100), D(5))})
        self.assertEqual((bad.status, bad.payable), ('exception', 0))
        self.assertTrue(any('only 50' in i and 'above PO' in i for i in bad.issues))
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            AP.decide(self.ap, bad.pk, 'matched', 'x')
        with self.assertRaisesMessage(ValidationError, 'Record why'):
            AP.decide(self.fc, bad.pk, 'matched', '')
        AP.decide(self.fc, bad.pk, 'matched', 'Rate revision not agreed')
        bad.refresh_from_db()
        dn = bad.debit_notes.get()
        self.assertEqual((bad.status, bad.payable, dn.total), ('approved', D('472500.00'), D('24360.00')))
        self.assertEqual(AP.balance(self.sup)['balance'], D('926100.00'))  # netted debit note not counted twice
        # Header totals and GSTIN are checked too
        l3 = self.receive('C-3', 30, 30)
        odd = AP.create_invoice(self.ap, self.sup, self.header('INV/004', 270000, 13000, 283000, gstin='23AAACX9999C1Z5'), {l3: (D(30), D(9000), D(5))})
        self.assertEqual(odd.status, 'exception')
        self.assertTrue(any('GST on invoice' in i for i in odd.issues) and any('GSTIN on invoice' in i for i in odd.issues))
        AP.decide(self.fc, odd.pk, 'reject', 'Ask supplier for corrected invoice')
        self.assertEqual(AP.open_to_bill(l3), D('30'))  # rejected invoice frees the quantity

    def test_debit_note_returns_value_only_billed_quantity(self):
        l1 = self.receive('C-1', 40, 40)
        AP.create_invoice(self.ap, self.sup, self.header('A-1', 270000, 13500), {l1: (D(30), D(9000), D(5))})  # 10 MT not yet billed
        self.assertEqual(self.stock(), D('40'))
        with self.assertRaisesMessage(ValidationError, 'vehicle'):
            AP.create_debit_note(self.ap, l1.grn, 'return', 'Off-spec', {l1: 5})
        dn = AP.create_debit_note(self.ap, l1.grn, 'return', 'Lab LR-9 off-spec', {l1: 35}, 'MP04 GA 9000')
        self.assertEqual((dn.taxable, dn.total, self.stock()), (D('270000.00'), D('283500.00'), D('5')))  # 30 billed valued, 5 just not billable
        self.assertEqual(AP.open_to_bill(l1), 0)
        with self.assertRaisesMessage(ValidationError, 'not yet returned'):
            AP.create_debit_note(self.ap, l1.grn, 'return', 'again', {l1: 6}, 'MP04GA9000')
        claim = AP.create_debit_note(self.ap, l1.grn, 'quality', 'Moisture claim', {l1: 2})
        self.assertEqual(claim.total, D('18900.00'))
        self.assertEqual(AP.balance(self.sup)['balance'], D('283500') - D('283500') - D('18900'))

    def test_screens(self):
        l1 = self.receive('C-1', 20, 20)
        self.client.force_login(self.ap)
        page = self.client.get(reverse('supplier_invoices'))
        self.assertContains(page, 'Received, not yet billed')
        r = self.client.post(reverse('sinv_new', args=[self.sup.pk]), {'invoice_number': 'W-77', 'invoice_date': TODAY().isoformat(), 'supplier_gstin': '23AAACC1234C1Z5',
                                                                       'billed_taxable': '180000', 'billed_tax': '9000', 'billed_total': '189000',
                                                                       f'qty_{l1.pk}': '20', f'rate_{l1.pk}': '9000', f'gst_{l1.pk}': '5'})
        inv = SupplierInvoice.objects.get()
        self.assertRedirects(r, reverse('sinv_detail', args=[inv.pk]))
        self.assertContains(self.client.get(reverse('sinv_detail', args=[inv.pk])), 'no differences')
        r = self.client.post(reverse('dn_new', args=[l1.grn_id]), {'reason': 'return', 'remarks': 'Broken lumps', 'vehicle': 'MP04GA1', f'qty_{l1.pk}': '2'})
        dn = DebitNote.objects.get()
        self.assertRedirects(r, reverse('dn_detail', args=[dn.pk]))
        for url in [reverse('debit_notes'), reverse('dn_detail', args=[dn.pk]), reverse('grn_detail', args=[l1.grn_id])]:
            self.assertEqual(self.client.get(url).status_code, 200)
