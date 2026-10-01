# Slack setup (bring your own app)

1. Go to https://api.slack.com/apps, click **Create New App**, then **From a manifest**. Paste `slack-app-manifest.yml` from this folder, replacing `YOUR-SOC-HUB-HOST` with your SOC Hub host.
2. Click **Install to Workspace**.
3. Copy the **Bot User OAuth Token** (`xoxb-...`, under OAuth & Permissions) and the **Signing Secret** (under Basic Information).
4. In SOC Hub, open **Integrations > Slack**. Paste both values and a default channel (for example `#soc-alerts`), click **Save**, then **Test**. A message should appear in the channel. An admin must click **Test** at least once: it stores the workspace `team_id`, and until then Slack button clicks are rejected with 401.
5. Run `/invite @SOC Hub` in every channel that workflows post to.
6. Local development: run `ngrok http 80` and set the app's interactivity URL to `https://<ngrok-id>.ngrok.app/api/v1/slack/interactions`.
7. Troubleshooting:

| Symptom | Cause |
|---|---|
| `not_in_channel` | Invite the bot to the channel. |
| `users_not_found` | The email is not a member of the workspace. |
| `invalid_auth` | Wrong bot token. |
| Buttons do nothing | The interactivity URL is not reachable, or the signing secret is wrong (check backend logs for 401s), or **Test** was never clicked so `team_id` is not stored (401). |
