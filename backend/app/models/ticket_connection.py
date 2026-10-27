from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func
from app.db.base_class import Base

TICKET_PROVIDERS = ("jira", "halopsa", "connectwise")


class TicketConnection(Base):
    """A tenant's ticketing/PSA connection (one per tenant). Credentials are Fernet-encrypted JSON
    (app/utils/crypto.py) and never returned by the API."""
    __tablename__ = "ticket_connections"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    provider = Column(String, nullable=False)
    base_url = Column(String, nullable=False)
    credentials_enc = Column(Text, nullable=False)
    settings = Column(JSON, nullable=False, default=dict)  # provider-specific: project_key, board_id, …
    enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    # Cases created at/after auto_create_since with severity >= this get a ticket automatically; NULL = manual only.
    auto_create_min_severity = Column(String, nullable=True)
    auto_create_since = Column(DateTime(timezone=True), nullable=True)
    # Push SOC Hub notes to the ticket (as internal notes where the PSA supports it). Off by default:
    # PSA tickets are often visible to the client.
    push_notes = Column(Boolean, nullable=False, default=False, server_default="false")
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
