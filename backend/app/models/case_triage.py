from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, JSON
from sqlalchemy.sql import func
from app.db.base_class import Base


class CaseTriageResult(Base):
    """One row per AI-triage run on a case (auto-on-create or manual re-triage).

    Re-triage inserts a new row rather than mutating the last one, so a prior
    run's confirm/dismiss history is preserved; only the latest row is shown.
    """
    __tablename__ = "case_triage_results"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    triggered_by = Column(String, nullable=False, default="manual")  # 'auto_on_create' | 'manual'
    triggered_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending | completed | failed
    error_message = Column(Text, nullable=True)

    proposed_severity = Column(String, nullable=True)
    proposed_tags = Column(JSON, nullable=True)
    severity_tags_status = Column(String, nullable=False, default="proposed")  # proposed | confirmed | dismissed

    proposed_playbook_template_id = Column(
        Integer, ForeignKey("playbook_templates.id", ondelete="SET NULL"), nullable=True
    )
    playbook_status = Column(String, nullable=False, default="proposed")

    related_case_ids = Column(JSON, nullable=True)  # list[int], display-only

    next_steps_text = Column(Text, nullable=True)
    next_steps_status = Column(String, nullable=False, default="proposed")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
