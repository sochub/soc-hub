import uuid

import pytest
from sqlalchemy import delete

import app.db.base  # noqa: F401
from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import ArtifactType
from app.models.artifact_type_definition import ArtifactTypeDefinition
from app.models.tenant import Tenant
from app.services.artifact_types import UnknownArtifactType, payload_values, resolve_type


def test_payload_values_scalars_lists_nested():
    p = {"user_name": "bob", "port": 22, "ok": True, "users": ["a", "b", "a", {"x": 1}, None, " "],
         "user": {"email": "x@y.z"}, "blank": "", "obj": {"a": 1}}
    assert payload_values(p, "user_name") == ["bob"]
    assert payload_values(p, "port") == ["22"]
    assert payload_values(p, "ok") == ["True"]
    assert payload_values(p, "users") == ["a", "b"]
    assert payload_values(p, "user.email") == ["x@y.z"]
    assert payload_values(p, "blank") == []
    assert payload_values(p, "obj") == []
    assert payload_values(p, "missing") == []
    assert payload_values(p, "user_name.deeper") == []
    assert payload_values(None, "x") == []
    assert payload_values({"many": [str(i) for i in range(30)]}, "many") == [str(i) for i in range(20)]


@pytest.mark.asyncio
async def test_resolve_type_builtin_custom_unknown_and_tenant_scoped():
    slug = f"at-test-{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        t1, t2 = Tenant(name=slug, slug=slug), Tenant(name=slug + "b", slug=slug + "b")
        db.add_all([t1, t2])
        await db.flush()
        d = ArtifactTypeDefinition(tenant_id=t1.id, key="username", label="Username")
        db.add(d)
        await db.commit()
        try:
            assert await resolve_type(db, t1.id, "ip") == (ArtifactType.IP, None)
            atype, got = await resolve_type(db, t1.id, "username")
            assert atype == ArtifactType.OTHER and got.id == d.id
            with pytest.raises(UnknownArtifactType):
                await resolve_type(db, t2.id, "username")  # other tenant's key
            with pytest.raises(UnknownArtifactType):
                await resolve_type(db, t1.id, "nope")
        finally:
            await db.execute(delete(ArtifactTypeDefinition).where(ArtifactTypeDefinition.id == d.id))
            await db.execute(delete(Tenant).where(Tenant.id.in_([t1.id, t2.id])))
            await db.commit()
    await engine.dispose()
