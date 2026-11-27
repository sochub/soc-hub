from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func, text, true as sa_true

from app.db.base_class import Base

KINDS = ("vulnerability", "incident", "news")
FORMATS = ("rss", "cisa_kev")


class BriefSource(Base):
    """A public security feed. ``tenant_id`` NULL = built-in, shared by every organization (each can switch it
    off for itself: ``tenants.briefs_disabled_sources``)."""
    __tablename__ = "brief_sources"
    __table_args__ = (
        # Two partial indexes: a plain UNIQUE(tenant_id, url) would let built-ins (NULL tenant) repeat.
        Index("uq_brief_sources_tenant_url", "tenant_id", "url", unique=True,
              postgresql_where=text("tenant_id IS NOT NULL")),
        Index("uq_brief_sources_builtin_url", "url", unique=True, postgresql_where=text("tenant_id IS NULL")),
    )

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(120), nullable=False)
    url = Column(String(2000), nullable=False)
    kind = Column(String(16), nullable=False)
    format = Column(String(16), nullable=False, default="rss", server_default="rss")
    enabled = Column(Boolean, nullable=False, default=True, server_default=sa_true())
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    last_ok_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)  # exception class / HTTP status only, never raw exception text
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BriefItem(Base):
    """One feed entry. All text is untrusted and stored as plain text (HTML stripped)."""
    __tablename__ = "brief_items"
    __table_args__ = (UniqueConstraint("source_id", "guid", name="uq_brief_items_source_guid"),)

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("brief_sources.id", ondelete="CASCADE"), nullable=False)
    guid = Column(String(500), nullable=False)
    url = Column(String(2000), nullable=True)
    title = Column(String(300), nullable=False)
    summary = Column(Text, nullable=False, default="", server_default="")
    published_at = Column(DateTime(timezone=True), nullable=False, index=True)
    kind = Column(String(16), nullable=False)  # copied from the source
    cves = Column(JSON, nullable=False, default=list, server_default="[]")
    # [{"type", "value"}] found in title + summary. SQL NULL = not computed yet (rows older than the column; the
    # fetch job fills them), [] = computed, none found.
    indicators = Column(JSON(none_as_null=True), nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())


class BriefAI(Base):
    """The AI-written brief of an item, per organization (each one's own model writes its own)."""
    __tablename__ = "brief_ai"
    __table_args__ = (UniqueConstraint("item_id", "tenant_id", name="uq_brief_ai_item_tenant"),)

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("brief_items.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(16), nullable=False)  # ready | failed
    what_happened = Column(Text, nullable=True)
    why_it_matters = Column(Text, nullable=True)
    lessons = Column(JSON, nullable=False, default=list, server_default="[]")
    model = Column(String(200), nullable=True)  # provider/model that wrote it
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BriefUserState(Base):
    __tablename__ = "brief_user_state"

    item_id = Column(Integer, ForeignKey("brief_items.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    read_at = Column(DateTime(timezone=True), nullable=True)
    saved_at = Column(DateTime(timezone=True), nullable=True)
