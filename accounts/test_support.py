"""Explicit authenticated-and-confirmed fixture for tests of business permissions/workflows.

Like Django's force_login, this is test setup rather than credential verification.
Password, throttling, binding, expiry and denial are exercised separately in test_reauth.
"""
from .reauth import confirm


def force_login_recent(client, user, **kwargs):
    client.force_login(user, **kwargs)
    session = client.session
    confirm(session, user)
    session.save()
