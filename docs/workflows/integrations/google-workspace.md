# Google Workspace integration

Workflow templates for Google Workspace: pull Alert Center alerts into the alert queue, and contain a user from a
case. Install them from Automations > Templates (admins). Each install creates a **disabled** workflow; add the
listed secret, review the graph, then enable it. Node and template-context details are in
[the workflows README](../README.md).

| Key | Kind | Trigger | What it does |
|---|---|---|---|
| `google-workspace-alerts-pull` | ingest | every 5 min | Alert Center `v1beta1/alerts` → alerts (source `google-workspace`) |
| `google-workspace-suspend-user` | response | manual | signs the case's user out of Google; suspends the account when the case is tagged `gws-suspend` |

Both use `http_request.auth` type `google_service_account`: the node signs a JWT with the service-account key from
a tenant secret, impersonates `ADMIN_EMAIL` (domain-wide delegation) and exchanges it at the fixed URL
`https://oauth2.googleapis.com/token`. The access token is cached per worker until shortly before it expires; the
key, the JWT and the token never reach step output, errors or logs.

## Google Cloud and Admin console setup

Do this once per service account. Use **two** service accounts (see [why](#why-two-service-accounts)).

1. **Enable the API** in a Google Cloud project (APIs & Services > Library): *Google Workspace Alert Center API*
   for the pull, *Admin SDK API* for containment.
2. **Create the service account**: IAM & Admin > Service accounts > Create service account. It needs no Google Cloud
   project roles.
3. **Create a key**: open the service account > Keys > Add key > Create new key > **JSON**. The downloaded file is
   the secret value. Organisation policy `iam.disableServiceAccountKeyCreation` blocks this step; an org admin has
   to exempt the project.
4. **Copy the client ID**: the numeric *Unique ID* / *OAuth 2 Client ID* on the service account's Details page.
5. **Domain-wide delegation** (Admin console, super admin): Security > Access and data control > API controls >
   **Manage Domain Wide Delegation** > Add new. Paste the client ID and the comma-separated scopes below, then
   Authorize. Google says changes can take up to 24 hours.

   | Template | OAuth scopes to authorise |
   |---|---|
   | `google-workspace-alerts-pull` | `https://www.googleapis.com/auth/apps.alerts` |
   | `google-workspace-suspend-user` | `https://www.googleapis.com/auth/admin.directory.user.security,https://www.googleapis.com/auth/admin.directory.user` |

   The containment template asks for one scope per call (sign-out: `admin.directory.user.security`; suspend:
   `admin.directory.user`). If you never suspend, authorise only the first.
6. **Pick the admin to impersonate** (`ADMIN_EMAIL`). The service account acts with that admin's privileges, so the
   API call fails with 403 if the admin lacks them:
   - Alert pull: Super Admin or Services Admin have full Alert Center access. For least privilege, create a custom
     role (Account > Admin roles) with **Alert Center: View access** only. Reporting-rule alerts also need the
     Reports privilege and DLP alerts a DLP privilege (View DLP rule), otherwise those alerts are not returned.
   - Containment: Super Admin, or a custom role with **Users: Read**, **Users: Update > Suspend users** and
     **Security > User Security Management** (sign-in cookie reset). A delegated admin cannot act on users with
     higher privileges, such as super admins.

### Why two service accounts

`apps.alerts` only reaches Alert Center. `admin.directory.user` can change any user (password, admin flag,
suspension). With a separate service account and secret (`GWS_RESPONSE_SA_KEY`) for containment, a leaked or
over-shared pull key cannot sign out or suspend anyone, and the containment delegation can be revoked on its own.
Use separate impersonated admins too, each with the custom role above.

## Secrets and hosts

The access token is bound to the hosts of the secret that produced it, so each key must allow the token host
**and** the API host (Settings > Secrets):

| Secret | Value | Allowed hosts |
|---|---|---|
| `GWS_SA_KEY` | the whole JSON key file of the alert-reader service account | `oauth2.googleapis.com`, `alertcenter.googleapis.com` |
| `GWS_RESPONSE_SA_KEY` | the whole JSON key file of the containment service account | `oauth2.googleapis.com`, `admin.googleapis.com` |

The key's own `token_uri` is ignored. Rotate keys in the Cloud console (Keys tab) and update the secret.

## Params

| Param | Templates | Value |
|---|---|---|
| `ADMIN_EMAIL` | both | email of the admin to impersonate (`subject`). Must be an email address. |

`customerId` is not sent: Alert Center infers it from the impersonated admin.

## `google-workspace-alerts-pull`

### What is pulled

`GET https://alertcenter.googleapis.com/v1beta1/alerts?filter=createTime >= "<since>"&orderBy=createTime desc&pageSize=100`
(the filter is URL-encoded; timestamps are quoted because they contain colons). `<since>` is `{{ trigger.since }}`,
the fire time of the last successful run (at most 24 h back, one hour back on the first run).

Graph: `schedule` (`*/5 * * * *`) → `pull` → `ok` (`status == 200`) → `for_each` over `body.alerts` (the key is
absent when nothing matched) → `alert_create`. The false branch goes to `fail` with
`Google Workspace alerts pull failed: HTTP <status>`; the response body is never put in the message.

Each Alert Center alert becomes an alert with:

- `source`: `google-workspace`
- `external_id`: `alertId` (stable, so overlapping windows create one alert and fire `alert.ingested` once)
- `title`: `type`, plus ` (<data.email>)` when the alert data names a user (AccountWarning types such as Leaked
  password and Suspicious login, Government attack warning, Device compromised, Suspicious device activity)
- `payload`: the whole alert, including `source`, `data` (with its `@type`), `metadata`, `startTime`,
  `securityInvestigationToolLink`

### Severity mapping

The Alert resource has no severity of its own; `metadata.severity` is HIGH/MEDIUM/LOW or empty. The template maps by
`type` first and falls back to `metadata.severity`, then `medium`:

| Severity | Alert types |
|---|---|
| critical | Potential ransomware detected |
| high | Government attack warning, Leaked password, Device compromised, Suspicious programmatic login, User suspended (suspicious activity), User granted Admin privilege, Primary admin changed, Super admin password reset, SSO profile added / updated / deleted, Customer takeout initiated, Malware reclassification, Phishing in inboxes due to bad whitelist |
| medium | Suspicious login, Suspicious login (less secure app), Suspicious device activity, User reported phishing, Phishing reclassification, Suspicious message reported |
| low | User reported spam spike, User suspended (spam), User suspended (spam through relay), User suspended, Account suspension warning, New user Added, Suspended user made active, User deleted, Users Admin privilege revoked, Users password changed, User suspended (Administrator email alert), Calendar / Drive / Email / Mobile settings changed, Access Approvals request |
| info | Google Operations, Apps outage, MSA Billing / Legal / Product / Security, APNS certificate is expiring soon / has expired, Configuration problem, AppMaker Default Cloud SQL setup, Client-side encryption service unavailable, File syncing resumed |
| `metadata.severity` | anything else, e.g. Activity Rule, Reporting Rule, Data Loss Prevention, new types |

Edit the dictionary in the `create` step's `severity` field to change it. Pair it with the generic triage template
(`generic-alerts-to-case`) to open cases for high and critical alerts.

### Limits and ceilings

- ponytail: **one page per run**: `pageSize=100`, newest first, `nextPageToken` not followed. More than 100 new
  alerts in one window keeps the newest 100. Raise `pageSize` and the loop's `max_items` (hard cap 500) together.
- The window is on `createTime`. An alert that is updated later (status, severity) is not re-read.
- Alerts marked for deletion are not filtered out (the list filter does not support `deleted`; it is in the payload).
- A non-200 answer creates nothing and fails the run; the next run re-reads from the last successful run (24 h cap),
  so windows longer than 24 h of outage are lost. Network errors and 5xx are retried twice.
- Alert Center API quotas are per Google Cloud project (Cloud console > APIs & Services > Quotas); a 5-minute
  schedule is one request per run plus a token exchange about once an hour.

## `google-workspace-suspend-user`

Target: the case's `username` attribute, else its first `email` artifact, used as the Directory API `userKey`
(primary or alias email). With no target the run only writes a note.

1. `POST https://admin.googleapis.com/admin/directory/v1/users/<email>/signOut` (empty body; 2xx = done). Signs the
   user out of web and device sessions and resets sign-in cookies. It does not change the password: reset it too
   when the credentials are compromised, and revoke OAuth tokens / app passwords in the Admin console.
2. Only when the case has the tag `gws-suspend`: `PUT https://admin.googleapis.com/admin/directory/v1/users/<email>`
   with `{"suspended": true}`. `users.update` keeps fields that are not sent (patch semantics) and Google recommends
   it over `users.patch`. Undo with `{"suspended": false}` or in the Admin console.
3. A case note: `Google Workspace containment for <email>: signOut HTTP <status>, suspend: <status | not requested>.`

A 4xx from Google (unknown user, admin lacks the privilege, user is a super admin) does not stop the run; the status
is in the note and in the step output.

## Docs

- [Alert Center alerts.list](https://developers.google.com/workspace/admin/alertcenter/reference/rest/v1beta1/alerts/list)
- [Alert resource and AlertMetadata](https://developers.google.com/workspace/admin/alertcenter/reference/rest/v1beta1/alerts)
- [Alert Center query filters](https://developers.google.com/workspace/admin/alertcenter/guides/query-filters) and [filter fields](https://developers.google.com/workspace/admin/alertcenter/reference/filter-fields)
- [Alert types](https://developers.google.com/workspace/admin/alertcenter/reference/alert-types)
- [Alert Center authorization](https://developers.google.com/workspace/admin/alertcenter/guides/auth)
- [Grant access to the alert center](https://support.google.com/a/answer/9176596)
- [Directory API users.signOut](https://developers.google.com/workspace/admin/directory/reference/rest/v1/users/signOut)
- [Directory API users.update](https://developers.google.com/workspace/admin/directory/reference/rest/v1/users/update)
- [Domain-wide delegation](https://developers.google.com/workspace/admin/directory/v1/guides/delegation)
- [Sign a user out of a managed Google Account](https://support.google.com/a/answer/178854)
- [Administrator privilege definitions](https://support.google.com/a/answer/1219251)
