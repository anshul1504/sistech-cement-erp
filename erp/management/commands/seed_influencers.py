"""Add fictional masons & contractors to a local/demo database (safe to run again; skips existing mobiles)."""
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from erp.models import Influencer, InfluencerActivity, Partner, CUSTOMER_KINDS

DISTRICTS = {'Bhopal': ['Huzur', 'Berasia'], 'Sehore': ['Ashta', 'Ichhawar'], 'Raisen': ['Obaidullaganj', 'Begumganj'],
             'Vidisha': ['Basoda', 'Sironj'], 'Indore': ['Sanwer', 'Depalpur'], 'Hoshangabad': ['Itarsi', 'Pipariya']}
FIRST = ['Ramesh', 'Suresh', 'Mahesh', 'Rajesh', 'Dinesh', 'Mukesh', 'Ramkishan', 'Bablu', 'Sunil', 'Anil', 'Gopal', 'Kailash', 'Shankar', 'Vinod', 'Pappu']
LAST = ['Kushwaha', 'Patel', 'Yadav', 'Prajapati', 'Lodhi', 'Verma', 'Ahirwar', 'Meena', 'Sahu', 'Rajput']


class Command(BaseCommand):
    help = 'Adds fictional masons, contractors and activities for local demos.'

    def add_arguments(self, parser):
        parser.add_argument('--count', type=int, default=40)

    def handle(self, *args, count, **options):
        rng = random.Random(1504)
        for code, district in [('DLR-001', 'Bhopal'), ('DLR-002', 'Indore')]:
            Partner.objects.filter(code=code, district='').update(district=district)
        dealers = list(Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True))
        by_district = {}
        for d in dealers:
            by_district.setdefault((d.district or '').strip().title(), []).append(d)
        user = User.objects.filter(is_superuser=True).first()
        created = 0
        for n in range(count):
            mobile = f'9{rng.randint(100000000, 999999999)}'
            if Influencer.objects.filter(mobile=mobile).exists():
                continue
            district = rng.choice(list(DISTRICTS))
            kind = rng.choices(['Mason', 'Contractor', 'Engineer', 'Builder'], [6, 3, 1, 1])[0]
            local = by_district.get(district) or []
            dealer = rng.choice(local) if local and rng.random() < .85 else (rng.choice(dealers) if dealers and rng.random() < .3 else None)
            bags = rng.choice([40, 80, 150, 250, 400, 600, 900, 1500]) * (3 if kind in ('Contractor', 'Builder') else 1)
            p = Influencer.objects.create(
                kind=kind, name=f'{rng.choice(FIRST)} {rng.choice(LAST)}', mobile=mobile, district=district, tehsil=rng.choice(DISTRICTS[district]),
                firm_name=f'{rng.choice(LAST)} Constructions' if kind in ('Contractor', 'Builder') else '', primary_dealer=dealer,
                specialisation=rng.choice(['residential', 'rcc', 'masonry', 'commercial']), experience_years=rng.randint(2, 25),
                monthly_cement_bags=bags, monthly_tmt_mt=round(bags / 200, 1), brand_preference=rng.choice(['sistech', 'mixed', 'competitor', '']),
                kyc_verified=rng.random() < .4, aadhaar_last4=f'{rng.randint(1000, 9999)}', created_by=user,
                notes='Fictional demo record.')
            for _ in range(rng.randint(0, 3)):
                kind_a = rng.choice(['meet', 'visit', 'call', 'referral', 'project'])
                InfluencerActivity.objects.create(
                    influencer=p, kind=kind_a, date=timezone.localdate() - timezone.timedelta(days=rng.randint(0, 90)), dealer=dealer,
                    title=f'G+{rng.randint(0, 3)} house, {p.tehsil}' if kind_a in ('referral', 'project') else '',
                    expected_bags=rng.choice([200, 500, 900]) if kind_a in ('referral', 'project') else 0,
                    status=rng.choice(['open', 'open', 'won', 'lost']) if kind_a in ('referral', 'project') else '', created_by=user)
            created += 1
        self.stdout.write(self.style.SUCCESS(f'{created} fictional masons/contractors added.'))
