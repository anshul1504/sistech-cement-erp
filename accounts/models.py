from django.db import models

class AuthRateLimit(models.Model):
    """Hashed identifiers only; counters persist across workers and restarts."""
    key = models.CharField(max_length=64, unique=True)
    attempts = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField()
