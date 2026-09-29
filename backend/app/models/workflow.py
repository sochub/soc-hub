from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db.base_class import Base


class Workflow(Base):
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    trigger_type = Column(String, nullable=False)
    trigger_filter = Column(Text, nullable=True)
    graph = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    workflow_version = Column(Integer, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="SET NULL"), nullable=True, index=True)
    alert_id = Column(Integer, ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True)
    # queued (loop children only) | running | waiting | succeeded | failed | cancelled
    status = Column(String, nullable=False, default="running", index=True)
    trigger_payload = Column(JSON, nullable=True)
    graph_snapshot = Column(JSON, nullable=False)
    depth = Column(Integer, nullable=False, default=0)
    is_dry_run = Column(Boolean, nullable=False, default=False)
    dry_run_mocks = Column(JSON, nullable=True)
    parent_run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    parent_step_id = Column(Integer, ForeignKey("workflow_run_steps.id", ondelete="CASCADE", use_alter=True), nullable=True, index=True)
    loop_index = Column(Integer, nullable=True)
    loop_item = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)


class WorkflowRunStep(Base):
    __tablename__ = "workflow_run_steps"
    __table_args__ = (UniqueConstraint("run_id", "node_id", name="uq_run_step_node"),)

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="pending")
    input = Column(JSON, nullable=True)
    output = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    wait_token = Column(String, unique=True, nullable=True)
    wait_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
