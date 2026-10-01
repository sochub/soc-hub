"""Auto-enqueue enrichment after commits that create/change IOCs or artifacts."""
import logging

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)
_KEY = "ti_pending"


def _delay(tenant_id, raw_type, value, tlp, force):
    from app.tasks.enrichment import enrich_indicator  # late import: worker import cycle
    enrich_indicator.delay(tenant_id, raw_type, value, tlp, force)


def enqueue(tenant_id, raw_type, value, tlp, force=False) -> None:
    if not settings.ENRICHMENT_ENABLED:
        return
    try:
        _delay(tenant_id, raw_type, value, tlp, force)
    except Exception:
        logger.exception("failed to enqueue enrichment for tenant %s", tenant_id)


def _changed(obj, attrs) -> bool:
    st = inspect(obj)
    return any(st.attrs[a].history.has_changes() for a in attrs)


@event.listens_for(Session, "after_flush")
def _collect(session, flush_context):
    from app.models.artifact import Artifact
    from app.models.ioc import IOC
    pending = session.info.setdefault(_KEY, {})
    for obj in list(session.new) + list(session.dirty):
        is_new = obj in session.new
        if isinstance(obj, IOC) and (is_new or _changed(obj, ("value", "ioc_type", "tlp"))):
            pending[("ioc", id(obj))] = (obj.tenant_id, obj.ioc_type, obj.value, obj.tlp)
        elif isinstance(obj, Artifact) and (is_new or _changed(obj, ("value", "artifact_type"))):
            atype = getattr(obj.artifact_type, "value", obj.artifact_type)
            pending[("art", id(obj))] = (obj.tenant_id, atype, obj.value, None)


@event.listens_for(Session, "after_commit")
def _flush_queue(session):
    for tenant_id, raw_type, value, tlp in session.info.pop(_KEY, {}).values():
        enqueue(tenant_id, raw_type, value, tlp)


@event.listens_for(Session, "after_rollback")
def _clear(session):
    session.info.pop(_KEY, None)
