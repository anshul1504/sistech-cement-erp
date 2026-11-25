"""Bins: put-away limited to unbinned stock, bin-to-bin moves, automatic trimming on issue, screen."""
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import bins as B
from .models import StorageBin, BinStock, BinMove
from .services import move_stock
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class BinTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        move_stock(self.maker, self.site, self.bag, D(100), 'opening:bin', 'Opening', unit_cost=D(300))
        self.b1 = StorageBin.objects.create(site=self.site, code='G1')
        self.b2 = StorageBin.objects.create(site=self.site, code='G2')

    def test_put_move_and_issue_trim(self):
        B.put_away(self.maker, self.bag, self.b1, 60)
        with self.assertRaisesMessage(ValidationError, 'Only 40'):
            B.put_away(self.maker, self.bag, self.b2, 50)
        B.put_away(self.maker, self.bag, self.b2, 30)
        B.move(self.maker, self.bag, self.b1, self.b2, 10)
        with self.assertRaisesMessage(ValidationError, 'holds only'):
            B.move(self.maker, self.bag, self.b1, self.b2, 999)
        self.assertEqual((B.binned(self.site, self.bag), B.unbinned(self.site, self.bag)), (D(90), D(10)))
        move_stock(self.maker, self.site, self.bag, D(-30), 'qa:issue', 'Dispatch')   # 10 loose + 20 from bins
        self.assertEqual(B.binned(self.site, self.bag), D(70))
        self.assertEqual(BinMove.objects.filter(kind='issue').get().quantity, D(20))
        self.assertEqual((BinStock.objects.get(bin=self.b1).quantity, BinStock.objects.get(bin=self.b2).quantity), (D(30), D(40)))   # largest bin (G1, 50) trimmed first
        self.client.force_login(self.checker)
        self.assertContains(self.client.get(reverse('bins') + f'?site={self.site.pk}'), 'Not in a bin')
        self.client.post(reverse('bin_action', args=['add']), {'site': self.site.pk, 'code': 'g3'})
        self.assertTrue(StorageBin.objects.filter(code='G3').exists())
