from app.db.base_class import Base  # noqa: F401

# Import all models here so that Base has them registered
# before any relationships are resolved.
from app.models.tenant import Tenant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.membership import TenantMembership  # noqa: F401
from app.models.case import Case, Alert, TimelineEvent, CaseLink  # noqa: F401
from app.models.artifact import Artifact  # noqa: F401
from app.models.case_artifact import CaseArtifact  # noqa: F401
from app.models.audit_log import AuditLog  # noqa: F401
from app.models.invitation import Invitation  # noqa: F401
from app.models.copilot_session import CopilotSession, CopilotMessage  # noqa: F401
from app.models.ioc import IOC  # noqa: F401
from app.models.playbook import PlaybookTemplate, PlaybookTaskTemplate  # noqa: F401
from app.models.case_task import CaseTask  # noqa: F401
from app.models.tenant_sso_config import TenantSSOConfig  # noqa: F401
