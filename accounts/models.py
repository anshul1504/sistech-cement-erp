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
