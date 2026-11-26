"""Login-page SSO discovery: an email's domain points at the one SSO organization that lists it."""
from app.db.session import AsyncSessionLocal
from app.models.tenant_sso_config import TenantSSOConfig
from tests.guest_fixtures import client, make_tenant, run


def test_sso_discover_by_domain():
    async def body(ids):
        async with AsyncSessionLocal() as db:
            async def org(method, domains, active=True):
                t = await make_tenant(db, ids)
                t.member_login_method, t.is_active = method, active
                db.add(TenantSSOConfig(tenant_id=t.id, idp_entity_id="e", idp_sso_url="https://idp.example/sso",
                                       allowed_email_domains=domains))
                await db.commit()
                return t
            sso = await org("saml", [f"{ids.h}-corp.example"])
            await org("password", [f"{ids.h}-pwd.example"])  # lists domains but members use passwords
            await org("oidc", [f"{ids.h}-off.example"], active=False)
            await org("saml", [f"{ids.h}-shared.example"])
            await org("oidc", [f"{ids.h}-shared.example"])

        async with client() as cl:
            async def slug(email):
                r = await cl.post("/api/v1/auth/sso/discover", json={"email": email})
                assert r.status_code == 200, r.text
                return r.json()["slug"]
            assert await slug(f"Someone@{ids.h}-CORP.example ") == sso.slug  # case and spaces ignored
            assert await slug(f"admin@{ids.h}-corp.example") == sso.slug  # per domain: says nothing about accounts
            for email in (f"a@{ids.h}-pwd.example", f"a@{ids.h}-off.example", f"a@{ids.h}-shared.example",
                          "a@unknown.example", "not-an-email", ""):
                assert await slug(email) is None, email
            assert (await cl.post("/api/v1/auth/sso/discover", json={"email": "a" * 400})).status_code == 422
    run(body)
