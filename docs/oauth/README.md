# Sessions and the OAuth authorization server

SOC Hub issues its own sign-in sessions and acts as an OAuth 2.1-style authorization server so outside
applications (the first one is the MCP server) can act on behalf of a signed-in analyst.

Code: `backend/app/oauth/{resources,routes,tokens}.py`, `backend/app/auth/{sessions,web}.py`,
`backend/app/core/session_cookie.py`, `backend/app/api/v1/sessions.py`.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/oauth-authorization-server` | Authorization server metadata (RFC 8414) at the site root. `issuer` is `PUBLIC_BASE_URL`. |
| `GET /.well-known/oauth-protected-resource` | Protected resource metadata, proxied to the backend (served by the resource itself). |
| `POST /api/v1/oauth/register` | Dynamic client registration (RFC 7591), public clients only. |
| `GET /api/v1/oauth/authorize` | Validates the request, then redirects (303) to the consent screen `/oauth/consent`. |
| `GET /api/v1/oauth/authorize/info` | Consent screen data: client name, redirect host, resource, scope, selectable tenants. |
| `POST /api/v1/oauth/authorize/decision` | Approve or deny. Returns the redirect URL for the client. |
| `POST /api/v1/oauth/token` | `authorization_code` and `refresh_token` grants (form-encoded). |
| `POST /api/v1/oauth/revoke` | Token revocation (RFC 7009); always answers 200. |

nginx proxies the two `/.well-known/oauth-*` paths and `/mcp` to the backend (`frontend/nginx/default.conf.template`).
The SPA pages, `/oauth/consent` included, are served with `X-Frame-Options: DENY` and
`Content-Security-Policy: frame-ancestors 'none'`, so Approve can never be clickjacked.

PKCE `S256` is mandatory. Only public clients are supported (`token_endpoint_auth_method: none`).

## Dynamic client registration

`POST /api/v1/oauth/register` is unauthenticated and limited to **20 registrations per hour per IP**.
Rules:

- `client_name`: required string, 1 to 200 characters, no control, format or line/paragraph separator
  characters.
- `redirect_uris`: 1 to 10 strings, each at most 2000 characters. A URI is refused when it:
  - contains a backslash, a `#` fragment, whitespace or any control character;
  - has userinfo (`user:pass@`) or an invalid port;
  - is `https` without a host, or `http` to anything other than a loopback host (`localhost`, `127.0.0.1`, `::1`);
  - uses a custom scheme that is malformed or one of the denied schemes (`javascript`, `data`, `file`, `vbscript`,
    `about`, `blob`, `http`, `https`, `ws`, `wss`, `ftp` as a custom scheme).
- `token_endpoint_auth_method` must be `none`; `grant_types` (if sent) exactly `authorization_code` and
  `refresh_token`; `response_types` (if sent) `["code"]`.

At authorization time the redirect URI must match a registered one exactly. For `http` loopback URIs only the port
may differ (RFC 8252). Registration is audited (`oauth.client_registered`).

Registered clients are unverified: the consent screen says so and shows the redirect host, not just the name.
**DCR clients never expire.** If the `oauth_clients` table grows, add a `last_used_at` column and a reaper.

## Consent and the tenant picker

The consent screen requires a browser session (the `sochub_session` cookie); a bearer token is refused. The user
picks one organization from their memberships and must click Approve; nothing is auto-approved on load.
A tenant is offered only if the resource's gate passes (for MCP: the tenant admin enabled it, see below) and the
current session may enter it under that tenant's login method. SSO-only tenants the session cannot enter are
listed separately with a hint to sign in through that organization's SSO first.

Approval stores a single-use code in Redis for 60 seconds. Denial redirects with `error=access_denied`. Both are
audited (`oauth.consent_granted`, `oauth.consent_denied`).

## Tokens

- **Access token**: JWT, 1 hour (`expires_in: 3600`). Claims: `iss` (PUBLIC_BASE_URL), `aud` (the resource URL, e.g.
  `<base>/mcp`), `sub` (email), `tid` (tenant), `tv` (token version), `sid` (session id), `cid` (client id),
  `scope`, `purpose: "oauth"`, `iat`, `exp`. The REST API refuses `purpose: oauth` tokens, and a resource refuses
  web tokens.
- **Audience per resource**: `RESOURCES` in `resources.py` maps a key to a URL, scopes and a tenant gate. A token
  is valid only for the resource it was issued for.
