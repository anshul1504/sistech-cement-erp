"""Load SISTECH's public business profile (sistech.co.in): head office, manufacturing partners, partner plants and the product range.

Only empty organisation fields are filled and only missing partners / plants / products are created, so running it again is safe and
nothing typed by users is overwritten. Grades, bag sizes, GST rates and capacities are as published and must be confirmed by Sistech.
"""
from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.db import transaction

from erp.models import Organization, Partner, Site, Product

PROFILE = {'legal_name': 'SISTECH Cement & TMT Pvt. Ltd.', 'trade_name': 'SISTECH', 'tagline': 'Solid Foundation. Strong Future.',
           'address': 'Apollo Premier, Vijay Nagar', 'city': 'Indore', 'district': 'Indore', 'state': '23', 'pincode': '452010',
           'phone': '01169290404', 'whatsapp': '8319416402', 'email': 'sistechcement@gmail.com', 'website': 'https://sistech.co.in'}

# partner code, name, city, district, state, plant code, plant name, facility, capacity MT/yr
PARTNERS = [
    ('MFG-ABHIRAJ', 'Abhiraj Cement', 'Simga', 'Raipur', '22', 'PLT-ABH-SMG', 'Abhiraj Cement, Simga', 'integrated_cement', 2500000),
    ('MFG-CENTRAL', 'Central Cement Industries', 'Simga', 'Raipur', '22', 'PLT-CCI-SMG', 'Central Cement, Simga', 'integrated_cement', 2000000),
    ('MFG-RAJAT', 'Rajat Cement', 'Sardarpur', 'Dhar', '23', 'PLT-RJT-DHR', 'Rajat Cement, Sardarpur', 'integrated_cement', 720000),
    ('MFG-HIMALAYA', 'Himalaya Height Cement', 'Bhabhua', 'Kaimur', '10', 'PLT-HHC-KMR', 'Himalaya Height Cement, Kaimur', 'integrated_cement', 5400000),
    ('MFG-PIONEER', 'Pioneer Industries', 'Jeerabad', 'Dhar', '23', 'PLT-PIO-DHR', 'Pioneer Industries, Ghursal–Jeerabad', 'integrated_cement', 3240000),
    ('MFG-RAIGARH', 'Raigarh Ispat', 'Raigarh', 'Raigarh', '22', 'PLT-RGI-RGH', 'SISTECH TMT, Raigarh Ispat', 'tmt_rolling', 300000),
]
CEMENT = [('SIS-OPC53-50', 'SISTECH OPC 53 Grade · 50 kg', 'OPC 53'), ('SIS-PPC-50', 'SISTECH PPC · 50 kg', 'PPC'), ('SIS-PSC-50', 'SISTECH PSC · 50 kg', 'PSC')]
TMT_SIZES = [8, 10, 12, 16, 20, 25, 32]


class Command(BaseCommand):
    help = "Load SISTECH's public profile, manufacturing partners, partner plants and product range (fills blanks only)."

    @transaction.atomic
    def handle(self, *args, **options):
        org = Organization.current()
        untouched = lambda k: not getattr(org, k) or getattr(org, k) == Organization._meta.get_field(k).default  # still the installation default
        filled = [k for k in PROFILE if untouched(k)]
        for k in filled:
            setattr(org, k, PROFILE[k])
        org.save()
        partners = sites = products = 0
        for code, name, city, district, state, scode, sname, facility, cap in PARTNERS:
            p, made = Partner.objects.get_or_create(code=code, defaults={'name': name, 'kind': 'Supplier', 'manufacturing_partner': True, 'city': city,
                                                                         'district': district, 'state': state, 'notes': 'Manufacturing partner (sistech.co.in).'})
            if not made and not p.manufacturing_partner and p.kind == 'Supplier':
                p.manufacturing_partner = True
                p.save(update_fields=['manufacturing_partner'])
            partners += made
            _, made = Site.objects.get_or_create(code=scode, defaults={'name': sname, 'kind': 'Plant', 'facility_type': facility, 'operated_by': p, 'city': city,
                                                                      'district': district, 'state': state, 'capacity_tpa': D(cap),
                                                                      'notes': 'Partner-operated plant. Confirm GST registration and dispatch arrangement.'})
            sites += made
        for code, name, grade in CEMENT:
            _, made = Product.objects.get_or_create(code=code, defaults={'name': name, 'category': 'Cement', 'unit': 'BAG', 'bag_weight_kg': D(50), 'grade': grade,
                                                                        'brand': 'SISTECH', 'hsn': '2523', 'gst_rate': D(18),
                                                                        'description': 'Confirm grade, bag size and GST rate with Sistech.'})
            products += made
        for d in TMT_SIZES:
            _, made = Product.objects.get_or_create(code=f'SIS-TMT550D-{d}', defaults={'name': f'SISTECH TMT Fe 550D · {d} mm', 'category': 'TMT', 'unit': 'MT',
                                                                                     'grade': 'Fe 550D', 'bar_diameter_mm': d, 'bar_length_m': D(12), 'brand': 'SISTECH',
                                                                                     'hsn': '7214', 'gst_rate': D(18), 'description': 'Confirm sizes and GST rate with Sistech.'})
            products += made
        self.stdout.write(self.style.SUCCESS(f'Organisation fields filled: {", ".join(filled) or "none"}. Created {partners} partner(s), {sites} plant(s), {products} product(s).'))
