"""Explicit company-wide scope for the central accounting workspace.

Operational site assignments never implicitly grant access to all company books.
This scope permission grants no posting, approval or export action by itself.
"""
from django.core.exceptions import PermissionDenied

from .services import permitted as module_permission


def require_company_finance(user):
    if not user.has_perm('erp.access_company_finance'):
        raise PermissionDenied


def permitted(user, permission):
    require_company_finance(user)
    module_permission(user, permission)
