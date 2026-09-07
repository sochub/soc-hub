import asyncio
import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.worker import celery_app
from app.db.session import AsyncSessionLocal, engine
from app.models.case import Case
from app.models.case_artifact import CaseArtifact
from app.models.artifact import Artifact
from app.models.ioc import IOC
from app.models.playbook import PlaybookTemplate
from app.models.case_triage import CaseTriageResult
from app.services.ai_service import AIService
from app.api.v1.copilot import _find_related_cases

logger = logging.getLogger(__name__)

# Deterministic playbook matching — never LLM-guessed, so it can only ever
# suggest a playbook the tenant actually imported. Categories mirror the
# marketplace seed set (app/scripts/seed_playbooks.py).
_CATEGORY_KEYWORDS = {
    "phishing": ["phish", "spearphish", "malicious link", "suspicious email", "credential harvest"],
    "malware": ["malware", "trojan", "virus", "backdoor", "infection", "suspicious executable"],
    "ransomware": ["ransomware", "encrypt", "ransom note", "extortion"],
    "unauthorized-access": ["unauthorized", "vpn", "brute force", "account takeover", "suspicious login"],
    "exfiltration": ["exfiltrat", "data leak", "large outbound", "unusual upload"],
    "credential-access": ["password spray", "credential stuffing", "mfa bypass", "repeated login"],
}


def best_matching_category(title: str, description: str, tags: List[str]) -> Optional[str]:
    """Pure keyword-overlap scoring, kept separate from the DB lookup so it's
    unit-testable without a database."""
    haystack = " ".join([title or "", description or "", *tags]).lower()
    best_category, best_score = None, 0
    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in haystack)
        if score > best_score:
            best_category, best_score = category, score
    return best_category


async def _match_playbook(db: AsyncSession, tenant_id: int, case: Case) -> Optional[int]:
    category = best_matching_category(case.title or "", case.description or "", case.tags or [])
    if not category:
        return None
    res = await db.execute(
        select(PlaybookTemplate.id)
        .where(PlaybookTemplate.tenant_id == tenant_id, PlaybookTemplate.category == category)
        .limit(1)
    )
    return res.scalars().first()


async def _indicator_values(db: AsyncSession, tenant_id: int, case_id: int) -> List[str]:
    a = await db.execute(
        select(Artifact.value).join(CaseArtifact, CaseArtifact.artifact_id == Artifact.id)
        .where(CaseArtifact.case_id == case_id, Artifact.tenant_id == tenant_id)
    )
    i = await db.execute(select(IOC.value).where(IOC.case_id == case_id, IOC.tenant_id == tenant_id))
    return [v for v in [*a.scalars().all(), *i.scalars().all()] if v]


async def _run(triage_id: int) -> None:
    try:
        await _run_with_session(triage_id)
    finally:
        # The engine's pooled connections are bound to this call's event loop.
        # A Celery prefork worker reuses the process for the next task, whose
        # asyncio.run() creates a *new* loop — without disposing here, the
        # next task would reuse a now-invalid connection and fail with
        # "Task got Future attached to a different loop".
        await engine.dispose()


async def _run_with_session(triage_id: int) -> None:
    async with AsyncSessionLocal() as db:
        triage = (await db.execute(
            select(CaseTriageResult).where(CaseTriageResult.id == triage_id)
        )).scalars().first()
        if not triage:
            return

        case = (await db.execute(select(Case).where(Case.id == triage.case_id))).scalars().first()
        if not case:
            triage.status = "failed"
            triage.error_message = "Case no longer exists."
            await db.commit()
            return

        try:
            indicator_values = await _indicator_values(db, triage.tenant_id, case.id)
            generated = await AIService().generate_triage({
                "title": case.title,
                "description": case.description,
                "tags": case.tags or [],
                "indicator_values": indicator_values,
            })
            if generated is None:
                triage.status = "failed"
                triage.error_message = "AI Assistant unavailable: Ollama service is not running."
                await db.commit()
                return

            triage.proposed_severity = generated.get("severity")
            triage.proposed_tags = generated.get("tags") or []
            triage.next_steps_text = generated.get("next_steps_text")

            related = await _find_related_cases(db, triage.tenant_id, case.id, None)
            triage.related_case_ids = [r.case_id for r in related][:5]

            triage.proposed_playbook_template_id = await _match_playbook(db, triage.tenant_id, case)

            triage.status = "completed"
            await db.commit()
        except Exception as e:
            logger.exception("Case triage failed for case %d: %s", case.id, e)
            await db.rollback()
            triage.status = "failed"
            triage.error_message = str(e)[:500]
            await db.commit()


@celery_app.task(acks_late=True, max_retries=1)
def run_case_triage_task(triage_id: int) -> None:
    """Runs async DB/LLM work from a sync Celery task — the app's ORM layer is
    fully async, so there's no sync session to reuse here."""
    asyncio.run(_run(triage_id))
