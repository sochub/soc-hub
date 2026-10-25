"""Member access tokens for tests: every one is bound to a real, live web session (``sid``).

The session row cascades away with its user, so the usual user cleanup removes it."""
from typing import Optional

from app.auth import sessions
from app.core import security
from app.db.session import AsyncSessionLocal


async def make_web_token(db, user, tenant_id: Optional[int], amr: str = "pwd",
                         sso_tenant_id: Optional[int] = None) -> str:
    """Create (and commit) a web session for ``user`` and return an access token for it.
    ``db`` may be None: a short-lived session of its own is used then."""
    if db is None:
        async with AsyncSessionLocal() as own:
            return await make_web_token(own, user, tenant_id, amr, sso_tenant_id)
    row, _refresh = await sessions.create_session(db, user, kind="web", amr=amr, sso_tenant_id=sso_tenant_id,
                                                  tenant_id=tenant_id)
    await db.commit()
    return security.issue_access_token(user, tenant_id, sid=row.id, amr=amr, sso_tenant_id=sso_tenant_id)
