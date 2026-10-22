# Slack setup (bring your own app)

1. Go to https://api.slack.com/apps, click **Create New App**, then **From a manifest**. Paste `slack-app-manifest.yml` from this folder, replacing `YOUR-SOCHUB-HOST` (3 places) with your sochub host.
   Already have the app? Open **App Manifest**, paste the new manifest, **Save**, then **Reinstall to Workspace** so the new scopes (`commands`, `channels:manage`) apply.
2. Click **Install to Workspace**.
3. Copy the **Bot User OAuth Token** (`xoxb-...`, under OAuth & Permissions) and the **Signing Secret** (under Basic Information).
4. In sochub, open **Integrations > Slack**. Paste both values and a default channel (for example `#soc-alerts`), click **Save**, then **Test**. A message should appear in the channel. An admin must click **Test** at least once: it stores the workspace `team_id`, and until then every Slack request (buttons, commands, events) is rejected with 401.
5. Optional: set a **responder alerts channel** and the **minimum severity** to page on (see below).
6. Run `/invite @sochub` in every channel that workflows or responder alerts post to.
7. Local development: run `ngrok http 80` and use `https://<ngrok-id>.ngrok.app` as the host in the manifest URLs.

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
| War room | **War room** on a card creates `#inc-0042-<title>`, invites the owner, followers and you, posts the card and records it on the case timeline. Status, severity and owner changes are posted there. |
| App Home | The app's **Home** tab shows your open cases with actions and a **New case** button. |

## Troubleshooting

| Symptom | Cause |
|---|---|
| `not_in_channel` | Invite the bot to the channel. |
| `users_not_found` | The email is not a member of the workspace. |
| `invalid_auth` | Wrong bot token. |
| `missing_scope` on war rooms or `/sochub` | The app was created from an older manifest: re-apply this one and reinstall. |
| `/sochub` says "dispatch_failed" | The commands URL is not reachable from Slack. |
| Buttons or commands do nothing | The URLs are not reachable, the signing secret is wrong (check backend logs for 401s), or **Test** was never clicked so `team_id` is not stored. |
| No responder alerts | Minimum severity is "off", the case is below it, or the bot is not in the alerts channel. |
