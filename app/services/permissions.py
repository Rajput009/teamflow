from app.core.exceptions import ForbiddenError
from app.models import Membership, OrgRole


def require_role(membership: Membership, allowed: set[OrgRole]) -> None:
    """Guard an operation behind a minimum-role set. Layer 2 of the RBAC chain."""
    if membership.role not in allowed:
        raise ForbiddenError()


def is_admin_or_above(membership: Membership) -> bool:
    return membership.role in {OrgRole.OWNER, OrgRole.ADMIN}


def is_manager_or_above(membership: Membership) -> bool:
    return membership.role in {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER}
