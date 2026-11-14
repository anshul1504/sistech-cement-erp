"""Replay protection for browser forms that create financial corrections/receipts."""
import hashlib
import json
from uuid import UUID

from django.core.exceptions import ValidationError


def metadata(request):
    raw = request.POST.get('submission_key')
    if not raw:  # Legacy callers use document-reference validation instead.
        return None, ''
    try:
        key = UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('The form submission key is invalid. Reload the form and try again.')
    payload = {name: request.POST.getlist(name) for name in sorted(request.POST) if name not in ('csrfmiddlewaretoken', 'submission_key')}
    fingerprint = hashlib.sha256(json.dumps([request.path, payload], separators=(',', ':')).encode()).hexdigest()
    return key, fingerprint


def replay(model, user, key, fingerprint):
    if key is None:
        return None
    existing = model.objects.select_for_update().filter(submission_key=key).first()
    if existing and (existing.created_by_id != user.pk or existing.submission_fingerprint != fingerprint):
        raise ValidationError('This form was already submitted with different details. Open a fresh form.')
    return existing
