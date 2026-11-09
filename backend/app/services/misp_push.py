"""Push a case's IOCs to a MISP event (one event per case, reused via a CaseLink row with system="misp")."""
import asyncio
import json
from typing import Optional

import httpx
from sqlalchemy import select

from app.enrichment.sources.misp import check_url
from app.models.case import CaseLink

SYSTEM = "misp"
TRANSPORT: Optional[httpx.AsyncBaseTransport] = None  # test seam

_TYPE_MAP = {"ip_address": "ip-dst", "domain": "domain", "url": "url", "email": "email-src",
             "registry_key": "regkey", "mutex": "mutex", "user_agent": "user-agent"}
_HASH_LEN = {32: "md5", 40: "sha1", 64: "sha256"}
_DUP_WORDS = ("already exist", "similar attribute")


class MispPushError(Exception):
    """Message is safe to show to users (never contains the API key)."""

    def __init__(self, message: str, pushed=None, skipped=None):
        super().__init__(message)
        self.pushed, self.skipped = list(pushed or []), list(skipped or [])


def misp_type(ioc) -> Optional[str]:
    if ioc.ioc_type == "file_hash":
        return _HASH_LEN.get(len((ioc.value or "").strip()))
    return _TYPE_MAP.get(ioc.ioc_type)


def _skip_reason(ioc) -> Optional[str]:
    if (ioc.tlp or "").lower() == "red":
        return "tlp_red"
    if ioc.ioc_type == "file_hash":
        return None if misp_type(ioc) else "unmappable_hash"
    return None if misp_type(ioc) else "unmappable"


def _is_duplicate(resp: httpx.Response) -> bool:
    if resp.status_code not in (403, 422):
        return False
    return any(w in resp.text.lower() for w in _DUP_WORDS)


async def push_iocs(db, *, tenant_id: int, case, iocs, misp_cfg: dict) -> dict:
    """misp_cfg is the parsed config ({"url","key","verify"}). Raises MispPushError on MISP/transport failure."""
    err = await asyncio.to_thread(check_url, misp_cfg["url"])
    if err:
        raise MispPushError("MISP host not allowed")
    base = misp_cfg["url"]
    skipped, todo = [], []
    for ioc in iocs:
        reason = _skip_reason(ioc)
        if reason:
            skipped.append({"id": ioc.id, "reason": reason})
        else:
            todo.append(ioc)

    link = (await db.execute(select(CaseLink).where(
        CaseLink.case_id == case.id, CaseLink.tenant_id == tenant_id, CaseLink.system == SYSTEM,
        CaseLink.task_id.is_(None)).order_by(CaseLink.id))).scalars().first()
    pushed = []
    headers = {"Authorization": misp_cfg["key"], "Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": "SOC-Hub/1.0"}
    try:
        async with httpx.AsyncClient(timeout=15.0, verify=misp_cfg["verify"], follow_redirects=False,
                                     transport=TRANSPORT, headers=headers) as c:
            if todo and link is None:
                resp = await c.post(f"{base}/events/add", json={"Event": {
                    "info": f"SOC Hub case #{case.id}: {case.title}", "distribution": 0, "analysis": 0,
                    "threat_level_id": 2}})
                if resp.status_code >= 400:
                    raise MispPushError(f"MISP request failed (HTTP {resp.status_code})")
                ev = resp.json().get("Event") or {}
                uuid, eid = ev.get("uuid"), ev.get("id")
                if not uuid or eid is None:
                    raise MispPushError("MISP returned an unexpected response")
                link = CaseLink(case_id=case.id, tenant_id=tenant_id, system=SYSTEM, external_id=str(uuid),
                                url=f"{base}/events/view/{uuid}")
                db.add(link)
                await db.flush()
            for ioc in todo:
                resp = await c.post(f"{base}/attributes/add/{link.external_id}", json={
                    "type": misp_type(ioc), "value": ioc.value.strip(), "to_ids": True,
                    "comment": ioc.description or ""})
                if resp.status_code < 400 or _is_duplicate(resp):
                    pushed.append(ioc.id)
                else:
                    raise MispPushError(f"MISP request failed (HTTP {resp.status_code})")
    except MispPushError as e:
        e.pushed, e.skipped = pushed, skipped
        raise
    except (httpx.HTTPError, ValueError, AttributeError):
        raise MispPushError("MISP request failed", pushed, skipped) from None
    return {"event_uuid": link.external_id if link else None, "event_url": link.url if link else None,
            "pushed": pushed, "skipped": skipped}
