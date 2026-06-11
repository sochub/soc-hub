from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base

# IR phases, in display order.
IR_PHASES = ["identification", "containment", "eradication", "recovery", "lessons_learned"]


class PlaybookTemplate(Base):
    """A playbook template.

    - tenant_id IS NULL + is_system=True  -> global marketplace template (read-only)
    - tenant_id set      + is_system=False -> a tenant's own template
    `source_template_id` records the marketplace template a tenant copy was imported from.
    """
    __tablename__ = "playbook_templates"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False, default="other", index=True)
    description = Column(Text)
    is_system = Column(Boolean, nullable=False, default=False)
    source_template_id = Column(Integer, ForeignKey("playbook_templates.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tasks = relationship(
        "PlaybookTaskTemplate",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="PlaybookTaskTemplate.order",
    )


class PlaybookTaskTemplate(Base):
    __tablename__ = "playbook_task_templates"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("playbook_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    phase = Column(String, nullable=False, default="identification")
    title = Column(String, nullable=False)
    description = Column(Text)
    order = Column(Integer, nullable=False, default=0)

    template = relationship("PlaybookTemplate", back_populates="tasks")
