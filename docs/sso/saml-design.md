# Per-tenant SAML SSO — design & build record

**Status:** Approved 2026-06-10, building.

## Decisions (locked)
| Fork | Decision |
|---|---|
| Entry flow | "Sign in with SSO" on Login → user enters **tenant slug** → redirect to that tenant's IdP |
| Provisioning | **JIT, configurable per tenant**: `auto_provision` toggle + `default_role` (analyst/viewer, never admin). Off → unknown users rejected |
| Enforcement | **Optional in v1** — password login keeps working; "require SSO" toggle deferred |
| Library | **python3-saml** (OneLogin) — full signature/assertion validation; needs xmlsec1 native deps in the backend image |
| Test IdP | mocksaml.com for verification; docs for Okta/Entra |

## Data model — `tenant_sso_configs` (1:1 tenant, migration `b3c4d5e6f7a8`)
`id, tenant_id (unique FK cascade), enabled bool, idp_entity_id, idp_sso_url,
idp_x509_cert (text), auto_provision bool default false, default_role str default
'viewer', created_at, updated_at`.
Separate table so tenant **admins** can edit without touching super_admin tenant routes.

## Endpoints
- `GET /api/v1/auth/saml/{slug}/metadata` — SP metadata XML (public)
- `GET /api/v1/auth/saml/{slug}/login` — build AuthnRequest, 307 → IdP (public)
- `POST /api/v1/auth/saml/{slug}/acs` — validate response (strict; signature vs stored cert, audience, conditions) → email from NameID → find-or-provision → JWT (`active_tenant_id`=tenant) → redirect `PUBLIC_BASE_URL/login#sso_token=…` (fragment: avoids server logs). Errors → `#sso_error=…`
- `GET/PUT /api/v1/tenants/sso-config` — active-tenant scoped, `require_admin`. GET also returns the SP values to paste into the IdP (ACS URL, entity ID, metadata URL). Registered BEFORE `/tenants/{tenant_id}` to avoid path collision.

## Find-or-provision rules (`_resolve_sso_user`)
- existing user + membership → ok
- existing user, no membership → membership(default_role) only if auto_provision else reject
- unknown user → if auto_provision: create user (random unusable password, is_super_admin=False) + membership(default_role) else reject
- default_role clamped to analyst|viewer. SSO logins + provisioning audit-logged.

## Config / infra
- `settings.PUBLIC_BASE_URL` (default `http://localhost`) — externally visible origin for SP URLs behind nginx.
- `requirements.txt`: + `python3-saml`. Dockerfile: + `libxmlsec1-dev xmlsec1 pkg-config libxml2-dev libxslt1-dev` → backend **image rebuild** required.

## Frontend
- **Settings** page: "Single Sign-On (SAML)" panel (admin-only): enable toggle, IdP entity ID / SSO URL / x509 cert textarea, auto-provision + default role; read-only SP info box with copy buttons.
- **Login**: "Sign in with SSO" → slug input → `window.location = /api/v1/auth/saml/{slug}/login`; on mount, parse `#sso_token` (store, enter app) / `#sso_error` (show).

## Out of scope v1
Require-SSO enforcement, SLO, IdP-initiated flow, SCIM, multiple IdPs/tenant.

## Verification plan
Unit-test `_resolve_sso_user` against the live container DB with scoped cleanup; metadata XML 200 + correct entityID; login redirect carries SAMLRequest to the configured IdP URL; garbage ACS → clean `#sso_error` redirect; full browser roundtrip documented with mocksaml.com.
