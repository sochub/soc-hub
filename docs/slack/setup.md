# Slack setup (bring your own app, Socket Mode)

sochub uses Slack **Socket Mode**: the `slack-socket` service opens an outbound WebSocket to Slack, and
commands, button clicks, forms and App Home events arrive over it. No public URL, ngrok or inbound firewall
rule is needed. Messages sochub sends (cards, alerts, DMs) are normal HTTPS calls to the Slack API.

1. Go to https://api.slack.com/apps, click **Create New App**, then **From a manifest**, and paste `slack-app-manifest.yml` from this folder.
   Already have the app? Open **App Manifest**, paste the new manifest, **Save**, then **Reinstall to Workspace**.
   **Reinstall the app after updating the manifest:** new scopes only take effect after a reinstall. War rooms opened from the case page and SOC-bot listening need `groups:write`, `groups:read`, `groups:history`, `channels:history` and the `message.groups` / `message.channels` events, so existing tenants must update and reinstall once. If you reinstall, copy the new bot token into sochub if it changed.
2. Click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`, under OAuth & Permissions).
3. Under **Basic Information > App-Level Tokens**, click **Generate Token and Scopes**, add the scope `connections:write`, and copy the `xapp-...` token.
4. In sochub, open **Integrations > Slack**. Paste the bot token and the app-level token, a default channel (for example `#soc-alerts`), click **Save**, then **Test**. Test stores the workspace `team_id`, checks both tokens and posts a message in the channel. Within ~30 seconds the card shows **socket connected**.
5. Optional: set a **responder alerts channel** and the **minimum severity** to page on (see below).
6. Run `/invite @sochub` in every channel that workflows or responder alerts post to.

The `slack-socket` service is part of both compose files. One replica handles every tenant (a second one for redundancy is fine: Slack allows up to 10 connections per app and delivers each event to only one), and it
reconnects by itself. Adding, rotating or removing a token in the UI takes effect within 30 seconds.

### HTTP mode (alternative)

If you'd rather have Slack call sochub over HTTPS, leave the app-level token empty and paste the
**Signing Secret** (Basic Information) instead. In the manifest set `socket_mode_enabled: false` and add
request URLs: `request_url: https://YOUR-SOCHUB-HOST/api/v1/slack/interactions` under `interactivity`,
`.../api/v1/slack/events` under `event_subscriptions`, and `url: .../api/v1/slack/commands` on the slash command.
Every HTTP request is verified with the signing secret.

## What you can do in Slack

Everyone acting from Slack must be **linked**: the email on their Slack profile must belong to an active
analyst or admin of the tenant the workspace is connected to. Unlinked users get a short explanation and nothing happens.

| Feature | How |
|---|---|
| Create a case | `/sochub new [title]` opens a form (title, severity, description, assign to me). The **Create sochub case** message shortcut (⋯ menu) prefills it from the message and links back to it. |
| Look up a case | `/sochub case 42` shows the case card, only to you. `/sochub mine` lists your open cases. |
| Case card actions | **Acknowledge**, **Assign to me**, **Add note** (form, Markdown), **Severity** (form), **War room**, **Close**, **Open in sochub**. The card redraws itself after each action. |
| Responder alerts | Cases at or above the configured severity are posted to the alerts channel as a live card when created, escalated into that severity, or when an SLA is breached. Follow-ups thread under the card, and the card is updated in place as the case changes. |
| Direct messages | Mentions, assignments and SLA breaches arrive as DMs with the case card (email fallback). The owner is also DMed when their case's severity goes up. |
| War room | Private channel for a case, opened with **War room** on a Slack card or **Open war room** on the case page. The name follows the tenant's naming convention (**Settings → Slack**, default `inc-{id}-{title}`; placeholders `{id}`, `{title}`, `{date}`) and can be edited when opening. The owner, followers and the opener are invited, the case card is posted and the opening is recorded on the case timeline. Cards in the room update on status, severity and owner changes, SLA breaches, task completion, workflow completion (with a link to the run) and containment confirmed. |
| SOC-bot listening | Optional per war room (toggle when opening, or later on the case page). SOC-bot reads the channel and suggests indicators (IPs, domains, hashes, hostnames, usernames) and incident milestone times (first seen, detected, contained, recovered) in a thread reply with **Add** / **Dismiss** buttons. The same suggestions appear on the case page. Nothing is added until an analyst confirms. |
| App Home | The app's **Home** tab shows your open cases with actions and a **New case** button. |

## Troubleshooting

| Symptom | Cause |
|---|---|
| `not_in_channel` | Invite the bot to the channel. |
| `restricted_action` | Workspace settings block the bot from creating channels or inviting people: ask a workspace admin. |
| `users_not_found` | The email is not a member of the workspace. |
| `invalid_auth` | Wrong bot token. |
| `missing_scope` on war rooms or `/sochub` | The app was created from an older manifest: re-apply this one and reinstall the app to the workspace. War rooms need `groups:write`. |
| `/sochub` says "dispatch_failed" | Socket Mode: the `slack-socket` service is down. HTTP mode: the commands URL is not reachable. |
| Card says **socket not connected** | The `slack-socket` service isn't running, or the app-level token is wrong/revoked (`docker compose logs slack-socket`). |
| Buttons or commands do nothing | Socket Mode: see above. HTTP mode: the URLs are not reachable, the signing secret is wrong (check backend logs for 401s), or **Test** was never clicked so `team_id` is not stored. |
| No responder alerts | Minimum severity is "off", the case is below it, or the bot is not in the alerts channel. |