- **Verification** (`verify_oauth_access_token`) re-checks live on every call: user active, `tv`, session not
  revoked and bound to the same tenant, client and resource, membership (the role comes from the membership,
  never the token), the tenant gate and the tenant's login policy.
- **Refresh token**: `<session id>.<secret>`; only an HMAC of the secret is stored. Every refresh rotates the
  secret. Idle expiry is 30 days since last use.
- **10 s race grace**: presenting the just-replaced secret within 10 seconds of the rotation is treated as a
  benign concurrent refresh. It returns a fresh access token but **no new refresh token** (the client keeps the
  winner's secret), and the window cannot be extended by replaying. Reuse of that old secret inside the window
  is therefore undetectable by design.
- **Reuse = revoke**: presenting any other non-current secret (or the old one after 10 s) revokes the whole session
  and writes a `session.refresh_reuse` security audit event.
- A refresh token presented by a different client than the one it was issued to is refused before it rotates.

### The `sid` caveat

Access tokens are not secret from the user's own tools, and `sid` is visible in them. Knowing a session id (without
the secret) lets someone sign that session out through logout, which accepts an access token's signature even when
expired; it grants no access. The signature must still be valid, so this requires a genuine token.

## Web sessions and cookies

Every member sign-in creates an `auth_sessions` row; the access token carries its `sid`.

- `sochub_session`: access cookie, HttpOnly, SameSite=Strict, path `/api`, lifetime of one access token (1 hour).
- `sochub_refresh`: refresh cookie, HttpOnly, SameSite=Strict, `Secure` when `PUBLIC_BASE_URL` is https, 30 days,
  **path `/api/v1/auth`**. The browser therefore sends it to *every* `/api/v1/auth/*` request (refresh, logout,
  login, ...), not just refresh. Never log cookies on those routes.
- The SPA sends `X-Requested-With` on every call; a cookie-authenticated unsafe request without it is refused.
  API clients using `Authorization: Bearer` get tokens in the response body instead.
- Logout works without the auth dependencies: a refresh cookie or an (even expired) access token proving the
  session is enough, so a stale session can always be ended.
- Changing password or MFA re-keys the current session (old refresh secret gets no grace) and revokes all of the
  user's other sessions, web and OAuth.

**Everyone signs in once after the deploy that introduced sessions**: access tokens without a `sid` claim are
refused.

## Sessions and connected apps

- `GET /api/v1/users/me/sessions` lists the user's live sessions (web and OAuth, with client name, tenant, method,
  IP, user agent, last used) and marks the current one. `DELETE /api/v1/users/me/sessions/{id}` revokes one.
- Tenant admins: `GET /api/v1/tenants/current/oauth-sessions` lists the active tenant's connected apps;
  `DELETE /api/v1/tenants/current/oauth-sessions/{id}` revokes one (`session.revoked_by_admin`). Admins can only
  touch OAuth sessions in their own tenant.
- "Last used" for an OAuth app updates when it **refreshes** (about once per hour), not on every API call.
- **MCP toggle**: `tenants.mcp_enabled` (default off) is changed by tenant admins in the security settings
  (`PUT` on the security settings in `mfa.py`; audited as `tenant.mcp_toggled`). While off, the tenant is not
  offered at consent and existing OAuth tokens for it stop working at the next check (refresh also revokes).

## Audit coverage

The audit middleware records every write request and every 401/403 with the matched route template, so all
`/api/v1/oauth/*` POSTs and the session routes are recorded like any other API route. Reads (such as the
`/.well-known/*` documents) are recorded only when a handler opts in, so they produce no noise. Explicit security
events: `oauth.client_registered`, `oauth.consent_granted`, `oauth.consent_denied`, `oauth.token_issued`,
`oauth.token_revoked`, `session.revoked`, `session.revoked_by_admin`, `session.refresh_reuse`.
See [Audit log](../audit/README.md).

## Extending

- **New integrations**: add an entry to `RESOURCES` (key, URL, scopes, tenant gate). Metadata scopes, consent,
  audience and verification pick it up; call `verify_oauth_access_token(db, token, resource=<key>)` in the new
  resource.
- **Cross-app access (XAA) extension point**: add a `urn:ietf:params:oauth:grant-type:jwt-bearer` grant on
  `/api/v1/oauth/token` that validates an assertion from a per-tenant trusted issuer, then calls the same
  `create_session(kind="oauth", ...)` path. Nothing else in the session model needs to change. Not built yet.
