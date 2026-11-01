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
