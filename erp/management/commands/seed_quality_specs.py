"""Create standard test specifications for every cement / TMT product from its grade (IS 269, IS 1489-1, IS 455, IS 16415, IS 1786).

Limits are the published minimum / maximum values; Sistech's QC manager should confirm them (and tighten in-house limits) in the
quality screens. Existing parameters are never overwritten. Run: python manage.py seed_quality_specs
"""
from decimal import Decimal as D

from django.core.management.base import BaseCommand

from erp.models import Product, QualitySpec

# (parameter, unit, method, min, max, age_days, required_for_release)
COMMON_CEMENT = [
    ('Initial setting time', 'min', 'IS 4031 (Part 5)', D(30), None, 0, True),
    ('Final setting time', 'min', 'IS 4031 (Part 5)', None, D(600), 0, True),
    ('Soundness – Le Chatelier', 'mm', 'IS 4031 (Part 3)', None, D(10), 0, True),
    ('Soundness – Autoclave', '%', 'IS 4031 (Part 3)', None, D('0.8'), 0, True),
    ('MgO', '%', 'IS 4032', None, D(6), 0, True),
    ('SO₃', '%', 'IS 4032', None, D('3.5'), 0, True),
    ('Loss on ignition', '%', 'IS 4032', None, D(5), 0, True),
    ('Chloride', '%', 'IS 4032', None, D('0.1'), 0, True),
]
STRENGTH = {'OPC 33': (16, 22, 33), 'OPC 43': (23, 33, 43), 'OPC 53': (27, 37, 53), 'PPC': (16, 22, 33), 'PSC': (16, 22, 33), 'PCC': (16, 22, 33)}
FINENESS = {'OPC 33': 225, 'OPC 43': 225, 'OPC 53': 225, 'PPC': 300, 'PSC': 370, 'PCC': 370}
# grade: (UTS ≥ ratio × YS, UTS min, elongation %, C, S, P, S+P)
TMT = {'Fe 415': ('1.10', 485, '14.5', '0.30', '0.060', '0.060', '0.110'), 'Fe 415D': ('1.12', 500, 18, '0.25', '0.045', '0.045', '0.085'),
       'Fe 500': ('1.08', 545, 12, '0.30', '0.055', '0.055', '0.105'), 'Fe 500D': ('1.10', 565, 16, '0.25', '0.040', '0.040', '0.075'),
       'Fe 550': ('1.06', 585, 10, '0.30', '0.055', '0.050', '0.100'), 'Fe 550D': ('1.08', 600, '14.5', '0.25', '0.040', '0.040', '0.075'),
       'Fe 600': ('1.06', 660, 10, '0.30', '0.040', '0.040', '0.075')}


def specs_for(product):
    g = product.grade
    if g in STRENGTH:
        s3, s7, s28 = STRENGTH[g]
        return [('Fineness (Blaine)', 'm²/kg', 'IS 4031 (Part 2)', D(FINENESS[g]), None, 0, True), *COMMON_CEMENT,
                ('Compressive strength 3 days', 'MPa', 'IS 4031 (Part 6)', D(s3), None, 3, True),
                ('Compressive strength 7 days', 'MPa', 'IS 4031 (Part 6)', D(s7), None, 7, False),
                ('Compressive strength 28 days', 'MPa', 'IS 4031 (Part 6)', D(s28), None, 28, False)]
    if g in TMT:
        ratio, uts, el, c, s, p, sp = TMT[g]
        ys = D(g.split()[1].rstrip('D'))
        return [('0.2% proof stress / yield', 'MPa', 'IS 1608', ys, None, 0, True), ('Tensile strength (UTS)', 'MPa', 'IS 1608', D(uts), None, 0, True),
                ('UTS / YS ratio', '', 'IS 1786', D(ratio), None, 0, True), ('Elongation', '%', 'IS 1608', D(str(el)), None, 0, True),
                ('Carbon', '%', 'IS 228', None, D(c), 0, True), ('Sulphur', '%', 'IS 228', None, D(s), 0, True), ('Phosphorus', '%', 'IS 228', None, D(p), 0, True),
                ('Sulphur + phosphorus', '%', 'IS 228', None, D(sp), 0, True), ('Bend & rebend (1 = pass)', '', 'IS 1599 / IS 1786', D(1), D(1), 0, True)]
    return []


class Command(BaseCommand):
    help = 'Create IS-standard test specifications for cement and TMT products (does not change existing ones).'

    def handle(self, *args, **options):
        made = 0
        for product in Product.objects.filter(category__in=['Cement', 'TMT']):
            for seq, (param, unit, method, lo, hi, age, req) in enumerate(specs_for(product), 1):
                _, created = QualitySpec.objects.get_or_create(product=product, parameter=param, defaults={
                    'unit': unit, 'method': method, 'min_value': lo, 'max_value': hi, 'age_days': age, 'required_for_release': req, 'sequence': seq * 10})
                made += created
        self.stdout.write(self.style.SUCCESS(f'{made} specification line(s) created. Ask the QC manager to confirm the limits.'))
