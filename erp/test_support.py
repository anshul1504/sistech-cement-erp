"""Fictional, disposable fixtures shared by business regression/browser checks."""
from django.contrib.auth.models import Permission, User
from . import gl
from .models import Organization, Partner, Product, Site, SiteAccess


def seed_business_data(target):
    Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'gstin': '23ABCDE1234F1Z5'})
    target.site = Site.objects.create(code='QA-A', name='Verification plant A', kind='Plant', state='23', gstin='23ABCDE1234F1Z5')
    target.other = Site.objects.create(code='QA-B', name='Verification plant B', kind='Plant', state='23')
    target.raw = Product.objects.create(code='QA-RAW', name='Raw material', category='Raw material', unit='MT', gst_rate=5)
    target.bulk = Product.objects.create(code='QA-BULK', name='Finished bulk', category='Cement', unit='MT', gst_rate=18, hsn='2523')
    target.bag = Product.objects.create(code='QA-BAG', name='Finished bags', category='Cement', unit='BAG', bag_weight_kg=50, gst_rate=18, hsn='2523')
    target.supplier = Partner.objects.create(code='QA-S', name='Verification supplier', kind='Supplier', state='23', gstin='23AAACC1234C1Z5',
                                          bank_account_number='1234567890', bank_ifsc='SBIN0000001')
    target.customer = Partner.objects.create(code='QA-D', name='Verification dealer', kind='Dealer', state='23', credit_limit=10**8, payment_terms_days=30)
    target.maker = User.objects.create_user('qa-maker', 'maker@example.com', 'Verification-Password-816!')
    target.checker = User.objects.create_user('qa-checker', 'checker@example.com', 'Verification-Password-816!')
    for actor in [target.maker, target.checker]:
        actor.user_permissions.set(Permission.objects.filter(content_type__app_label='erp'))
        SiteAccess.objects.create(user=actor, site=target.site)
    target.outsider = User.objects.create_user('qa-other-site')
    target.outsider.user_permissions.set(Permission.objects.filter(content_type__app_label='erp'))
    SiteAccess.objects.create(user=target.outsider, site=target.other)
    gl.ensure_chart()
