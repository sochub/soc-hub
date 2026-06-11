from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base


class CaseTask(Base):
    __tablename__ = "case_tasks"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    phase = Column(String, nullable=False, default="identification")
    title = Column(String, nullable=False)
    description = Column(Text)
    status = Column(String, nullable=False, default="todo")  # 'todo' | 'done'
    order = Column(Integer, nullable=False, default=0)
    source_template_id = Column(Integer, ForeignKey("playbook_templates.id", ondelete="SET NULL"), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    case = relationship("Case", back_populates="tasks")
