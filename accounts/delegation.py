"""Explicit, non-transitive approval authority; never grants general module/finance/site access."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from erp.models import AuditEvent, SiteAccess
from .models import ApprovalDelegation, ApprovalDelegationUse
from .security import access_allowed

# permission: label, site-specific, separately requires existing company-finance scope
POLICIES = {
    'approve_recipe': ('Recipe / BOM version approval', False, False),
    'approve_pod_correction': ('Delivery evidence correction approval', True, False),
    'approve_weight_exception': ('Dispatch weight exception approval', True, False),
    'publish_recognitionprogram': ('Recognition winners publication', False, False),
    'verify_influencerusage': ('Recognition usage verification', False, False),
    'decide_complaint': ('Complaint decision / closure', True, False),
    'verify_capaaction': ('CAPA effectiveness verification', True, False),
    'approve_freightbill': ('Consolidated freight bill review', False, True),
    'review_dealerkyc': ('Dealer KYC review', True, False),
    'review_dealercredit': ('Dealer credit review', True, False),
    'approve_dealerapplication': ('Dealer activation review', True, False),
    'approve_sales_order': ('Sales order approval', True, False),
    'approve_purchase_order': ('Purchase order approval', True, False),
    'approve_high_value_purchase': ('High-value purchase approval', True, False),
    'approve_price_exception': ('Sales price exception', True, False),
    'override_credit_sales_order': ('Sales credit override', True, True),
    'approve_quotation': ('Quotation discount approval', True, False),
    'decide_damagereport': ('Damaged stock disposition', True, False),
    'approve_stockcount': ('Stock count adjustment approval', True, False),
    'approve_supplierpayment': ('Supplier payment approval', False, True),
    'release_supplierpayment': ('Supplier payment release', False, True),
    'approve_schemeentitlementbatch': ('Scheme entitlement approval', True, True),
    'approve_schemesettlement': ('Scheme payout approval', False, True),
    'approve_journalentry': ('Manual journal approval', False, True),
    'close_period': ('Month close / reopening', False, True),
    'approve_yearclose': ('Year close / reopening', False, True),
    'approve_purchaserequest': ('Purchase request review', True, False),
    'award_rfq': ('RFQ award', True, False),
    'release_goodsreceipt': ('Inbound quality release', True, False),
    'release_production': ('Production quality release', True, False),
    'approve_returnrequest': ('Customer return approval', True, False),
    'approve_beatplan': ('Beat plan approval', True, False),
    'approve_supplycontract': ('Manufacturing supply-rate approval', True, False),
    'approve_schemeversion': ('Incentive rule approval', True, False),
    'approve_supplierinvoice': ('Supplier invoice exception approval', True, False),
    'approve_stocktransfer': ('Stock transfer approval / discrepancy', True, False),
}


def _user(pk):
    return get_user_model().objects.get(pk=pk)


def _site_allowed(user, site):
    return site is not None and site.active and (user.is_superuser or SiteAccess.objects.filter(user=user, site=site).exists())


def _qualified(user, permission, site):
    if permission not in POLICIES or not access_allowed(user):
        return False
    _, scoped, finance = POLICIES[permission]
    if permission == 'approve_recipe' and not user.has_perm('erp.view_recipe'):
        return False
    if permission in {'approve_weight_exception','approve_pod_correction'} and not user.has_perm('erp.view_dispatch'):
        return False
    if permission == 'publish_recognitionprogram' and not user.has_perm('erp.view_recognitionprogram'):
        return False
    if permission == 'verify_influencerusage' and not user.has_perm('erp.view_influencerusage'):
        return False
    if permission in {'decide_complaint','verify_capaaction'} and not user.has_perm('erp.view_complaint'):
        return False
    if permission == 'verify_capaaction' and not user.has_perm('erp.view_capaaction'):
        return False
    if permission == 'approve_freightbill' and not user.has_perm('erp.view_freightbill'):
        return False
    if permission in {'review_dealerkyc','review_dealercredit','approve_dealerapplication'} and not (user.has_perm('erp.view_team_crm') and user.has_perm('erp.view_dealerapplication')):
        return False
    if permission == 'approve_beatplan' and not user.has_perm('erp.view_team_beats'):
        return False
    return ((not scoped or _site_allowed(user, site))
            and (not finance or user.has_perm('erp.access_company_finance')))


def _grant(user, permission, site, makers=(), lock=False):
    _, scoped, _ = POLICIES[permission]
    now = timezone.now()
    qs = ApprovalDelegation.objects.filter(deputy=user, permission=permission,
        site=site if scoped else None, revoked_at__isnull=True, starts_at__lte=now, ends_at__gt=now)
    if lock:
        qs = qs.select_for_update(of=('self',))
    excluded = set(x for x in makers if x is not None)
    for grant in qs.select_related('principal').order_by('pk'):
        principal = grant.principal
        if (_qualified(principal, permission, site) and principal.has_perm('erp.' + permission)):
            if principal.pk in excluded or user.pk in excluded:
                if lock:
                    raise ValidationError('Delegation cannot approve a transaction made by its principal or deputy (maker-checker).')
                return None
            return grant
    return None


def can_approve(user, permission, site=None, makers=()):
    """Read-only capability for scoped buttons/queues; effective dates checked on every call."""
    if permission not in POLICIES or not user.is_authenticated:
        return user.has_perm('erp.' + permission)
    actor = _user(user.pk)
    # Preserve existing direct authority rules, including legacy non-finance sales approvers.
    if access_allowed(actor) and actor.has_perm('erp.' + permission):
        return True
    if not _qualified(actor, permission, site):
        return False
    return _grant(actor, permission, site, makers) is not None


@transaction.atomic
def authorize(user, permission, site=None, makers=(), reference=''):
    """Call inside the business transaction; failed transitions roll back the use/audit record too."""
    if permission not in POLICIES:
        raise PermissionDenied
    actor = _user(user.pk)
    if not access_allowed(actor):
        raise PermissionDenied
    if actor.has_perm('erp.' + permission):
        return None
    if not _qualified(actor, permission, site):
        raise PermissionDenied
    grant = _grant(actor, permission, site, makers, lock=True)
    if grant is None:
        raise PermissionDenied
    ApprovalDelegationUse.objects.create(delegation=grant, actor=actor, reference=reference[:160])
    AuditEvent.objects.create(actor=actor, site=site, action='Delegated approval used', reference=reference[:160],
        detail=f'{grant}; principal {grant.principal_id}; deputy {actor.pk}; {permission}'[:250])
    return grant


@transaction.atomic
def create(admin, principal, deputy, permission, site, starts_at, ends_at, reason):
    if not admin.is_superuser or not access_allowed(admin):
        raise PermissionDenied
    if permission not in POLICIES:
        raise ValidationError('Choose a supported approval action.')
    # Serialize overlapping windows for a deputy, without changing existing role assignments.
    people = {u.pk: u for u in get_user_model().objects.select_for_update().filter(pk__in=[principal.pk, deputy.pk]).order_by('pk')}
    principal, deputy = people[principal.pk], people[deputy.pk]
    if principal.pk == deputy.pk:
        raise ValidationError('Choose different principal and deputy users.')
    _, scoped, _ = POLICIES[permission]
    if scoped != bool(site):
        raise ValidationError('Site approvals need one site; company finance approvals have no site.')
    if not starts_at or not ends_at or not timezone.is_aware(starts_at) or not timezone.is_aware(ends_at):
        raise ValidationError('Choose timezone-aware start and end times.')
    if ends_at <= starts_at or ends_at <= timezone.now():
        raise ValidationError('Choose a valid approval window ending in the future.')
    if starts_at < timezone.now() - timedelta(minutes=1):
        raise ValidationError('Approval delegation cannot be backdated.')
    if not (reason or '').strip():
        raise ValidationError('Explain why approval authority is delegated.')
    if not _qualified(principal, permission, site) or not principal.has_perm('erp.' + permission):
        raise ValidationError('The principal must currently hold the direct action, site and finance authority.')
    if not _qualified(deputy, permission, site):
        raise ValidationError('The deputy needs active access and the existing site/finance scope.')
    if ApprovalDelegation.objects.filter(deputy=deputy, permission=permission, site=site,
        revoked_at__isnull=True, starts_at__lt=ends_at, ends_at__gt=starts_at).exists():
        raise ValidationError('The deputy already has an overlapping delegation for this action and scope.')
    grant = ApprovalDelegation.objects.create(principal=principal, deputy=deputy, permission=permission,
        site=site, starts_at=starts_at, ends_at=ends_at, reason=reason.strip()[:250], created_by=admin)
    AuditEvent.objects.create(actor=admin, site=site, action='Approval authority delegated', reference=str(grant),
        detail=f'{principal.pk} → {deputy.pk}; {permission}; {starts_at.isoformat()} to {ends_at.isoformat()}'[:250])
    return grant


@transaction.atomic
def revoke(admin, pk, reason):
    if not admin.is_superuser or not access_allowed(admin):
        raise PermissionDenied
    grant = ApprovalDelegation.objects.select_for_update().get(pk=pk)
    if grant.revoked_at:
        return grant
    if not (reason or '').strip():
        raise ValidationError('Give the reason for revoking approval authority.')
    grant.revoked_at, grant.revoked_by, grant.revocation_reason = timezone.now(), admin, reason.strip()[:250]
    grant.save(update_fields=['revoked_at', 'revoked_by', 'revocation_reason'])
    AuditEvent.objects.create(actor=admin, site=grant.site, action='Approval delegation revoked', reference=str(grant),
                              detail=grant.revocation_reason)
    return grant


def approvable(queryset, user, permission, site_field='site', maker_fields=('created_by_id',)):
    """Filter approval queues using the same live authority and independence as the service."""
    from django.db.models import Q
    actor = _user(user.pk)
    if not access_allowed(actor):
        return queryset.none()
    for field in maker_fields:
        queryset = queryset.exclude(**{field: actor.pk})
    queryset = _without_proxy_maker(queryset, permission, actor.pk)
    if actor.has_perm('erp.' + permission):
        return queryset
    if permission not in POLICIES:
        return queryset.none()
    now = timezone.now()
    grants = ApprovalDelegation.objects.filter(deputy=actor, permission=permission,
        revoked_at__isnull=True, starts_at__lte=now, ends_at__gt=now).select_related('principal', 'site')
    allowed = None
    for grant in grants:
        if not (_qualified(actor, permission, grant.site) and _qualified(grant.principal, permission, grant.site)
                and grant.principal.has_perm('erp.' + permission)):
            continue
        condition = Q(**{site_field: grant.site}) if POLICIES[permission][1] else Q()
        for field in maker_fields:
            condition &= ~Q(**{field: grant.principal_id})
        candidate = _without_proxy_maker(queryset.filter(condition), permission, grant.principal_id)
        condition = Q(pk__in=candidate.values('pk'))
        allowed = condition if allowed is None else allowed | condition
    return queryset.none() if allowed is None else queryset.filter(allowed).distinct()


def order_makers(order):
    """An RFQ award creates a PO: delegated creation must not hide its principal as a maker."""
    makers = {order.created_by_id}
    if order.kind == 'Purchase' and order.quote_id:
        uses = ApprovalDelegationUse.objects.filter(
            reference=order.quote.rfq.number, actor_id=order.created_by_id,
            delegation__permission='award_rfq')
        makers.update(uses.values_list('delegation__principal_id', flat=True))
    return tuple(makers)


def _without_proxy_maker(queryset, permission, person_id):
    from django.db.models import Exists, OuterRef
    from erp.models import Order
    if permission not in {'approve_purchase_order', 'approve_high_value_purchase'} or queryset.model is not Order:
        return queryset
    uses = ApprovalDelegationUse.objects.filter(reference=OuterRef('quote__rfq__number'),
        actor_id=OuterRef('created_by_id'), delegation__permission='award_rfq', delegation__principal_id=person_id)
    alias = '_approval_proxy_maker_' + str(person_id).replace('-', '')
    return queryset.alias(**{alias: Exists(uses)}).filter(**{alias: False})
