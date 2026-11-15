from django.db import models
import uuid
from django.conf import settings

class AuthRateLimit(models.Model):
    """Hashed identifiers only; counters persist across workers and restarts."""
    key = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()


class EmailOTPChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    email = models.EmailField()
    code_hash = models.CharField(max_length=128)
    session_hash = models.CharField(max_length=64)
    credential_hash = models.CharField(max_length=64)
    password_verified_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    delivered = models.BooleanField(default=False)


class UserProfile(models.Model):
    """Self-service profile details for each user (email, roles and sites stay admin-managed)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField('Mobile number', max_length=20, blank=True)
    designation = models.CharField(max_length=80, blank=True)
    department = models.CharField(max_length=80, blank=True)
    photo = models.BinaryField(null=True, blank=True, editable=False)
    photo_type = models.CharField(max_length=20, blank=True, editable=False)
    work_backup = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    access_expires_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Profile of {self.user}'

    @classmethod
    def for_user(cls, user):
        profile, _ = cls.objects.get_or_create(user=user)
        return profile

    @property
    def photo_version(self):
        return int(self.updated_at.timestamp()) if self.updated_at else 0

    @property
    def password_age_days(self):
        from django.utils import timezone
        return (timezone.now() - self.password_changed_at).days if self.password_changed_at else None


class EmergencyContact(models.Model):
    """People to call if something happens to this employee at a plant or depot. Owned by the user."""
    RELATIONSHIPS = [('spouse', 'Spouse'), ('parent', 'Parent'), ('sibling', 'Sibling'), ('child', 'Child'),
                     ('relative', 'Relative'), ('friend', 'Friend'), ('other', 'Other')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='emergency_contacts')
    name = models.CharField(max_length=120)
    relationship = models.CharField(max_length=15, choices=RELATIONSHIPS)
    phone = models.CharField('Phone', max_length=20)
    alt_phone = models.CharField('Alternate phone', max_length=20, blank=True)
    address = models.CharField(max_length=200, blank=True)
    is_primary = models.BooleanField('Primary contact', default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', 'name']

    def __str__(self):
        return f'{self.name} ({self.get_relationship_display()})'


class ApprovalDelegation(models.Model):
    """Immutable authority window; revocation retains the original grant and its uses."""
    principal = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='approval_delegations_given')
    deputy = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='approval_delegations_received')
    permission = models.CharField(max_length=80)
    site = models.ForeignKey('erp.Site', null=True, blank=True, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=250)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    revocation_reason = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [
            models.CheckConstraint(condition=models.Q(ends_at__gt=models.F('starts_at')), name='delegation_positive_window'),
            models.CheckConstraint(condition=~models.Q(principal=models.F('deputy')), name='delegation_distinct_people'),
        ]

    def __str__(self):
        return f'DEL-{self.pk}'


class ApprovalDelegationUse(models.Model):
    delegation = models.ForeignKey(ApprovalDelegation, on_delete=models.PROTECT, related_name='uses')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reference = models.CharField(max_length=160)
    used_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-used_at', '-pk']


class ApprovalRouting(models.Model):
    """Accountable reviewer only; assignment never grants authority or excludes other eligible checkers."""
    kind = models.CharField(max_length=40)
    object_id = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=160)
    site = models.ForeignKey('erp.Site', null=True, blank=True, on_delete=models.PROTECT)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    assigned_at = models.DateTimeField()
    reason = models.CharField(max_length=250)

    class Meta:
        ordering = ['-assigned_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['kind','object_id'], name='one_approval_routing_per_source')]


class WorkHandoffRun(models.Model):
    request_key = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    payload_digest = models.CharField(max_length=64)
    source_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    reason = models.CharField(max_length=250)
    automatic = models.BooleanField(default=False)
    source_event = models.ForeignKey('erp.AuditEvent', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=12, default='empty', choices=[('complete','Complete'),('partial','Partial'),('attention','Needs attention'),('empty','No pending work')])
    transferred = models.PositiveIntegerField(default=0)
    blocked = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at','-pk']

    def __str__(self):
        return f'HAND-{self.pk}'


class WorkHandoffItem(models.Model):
    run = models.ForeignKey(WorkHandoffRun, on_delete=models.PROTECT, related_name='items')
    kind = models.CharField(max_length=40)
    object_id = models.PositiveBigIntegerField()
    reference = models.CharField(max_length=160)
    site = models.ForeignKey('erp.Site', null=True, blank=True, on_delete=models.PROTECT)
    source_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    outcome = models.CharField(max_length=10, choices=[('moved','Transferred'),('blocked','Needs attention')])
    note = models.CharField(max_length=250)
    snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(fields=['run','kind','object_id'], name='one_handoff_item_per_run_source')]
        indexes = [models.Index(fields=['kind','object_id','outcome'], name='handoff_source_history')]


class WorkHandoffGuard(models.Model):
    """Per-source mutex, separate from credentials/profile locks used during account disabling."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
