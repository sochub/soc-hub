# Integration templates

Ready-made workflows that pull security alerts and events from SaaS and cloud tools on a schedule, turn them into cases, and run response actions. They ship with sochub: any instance on this version lists them under **Workflows › Templates**.

## How to use them

1. **Workflows › Templates**, pick a template and press **Install**. Fill in the parameters (domain, account id, region…). The workflow is created **disabled**.
2. Create the secrets the dialog lists in **Settings › Integrations › Secrets**, each restricted to the hosts shown. Credentials never go into the workflow itself.
3. Open the workflow, run a **Dry run**, then enable it.
4. Install a **triage** template too, so the alerts the pulls create are grouped into cases. Use one triage template per source: two triage workflows matching the same alert make the second one fail.
5. New playbooks used by the triage templates: run `python -m app.scripts.seed_playbooks` in the backend container (safe to repeat), then import them from **Playbooks › Marketplace** and pass the imported playbook's id as `PLAYBOOK_ID`.

How pulls behave: each run reads from the last **successful** run (at most 24 h back), creates one alert per vendor event (deduplicated by the vendor id, so re-read windows never duplicate), and fails visibly on 401/403/429 so the next run re-reads the window. Each run reads one page; the per-template limits are in the vendor guides.

## Catalog

| Vendor | Kind | Template | Secrets | Parameters |
|---|---|---|---|---|
| AWS | ingest | `aws-guardduty-findings-pull`: AWS GuardDuty: pull findings | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY | AWS_REGION, DETECTOR_ID |
| AWS | ingest | `aws-securityhub-findings-pull`: AWS Security Hub: pull findings | AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY | AWS_REGION |
| Cloudflare | ingest | `cloudflare-audit-logs-pull`: Cloudflare: pull security-relevant audit logs | CLOUDFLARE_API_TOKEN | ACCOUNT_ID |
| Cloudflare | ingest | `cloudflare-security-insights-pull`: Cloudflare: pull Security Center insights | CLOUDFLARE_API_TOKEN | ACCOUNT_ID |
| Cloudflare | ingest | `cloudflare-waf-top-ips-pull`: Cloudflare: WAF top blocked client IPs | CLOUDFLARE_API_TOKEN | ZONE_ID, MIN_EVENTS |
| Cloudflare | response | `cloudflare-block-ip`: Cloudflare: block IP for the account | CLOUDFLARE_API_TOKEN | ACCOUNT_ID |
| CrowdStrike | ingest | `crowdstrike-alerts-pull`: CrowdStrike Falcon: pull alerts | FALCON_CLIENT_ID, FALCON_CLIENT_SECRET | FALCON_API_HOST |
| CrowdStrike | response | `crowdstrike-contain-host`: CrowdStrike Falcon: contain host | FALCON_CLIENT_ID, FALCON_CLIENT_SECRET | FALCON_API_HOST |
| EDR | triage | `edr-alerts-to-case`: EDR alerts to case (CrowdStrike, SentinelOne) | - | PLAYBOOK_ID |
| Generic | triage | `generic-alerts-to-case`: High and critical alerts to case (any source) | - | - |
| GitHub | ingest | `github-security-alerts-pull`: GitHub: pull secret scanning and Dependabot alerts | GITHUB_TOKEN | GITHUB_ORG |
| Google | ingest | `google-workspace-alerts-pull`: Google Workspace: pull Alert Center alerts | GWS_SA_KEY | ADMIN_EMAIL |
| Google | response | `google-workspace-suspend-user`: Google Workspace: sign out (and optionally suspend) user | GWS_RESPONSE_SA_KEY | ADMIN_EMAIL |
| Identity | triage | `identity-alerts-to-case`: Identity alerts: group by user into a case | - | PLAYBOOK_ID |
| Microsoft | ingest | `entra-risk-detections-pull`: Entra ID Protection: pull risk detections | MS_CLIENT_ID, MS_CLIENT_SECRET | AZURE_TENANT_ID |
| Microsoft | ingest | `m365-defender-alerts-pull`: Microsoft 365 Defender: pull alerts | MS_CLIENT_ID, MS_CLIENT_SECRET | AZURE_TENANT_ID |
| Microsoft | ingest | `sentinel-incidents-pull`: Microsoft Sentinel: pull incidents | SENTINEL_CLIENT_ID, SENTINEL_CLIENT_SECRET | AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, RESOURCE_GROUP, WORKSPACE_NAME |
| Microsoft | response | `entra-revoke-sessions`: Entra ID: revoke sign-in sessions (and optionally disable) | ENTRA_RESPONSE_CLIENT_ID, ENTRA_RESPONSE_CLIENT_SECRET | AZURE_TENANT_ID |
| Okta | ingest | `okta-system-log-pull`: Okta: pull security events | OKTA_API_TOKEN | OKTA_DOMAIN |
| Okta | response | `okta-contain-user`: Okta: suspend user and revoke sessions | OKTA_RESPONSE_API_TOKEN | OKTA_DOMAIN |
| Proofpoint | ingest | `proofpoint-tap-pull`: Proofpoint TAP: pull delivered threats and permitted clicks | PROOFPOINT_PRINCIPAL, PROOFPOINT_SECRET | - |
| SentinelOne | ingest | `sentinelone-threats-pull`: SentinelOne: pull threats | S1_API_TOKEN | S1_CONSOLE |
| SentinelOne | response | `sentinelone-disconnect-agent`: SentinelOne: disconnect agent from network | S1_API_TOKEN | S1_CONSOLE |
| Wiz | ingest | `wiz-issues-pull`: Wiz: pull issues | WIZ_CLIENT_ID, WIZ_CLIENT_SECRET | WIZ_API_HOST |

## Vendor guides

- [Identity and Microsoft 365](identity.md): Okta, Microsoft 365 Defender, Entra ID
- [EDR](edr.md): CrowdStrike Falcon, SentinelOne
- [Cloud and SaaS](cloud-saas.md): AWS GuardDuty, AWS Security Hub, Microsoft Sentinel, Wiz, Proofpoint TAP, GitHub
- [Google Workspace](google-workspace.md)
- [Cloudflare](cloudflare.md)

All templates were checked with dry runs against response samples from each vendor's documentation, not against live tenants. Each template's setup text lists anything to double-check before enabling.
