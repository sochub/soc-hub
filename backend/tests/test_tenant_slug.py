"""Tenant slugs: validated on create/update (new values only), explicit nulls refused, and a rename of a tenant
with SSO configured needs an explicit confirmation (the slug is part of its SAML/OIDC URLs)."""
from urllib.parse import quote

from sqlalchemy import delete, select

from app.api.v1.sso import oidc_redirect_uri
from app.db.session import AsyncSessionLocal
from app.models.tenant import Tenant
from app.models.tenant_sso_config import TenantSSOConfig
from app.services.saml_service import sp_urls
from tests.audit_fixtures import hdr, run

BAD = ["", "-lead", "Upper", "has space", "under_score", "a/b", "x" * 65, "ünï"]


def test_create_validates_slug():
    async def body(c, w):
        h = hdr(w, "root")
        for slug in BAD:
            r = await c.post("/api/v1/tenants/", headers=h, json={"name": "wf-test", "slug": slug})
            assert r.status_code == 422, (slug, r.text)
        good = f"wf-test-{w.h}-new"
        try:
            r = await c.post("/api/v1/tenants/", headers=h, json={"name": "wf-test", "slug": good})
            assert r.status_code == 201, r.text
        finally:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(Tenant).where(Tenant.slug == good))
                await db.commit()
    run(body)


def test_update_validates_slug_and_refuses_nulls():
    async def body(c, w):
        h, url = hdr(w, "root"), f"/api/v1/tenants/{w.tb}"
        for slug in BAD:
            r = await c.put(url, headers=h, json={"slug": slug})
            assert r.status_code == 422, (slug, r.text)
        for field in ("name", "slug", "is_active"):
            r = await c.put(url, headers=h, json={field: None})
            assert r.status_code == 422, (field, r.text)
        r = await c.put(url, headers=h, json={"slug": f"wf-test-{w.h}-ren"})
        assert r.status_code == 200 and r.json()["slug"] == f"wf-test-{w.h}-ren", r.text
    run(body)


def test_existing_nonconforming_slug_still_reads_and_saves():
    """Validation applies to new values: a legacy slug is read back and survives an unrelated update."""
    async def body(c, w):
        legacy = f"WF_Test {w.h}"
        async with AsyncSessionLocal() as db:
            (await db.get(Tenant, w.tb)).slug = legacy
            await db.commit()
        h, url = hdr(w, "root"), f"/api/v1/tenants/{w.tb}"
        assert (await c.get(url, headers=h)).json()["slug"] == legacy
        r = await c.put(url, headers=h, json={"name": "wf-test", "slug": legacy})
        assert r.status_code == 200, r.text
        assert quote(legacy, safe="") in sp_urls(legacy)["acs_url"]
        assert " " not in oidc_redirect_uri(legacy)
    run(body)


def test_rename_with_sso_needs_confirmation():
    async def body(c, w):
        async with AsyncSessionLocal() as db:
            db.add(TenantSSOConfig(tenant_id=w.tb, oidc_issuer="https://idp.example", oidc_client_id="cid",
                                   oidc_client_secret_enc="x"))
            await db.commit()
        h, url = hdr(w, "root"), f"/api/v1/tenants/{w.tb}"
        new = f"wf-test-{w.h}-sso"
        r = await c.put(url, headers=h, json={"slug": new})
        assert r.status_code == 409 and "confirm_sso_slug_change" in r.text, r.text
        async with AsyncSessionLocal() as db:
            assert (await db.execute(select(Tenant.slug).where(Tenant.id == w.tb))).scalar_one() != new
        assert (await c.put(url, headers=h, json={"name": "wf-test"})).status_code == 200  # no rename: fine
        r = await c.put(url, headers=h, params={"confirm_sso_slug_change": "true"}, json={"slug": new})
        assert r.status_code == 200 and r.json()["slug"] == new, r.text
    run(body)
