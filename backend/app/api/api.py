from fastapi import APIRouter

from app.api.v1 import auth, users, cases, alerts, artifacts, integrations, copilot, audit_logs, tenants, invitations, iocs, stats, playbooks, case_tasks, sso

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(sso.saml_router, prefix="/auth", tags=["sso"])
# /tenants/sso-config must register before tenants.router's /{tenant_id} routes
api_router.include_router(sso.sso_admin_router, prefix="/tenants", tags=["sso"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(cases.router, prefix="/cases", tags=["cases"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(artifacts.router, prefix="/artifacts", tags=["artifacts"])
api_router.include_router(integrations.router, prefix="/integrations", tags=["integrations"])
api_router.include_router(copilot.router, prefix="/copilot", tags=["copilot"])
api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["audit-logs"])
api_router.include_router(tenants.router, prefix="/tenants", tags=["tenants"])
api_router.include_router(invitations.router, prefix="/invitations", tags=["invitations"])
api_router.include_router(iocs.router, prefix="/iocs", tags=["iocs"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
api_router.include_router(playbooks.router, prefix="/playbooks", tags=["playbooks"])
api_router.include_router(case_tasks.router, prefix="/cases", tags=["case-tasks"])
