# Login methods and OIDC SSO

**Status:** Built (Plan 1, 2026-10-24). Covers the per-tenant login method setting
and OpenID Connect (authorization-code + PKCE) as a second SSO protocol next to
[SAML](saml-design.md).

## Login methods

Each tenant picks a login method **per audience**:

| Setting | Audience | Values |
|---|---|---|
| `member_login_method` | Members (admins, analysts, viewers) | `password` \| `saml` \| `oidc` |
| `guest_login_method` | [Case guests](../guests/README.md) | `password` \| `saml` \| `oidc` |

Both default to `password`. Set them in **Settings → Single Sign-On** (admin only),
or `PUT /api/v1/tenants/sso-config`. The save is refused (422) if a chosen protocol's
configuration is incomplete: SAML needs the IdP entity ID, SSO URL and certificate;
OIDC needs issuer, client ID and a stored client secret.

Choosing SAML or OIDC for an audience **turns password sign-in off for that audience**.
There is no separate "require SSO" switch any more.

### Password fallback for admins

So a broken IdP cannot lock a tenant out:

- A tenant **admin** may still sign in with a password to an SSO tenant, but only with
  **MFA**. An admin who has not enrolled yet is taken through MFA enrolment first, then
  signed in.
- Every such sign-in is audited as `sso.password_fallback` (platform audit log, with the
  tenant's method).
- **Super admins** keep password access to every tenant.
- Analysts and viewers have no fallback.

### Multi-tenant users

A tenant's IdP may sign in any **existing member of that tenant**, even one who also
belongs to other tenants. Super admins are still refused, and an unknown email is only
accepted when `auto_provision` is on (role `analyst` or `viewer`, never admin). Existing
users are never attached to a tenant by SSO.

To keep that safe, an SSO session opens **only the tenant whose IdP issued it**. Switching
to another tenant from an SSO session fails with:

- `password_required` when the target tenant uses passwords: sign in again with a password.
- `sso_required` (with the tenant `slug`) when the target uses SSO: sign in through that
  tenant's IdP.

A password session can open a password tenant, and an SSO tenant only through the admin
fallback above.

## Migration notes

Migration `3eda39884bf3` (additive):

- Tenants that had **SAML enabled became SAML-only for members**. Analysts and viewers
  there who signed in with a password must now use SSO. Admins can still use the
  password fallback (with MFA).
- `guest_require_sso` became `guest_login_method` (`true` -> `saml`, otherwise `password`).
- `tenants.guest_require_sso` and `tenant_sso_configs.enabled` remain as columns but are
  no longer read.

## Setting up Okta (OIDC)

1. In Okta: **Applications -> Create App Integration -> OIDC - OpenID Connect**, application
   type **Web Application**.
2. **Sign-in redirect URI:** copy it from sochub **Settings -> Single Sign-On** (read-only
   field). It is `{PUBLIC_BASE_URL}/api/v1/auth/oidc/{slug}/callback`, so
   `PUBLIC_BASE_URL` must be your real external https origin. It must match exactly.
3. Grant type: Authorization Code. Assign the people or groups who may sign in.
4. Copy the **Client ID** and **Client secret** from the Okta app.
5. In sochub **Settings -> Single Sign-On**, fill in:
   - **Issuer:** your Okta issuer, e.g. `https://example.okta.com` (or
     `https://example.okta.com/oauth2/default` for the default authorization server). It
     must be `https://` and must equal the `issuer` in the IdP's
     `/.well-known/openid-configuration` document, character for character.
   - **Client ID** and **Client secret.** The secret is encrypted at rest and **write-only**:
     the API only returns `has_oidc_client_secret`. Leave the field blank on later saves to
     keep the stored one.
   - **Scopes:** `openid email profile` (default). `openid` is required, and the ID token
     must carry an `email` claim.
6. Optionally enable **auto-provision** and a **default role** (`analyst` or `viewer`).
7. Set **Member login method** (and/or **Guest login method**) to `oidc` and save.
8. Test in a private window: the login page's "Sign in with SSO" takes the tenant slug.
   Keep an admin session open until it works.

Sign-in is refused when the ID token has `email_verified: false`; a missing
`email_verified` is accepted. Failures return the user to the login page with a generic
message; details are in the backend log (`OIDC sign-in failed tenant=... code=...`).

## Flow and endpoints

- `GET /api/v1/auth/sso/{slug}/login[?guest=1]` redirects to the tenant's protocol for that audience.
- `GET /api/v1/auth/oidc/{slug}/login` starts the flow; `GET /api/v1/auth/oidc/{slug}/callback`
  is the redirect URI. Both are public.
- After the callback validates the ID token, the user is resolved and signed in exactly
  as in the SAML ACS (session cookie, redirect to `PUBLIC_BASE_URL/login#sso=ok`, no token
  in any URL).

## Security notes

- **State, nonce, PKCE.** Each login generates a random `state`, `nonce` and PKCE verifier
  (S256). They are stored in Redis for 10 minutes. The ID token signature (RS256/ES256,
  keys from the IdP's JWKS), issuer, audience, expiry and nonce are all verified.
- **Single-use state, bound to the browser.** The callback atomically consumes the state
  (replay fails), and requires the `sochub_oidc` cookie set at login to match a secret
  stored with it (stops login CSRF). The cookie is HttpOnly, SameSite=Lax, `Secure` when
  `PUBLIC_BASE_URL` is https, scoped to `/api/v1/auth/oidc`, and cleared on the callback.
  If Redis is down, sign-in fails closed.
- **Cookie is not `__Host-` prefixed.** The cookie is scoped to a path, which the `__Host-`
  prefix forbids. A deployment that shares a parent domain with **untrusted subdomains** is
  exposed to cookie tossing from those subdomains. Host sochub on a domain whose subdomains
  you control.
- **SSRF guard.** The issuer is tenant-admin supplied, so discovery, JWKS and token
  requests must be `https` and are checked against the platform SSRF guard
  (private/loopback/link-local addresses refused, no redirects followed, 10 s timeout).
  For a **self-hosted IdP on an internal network**, list its hostname in
  `OIDC_PRIVATE_HOSTS` (exact match, a JSON list, e.g. `["idp.corp.internal"]`).
  Resolution is checked and then connected, so a DNS-rebinding window remains for a hostile
  name; only allowlist hosts you trust.
- **Secrets.** The client secret is encrypted with the app key and never returned.
- **IdP trust.** A tenant's admins control its IdP, which is why an SSO session only opens
  its own tenant and super admins can never sign in through a tenant IdP.

Out of scope: SLO, IdP-initiated flow, SCIM, group/role mapping, multiple IdPs per tenant.
