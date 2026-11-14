from django.contrib.auth.models import User, Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import normalise_mobile
from .models import Influencer, InfluencerActivity, Partner, AuditEvent

BASE = {'kind': 'Mason', 'name': '', 'firm_name': '', 'mobile': '', 'alt_mobile': '', 'whatsapp': 'on', 'active': 'on', 'state': '23',
        'district': '', 'tehsil': '', 'village': '', 'pincode': '', 'address': '', 'primary_dealer': '', 'specialisation': '',
        'experience_years': '', 'team_size': '', 'monthly_cement_bags': '0', 'monthly_tmt_mt': '0', 'brand_preference': '',
        'aadhaar_last4': '', 'pan': '', 'upi_id': '', 'date_of_birth': '', 'notes': ''}


def person(name, mobile, district, kind='Mason', dealer=None, bags=0, **extra):
    return Influencer.objects.create(name=name, mobile=mobile, district=district, kind=kind, primary_dealer=dealer, monthly_cement_bags=bags, **extra)


@override_settings(EMAIL_OTP_ENABLED=False)
class InfluencerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('in-admin', 'in.admin@example.com', 'Original-Password-816!')
        cls.viewer = User.objects.create_user('in-view', 'in.view@example.com', 'Original-Password-816!')
        g = Group.objects.create(name='Influencer viewer'); g.permissions.add(Permission.objects.get(codename='view_influencer'))
        cls.viewer.groups.add(g)
        cls.bpl = Partner.objects.create(code='DLR-BPL', name='Bhopal Cement House', kind='Dealer', district='bhopal')
        cls.shr = Partner.objects.create(code='DLR-SHR', name='Sehore Traders', kind='Dealer', district='Sehore')
        cls.vidisha = Partner.objects.create(code='DLR-VDS', name='Vidisha Agencies', kind='Dealer', district='Vidisha')
        cls.supplier = Partner.objects.create(code='SUP-1', name='Gypsum Co', kind='Supplier')

    def setUp(self):
        self.client.force_login(self.admin)

    def post(self, url, **data):
        return self.client.post(url, {**BASE, **data})

    def test_mobile_normalisation(self):
        self.assertEqual(normalise_mobile('+91 98765-43210'), '9876543210')
        self.assertEqual(normalise_mobile('098765 43210'), '9876543210')
        self.assertEqual(normalise_mobile('12345'), '')
        self.assertEqual(normalise_mobile('5876543210'), '')  # Indian mobiles start 6-9

    def test_create_with_code_place_cleanup_and_dealer(self):
        r = self.post(reverse('influencers_new'), name='Ramesh Kushwaha', mobile='+91 98765 43210', district='  bhopal ', tehsil='HUZUR',
                      village='kolar road', primary_dealer=self.bpl.pk, dealers=[self.bpl.pk, self.shr.pk], monthly_cement_bags='450')
        p = Influencer.objects.get()
        self.assertRedirects(r, reverse('influencer_detail', args=[p.pk]))
        self.assertEqual((p.code, p.mobile, p.district, p.tehsil, p.village), ('INF-00001', '9876543210', 'Bhopal', 'Huzur', 'Kolar Road'))
        self.assertEqual(p.tier, 'Gold')
        self.assertEqual(list(p.dealers.all()), [self.shr])  # main dealer is not repeated
        self.assertEqual([d.pk for d in p.all_dealers()], [self.bpl.pk, self.shr.pk])
        self.assertTrue(AuditEvent.objects.filter(action='Influencer created', reference=f'influencer:{p.pk}').exists())
        page = self.client.get(reverse('influencer_detail', args=[p.pk]))
        self.assertContains(page, '+91 98765 43210')
        self.assertContains(page, 'Bhopal Cement House')

    def test_duplicate_mobile_and_validation(self):
        existing = person('Suresh', '9123456780', 'Sehore')
        r = self.post(reverse('influencers_new'), name='Suresh again', mobile='09123456780', district='Sehore')
        self.assertContains(r, 'already registered to Suresh')
        self.assertContains(r, reverse('influencer_detail', args=[existing.pk]))
        r = self.post(reverse('influencers_new'), name='X', mobile='123', district='', pincode='12', aadhaar_last4='12345', upi_id='bad', kyc_verified='on')
        for msg in ['valid 10-digit Indian mobile', 'This field is required', '6-digit PIN', 'last 4 digits', 'valid UPI ID']:
            self.assertContains(r, msg)
        r = self.post(reverse('influencers_new'), name='Y', mobile='9000000001', district='Bhopal', kyc_verified='on')
        self.assertContains(r, 'before marking KYC verified')
        self.assertEqual(Influencer.objects.count(), 1)

    def test_supplier_cannot_be_linked_as_dealer(self):
        r = self.post(reverse('influencers_new'), name='Z', mobile='9000000002', district='Bhopal', primary_dealer=self.supplier.pk)
        self.assertContains(r, 'Select a valid choice')

    def test_list_filters(self):
        person('Mason A', '9000000011', 'Bhopal', dealer=self.bpl, bags=120)
        person('Contractor B', '9000000012', 'Bhopal', kind='Contractor', bags=1200)
        c = person('Mason C', '9000000013', 'Sehore')
        c.dealers.add(self.bpl)
        person('Gone D', '9000000014', 'Sehore', active=False)
        Influencer.objects.filter(name='Mason A').update(brand_preference='sistech')
        names = lambda **q: [p.name for p in self.client.get(reverse('influencers'), q).context['page'].object_list]
        self.assertEqual(names(), ['Contractor B', 'Mason A', 'Mason C'])
        self.assertEqual(names(group='Mason'), ['Mason A', 'Mason C'])
        self.assertEqual(names(district='bhopal'), ['Contractor B', 'Mason A'])
        self.assertEqual(names(dealer=self.bpl.pk), ['Mason A', 'Mason C'])
        self.assertEqual(names(dealer='none'), ['Contractor B'])
        self.assertEqual(names(tier='Platinum'), ['Contractor B'])
        self.assertEqual(names(tier='Silver'), ['Mason A'])
        self.assertEqual(names(q='0013'), ['Mason C'])
        self.assertEqual(names(brand='sistech'), ['Mason A'])
        self.assertEqual(names(brand=''), ['Contractor B', 'Mason A', 'Mason C'])  # empty filter keeps everyone
        self.assertEqual(names(status='inactive'), ['Gone D'])
        stats = self.client.get(reverse('influencers')).context['stats']
        self.assertEqual((stats['total'], stats['masons'], stats['unlinked'], stats['bags']), (3, 2, 1, 1320))

    def test_district_view_counts_and_gaps(self):
        person('A', '9000000021', 'Bhopal', dealer=self.bpl, bags=100, brand_preference='sistech')
        person('B', '9000000022', 'Bhopal', kind='Contractor', bags=300)
        person('C', '9000000023', 'Raisen', bags=50)
        r = self.client.get(reverse('influencer_districts'))
        rows = {row['district']: row for row in r.context['rows']}
        self.assertEqual((rows['Bhopal']['masons'], rows['Bhopal']['contractors'], rows['Bhopal']['bags'], rows['Bhopal']['dealer_count']), (1, 1, 400, 1))
        self.assertEqual(rows['Bhopal']['share'], 50)
        self.assertEqual(rows['Bhopal']['gap'], 'unlinked')
        self.assertEqual(rows['Raisen']['gap'], 'no_dealer')
        self.assertEqual(rows['Sehore']['gap'], 'no_people')  # dealer there, nobody registered
        self.assertEqual(r.context['gaps']['no_people'], 2)  # Sehore and Vidisha
        self.assertContains(r, 'No masons yet')

    def test_dealer_view_and_partner_detail(self):
        a = person('A', '9000000031', 'Bhopal', dealer=self.bpl, bags=200)
        b = person('B', '9000000032', 'Sehore', kind='Contractor', dealer=self.shr, bags=500)
        b.dealers.add(self.bpl)
        r = self.client.get(reverse('influencer_dealers'))
        rows = {row['dealer'].code: row for row in r.context['rows']}
        self.assertEqual((rows['DLR-BPL']['total'], rows['DLR-BPL']['masons'], rows['DLR-BPL']['contractors'], rows['DLR-BPL']['bags'], rows['DLR-BPL']['primary']), (2, 1, 1, 700, 1))
        self.assertEqual(rows['DLR-VDS']['total'], 0)
        self.assertEqual(r.context['empty_dealers'], 1)
        self.assertNotIn('SUP-1', rows)
        page = self.client.get(reverse('partner_detail', args=[self.bpl.pk]))
        self.assertEqual(page.context['network']['total'], 2)
        self.assertEqual(page.context['network']['bags'], 700)
        self.assertContains(page, reverse('influencer_detail', args=[a.pk]))
        self.assertIsNone(self.client.get(reverse('partner_detail', args=[self.supplier.pk])).context['network'])

    def test_activity_lead_lifecycle(self):
        p = person('A', '9000000041', 'Bhopal', dealer=self.bpl)
        url = reverse('influencer_activity_new', args=[p.pk])
        r = self.client.post(url, {'kind': 'referral', 'date': '2026-11-10', 'title': '', 'expected_bags': '800', 'expected_tmt_mt': '0'})
        self.assertFalse(InfluencerActivity.objects.exists())  # referral needs a site name
        self.client.post(url, {'kind': 'referral', 'date': '2026-11-10', 'title': 'G+2 house, Kolar Road', 'dealer': self.bpl.pk, 'expected_bags': '800', 'expected_tmt_mt': '2.5'})
        self.client.post(url, {'kind': 'call', 'date': '2026-11-11', 'status': 'won', 'expected_bags': '0', 'expected_tmt_mt': '0'})
        lead, call = InfluencerActivity.objects.order_by('id')
        self.assertEqual((lead.status, call.status), ('open', ''))
        self.assertEqual(self.client.get(reverse('influencer_detail', args=[p.pk])).context['leads']['bags'], 800)
        self.client.post(reverse('influencer_activity_status', args=[p.pk, lead.pk]), {'status': 'won'})
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'won')
        self.assertEqual(self.client.post(reverse('influencer_activity_status', args=[p.pk, call.pk]), {'status': 'won'}).status_code, 404)
        # Delete is blocked while history exists; deactivate instead
        self.client.post(reverse('influencer_delete', args=[p.pk]))
        self.assertTrue(Influencer.objects.filter(pk=p.pk).exists())
        self.client.post(reverse('influencer_status', args=[p.pk]))
        p.refresh_from_db()
        self.assertFalse(p.active)

    def test_viewer_permissions(self):
        p = person('A', '9000000051', 'Bhopal')
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse('influencers')).status_code, 200)
        self.assertEqual(self.client.get(reverse('influencer_districts')).status_code, 200)
        self.assertEqual(self.client.get(reverse('influencer_dealers')).status_code, 200)
        page = self.client.get(reverse('influencer_detail', args=[p.pk]))
        self.assertIsNone(page.context['form'])
        self.assertNotContains(page, reverse('influencers_edit', args=[p.pk]))
        for url in [reverse('influencers_new'), reverse('influencer_import'), reverse('influencers_edit', args=[p.pk]), reverse('influencers') + '?export=csv']:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.post(reverse('influencer_activity_new', args=[p.pk]), {'kind': 'call'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('influencer_delete', args=[p.pk])).status_code, 403)
        nav = [i['label'] for g in self.client.get(reverse('influencers')).context['nav_groups'] for i in g['items'] if not i['soon']]
        self.assertIn('Masons & contractors', nav)

    def test_export_csv(self):
        person('राम मिस्त्री', '9000000061', 'Bhopal', dealer=self.bpl, bags=50)
        r = self.client.get(reverse('influencers') + '?export=csv')
        body = r.content.decode('utf-8')
        self.assertTrue(body.startswith('﻿Code,Type,Name'))
        self.assertIn('राम मिस्त्री', body)
        self.assertIn('DLR-BPL', body)
        self.assertTrue(AuditEvent.objects.filter(action='Masons & contractors exported').exists())

    def test_csv_import(self):
        person('Existing', '9000000071', 'Bhopal')
        csv_text = ('Type,Name,Mobile,District,Tehsil,State,Dealer_code,Monthly_cement_bags,Brand\n'
                    'Mistri,Ramu,+91 90000 00072,bhopal,huzur,MP,dlr-bpl,300,Sistech\n'
                    'Thekedar,Shyam,9000000073,Sehore,,Madhya Pradesh,,1500,Other brand only\n'
                    'Mason,Dup in file,9000000073,Sehore,,,,0,\n'
                    'Mason,Already there,9000000071,Bhopal,,,,0,\n'
                    'Plumber,Bad type,9000000074,Bhopal,,,,0,\n'
                    'Mason,No district,9000000075,,,,,0,\n'
                    'Mason,Bad dealer,9000000076,Bhopal,,,NOPE,0,\n'
                    ',,,,,,,,\n')
        f = SimpleUploadedFile('sheet.csv', csv_text.encode('utf-8-sig'), content_type='text/csv')
        r = self.client.post(reverse('influencer_import'), {'file': f})
        result = r.context['result']
        self.assertEqual(result['created'], 2)
        self.assertEqual(len(result['duplicates']), 2)
        self.assertEqual(len(result['errors']), 3)
        ramu = Influencer.objects.get(mobile='9000000072')
        self.assertEqual((ramu.kind, ramu.district, ramu.primary_dealer, ramu.brand_preference, ramu.created_by), ('Mason', 'Bhopal', self.bpl, 'sistech', self.admin))
        self.assertEqual(Influencer.objects.get(mobile='9000000073').kind, 'Contractor')
        self.assertContains(r, 'unknown type')
        self.assertContains(r, 'dealer code NOPE not found')

    def test_import_rejects_bad_files_and_serves_template(self):
        r = self.client.post(reverse('influencer_import'), {'file': SimpleUploadedFile('x.csv', b'name,phone\nA,1\n')})
        self.assertContains(r, 'Missing column(s)')
        r = self.client.post(reverse('influencer_import'), {'file': SimpleUploadedFile('x.xlsx', b'PK')})
        self.assertContains(r, 'Upload a .csv file')
        r = self.client.post(reverse('influencer_import'), {'file': SimpleUploadedFile('x.csv', 'type,name\n'.encode('utf-16'))})
        self.assertContains(r, 'not UTF-8')
        t = self.client.get(reverse('influencer_import') + '?template=1')
        self.assertIn('type,name,mobile,district', t.content.decode('utf-8-sig'))
