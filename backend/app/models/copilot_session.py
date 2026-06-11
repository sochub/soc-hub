from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base


class CopilotSession(Base):
    __tablename__ = "copilot_sessions"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable: a NULL case_id is the user's "general" (case-less) session used
    # by the global copilot widget outside of a specific case.
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    messages = relationship("CopilotMessage", back_populates="session", order_by="CopilotMessage.created_at")
    case = relationship("Case")
    user = relationship("User")


class CopilotMessage(Base):
    __tablename__ = "copilot_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("copilot_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    # Optional structured action the copilot proposed with this message
    # (ActionProposal shape). Executed only on user confirmation.
    action = Column(JSON, nullable=True)
    # Optional list of proactive suggestions (ActionProposal shapes) detected
    # from the conversation, e.g. "new IOC mentioned — add it?".
    suggestions = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("CopilotSession", back_populates="messages")
