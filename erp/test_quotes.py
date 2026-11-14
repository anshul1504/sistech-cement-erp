"""Quotations: list price, discount approval, sending, expiry, revisions and conversion."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import quotes as QT
from .models import PriceRule, Quotation, CRMLead, Order
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class QuotationTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.boss = User.objects.create_superuser('qt-boss', 'b@example.com', 'Verification-Password-816!')
        PriceRule.objects.create(product=self.bag, rate=D('380'), valid_from=self.today - timedelta(days=30))

    def data(self, **kw):
        return {'site': self.site, 'partner': self.customer, 'lead': None, 'date': self.today, 'valid_until': self.today + timedelta(days=10), 'place_of_supply': '23',
                'freight_terms': 'for_destination', 'delivery_terms': '', 'payment_terms': '30 days', 'notes': '', **kw}

    def test_discount_approval_send_accept_convert(self):
        q = QT.save(self.maker, self.data(), [(self.bag, 500, None)])
        line = q.lines.get()
        self.assertEqual((line.rate, line.list_rate, QT.totals(q)['grand_total']), (D('380.00'), D('380.00'), D('224200')))  # 1,90,000 + 18 %
        QT.submit(self.maker, q.pk)
        self.assertEqual(Quotation.objects.get(pk=q.pk).status, 'ready')  # at list price: no approval needed
        q2 = QT.save(self.maker, self.data(), [(self.bag, 1000, D('360'))])
        self.assertEqual(QT.submit(self.maker, q2.pk).status, 'approval')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            QT.approve(self.maker, q2.pk, True)
        with self.assertRaisesMessage(ValidationError, 'Only approved'):
            QT.mark_sent(self.maker, q2.pk)
        QT.approve(self.checker, q2.pk, True, 'Volume deal')
        QT.mark_sent(self.maker, q2.pk)
        with self.assertRaisesMessage(ValidationError, 'Only drafts'):
            QT.save(self.maker, self.data(), [(self.bag, 1, None)], Quotation.objects.get(pk=q2.pk))
        QT.outcome(self.maker, q2.pk, True, 'PO by phone')
        order = QT.convert(self.maker, q2.pk)
        self.assertEqual((order.kind, order.status, order.lines.get().rate, order.lines.get().list_rate), ('Sale', 'Draft', D('360.00'), D('380.00')))
        with self.assertRaisesMessage(ValidationError, 'Only accepted'):
            QT.convert(self.maker, q2.pk)

    def test_expiry_revision_and_prospect(self):
        q = QT.save(self.maker, self.data(valid_until=self.today), [(self.bag, 100, None)])
        QT.submit(self.maker, q.pk); QT.mark_sent(self.maker, q.pk)
        Quotation.objects.filter(pk=q.pk).update(valid_until=self.today - timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, 'expired'):
            QT.outcome(self.maker, q.pk, True)
        new = QT.revise(self.maker, q.pk)
        self.assertEqual((new.number, new.revision, Quotation.objects.get(pk=q.pk).status, new.lines.count()), (q.number, 2, 'revised', 1))
        with self.assertRaisesMessage(ValidationError, 'cannot be revised'):
            QT.revise(self.maker, q.pk)
        lead = CRMLead.objects.create(number='L-1', site=self.site, owner=self.maker, business_name='New Builder', mobile='9876512345', created_by=self.maker)
        with self.assertRaisesMessage(ValidationError, 'either a customer'):
            QT.save(self.maker, self.data(lead=lead), [(self.bag, 10, None)])
        pq = QT.save(self.maker, self.data(partner=None, lead=lead), [(self.bag, 10, None)])
        QT.submit(self.maker, pq.pk); QT.mark_sent(self.maker, pq.pk); QT.outcome(self.maker, pq.pk, True)
        with self.assertRaisesMessage(ValidationError, 'onboarding'):
            QT.convert(self.maker, pq.pk)
        self.client.force_login(self.boss)
        for url in [reverse('quotations'), reverse('quotation_new'), reverse('quotation_detail', args=[pq.pk]), reverse('quotation_detail', args=[new.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('quotation_new'), {'site': self.site.pk, 'partner': self.customer.pk, 'date': self.today.isoformat(),
                                                        'valid_until': (self.today + timedelta(days=5)).isoformat(), 'place_of_supply': '23', 'freight_terms': 'ex_works',
                                                        'product_0': self.bag.pk, 'qty_0': '50', 'rate_0': ''})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Order.objects.count(), 0)
