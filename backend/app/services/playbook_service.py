"""Playbook template creation and publishing (shared by the REST routes and the MCP tools)."""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.playbook import PlaybookTemplate, PlaybookTaskTemplate
from app.schemas.playbook import PlaybookTemplateCreate, PlaybookTemplateUpdate
from app.utils.audit import create_audit_log


async def _load(db: AsyncSession, template_id: int) -> PlaybookTemplate:
    res = await db.execute(
        select(PlaybookTemplate).options(selectinload(PlaybookTemplate.tasks))
        .where(PlaybookTemplate.id == template_id)
        .execution_options(populate_existing=True)
    )
    return res.scalars().first()


async def create_template(
    db: AsyncSession, *, tenant_id: int, data: PlaybookTemplateCreate, user_id: int, status: str = "published"
) -> PlaybookTemplate:
    """Create a tenant playbook template (tasks included) and commit. Drafts are never offered or applied."""
    t = PlaybookTemplate(
        tenant_id=tenant_id, name=data.name, category=data.category, description=data.description,
        is_system=False, status=status, created_by=user_id,
    )
    db.add(t)
    await db.flush()
    for i, task in enumerate(data.tasks):
        db.add(PlaybookTaskTemplate(
            template_id=t.id, phase=task.phase, title=task.title,
            description=task.description, order=task.order or i,
        ))
    await db.commit()
    return await _load(db, t.id)


async def publish_template(
    db: AsyncSession, *, tenant_id: int, template_id: int, user_id: Optional[int]
) -> Optional[PlaybookTemplate]:
    """Publish the tenant's own template. Idempotent. Returns None when it is not the tenant's."""
    t = await _load(db, template_id)
    if not t or t.tenant_id != tenant_id or t.is_system:
        return None
    if t.status != "published":
        t.status = "published"
        await create_audit_log(db=db, entity_type="playbook", entity_id=t.id, action="playbook_published",
                               tenant_id=tenant_id, user_id=user_id, changes={"name": t.name})
        await db.commit()
        t = await _load(db, template_id)
    return t


async def tenant_template(db: AsyncSession, *, tenant_id: int, template_id: int) -> Optional[PlaybookTemplate]:
    """The tenant's own (non-system) template with its tasks, or None."""
    t = await _load(db, template_id)
    return t if t is not None and t.tenant_id == tenant_id and not t.is_system else None


async def update_template(db: AsyncSession, *, template: PlaybookTemplate, data: PlaybookTemplateUpdate) -> PlaybookTemplate:
    """Set the given fields; ``tasks`` (when not None) replaces every task. Commits. The caller checks ownership."""
    t = template
    if data.name is not None:
        t.name = data.name
    if data.category is not None:
        t.category = data.category
    if data.description is not None:
        t.description = data.description
    if data.tasks is not None:
        for task in list(t.tasks):
            await db.delete(task)
        await db.flush()
        for i, task in enumerate(data.tasks):
            db.add(PlaybookTaskTemplate(
                template_id=t.id, phase=task.phase, title=task.title,
                description=task.description, order=task.order or i,
            ))
    await db.commit()
    return await _load(db, t.id)


async def delete_template(db: AsyncSession, *, template: PlaybookTemplate) -> None:
    """Delete the template and its tasks. Commits. The caller checks ownership."""
    await db.delete(template)
    await db.commit()
