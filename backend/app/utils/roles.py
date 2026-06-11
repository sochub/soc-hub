"""Pure helper for resolving a user's effective role in their active tenant."""
from typing import Iterable, Optional


def resolve_active_role(
    is_super_admin: bool,
    active_tenant_id: Optional[int],
    memberships: Iterable,
) -> Optional[str]:
    """Return the caller's effective role string.

    - 'super_admin' when the user is a global super admin.
    - the membership role ('admin' | 'analyst' | 'viewer') for the active tenant.
    - None when the user has no active tenant or isn't a member of it.
    """
    if is_super_admin:
        return "super_admin"
    if active_tenant_id is None:
        return None
    for m in memberships:
        if m.tenant_id == active_tenant_id:
            return m.role
    return None
