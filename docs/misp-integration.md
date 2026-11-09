# MISP integration

Each organization can connect its own MISP server to look up indicators and to push case IOCs as a MISP event.

## Configuration

**Settings -> Integrations -> Threat intelligence** (admins):

- **MISP server URL**, `http://` or `https://`, no embedded credentials.
- **MISP auth key.** Write-only, stored encrypted, scrubbed from errors and logs.
- **Verify TLS certificate.** On by default. Turning it off shows a warning; use it only for trusted internal
  servers, since the connection is then exposed to interception.

The server URL must not resolve to a private or other non-global address. For a self-hosted MISP on an internal
network the platform operator adds the exact hostname to `MISP_PRIVATE_HOSTS` (a JSON list, for example
`["misp.corp.internal"]`). Redirects are not followed.

## Enrichment lookups

MISP is one of the enrichment sources, enabled with the other sources in the same card. It looks up IPs, domains,
URLs and file hashes with `attributes/restSearch` (up to 50 attributes) and shows:

- **Not found**, or the number of matching attributes.
- Verdict **suspicious** if any matching attribute is flagged `to_ids`, otherwise unknown.
- Up to 5 matching events (id and title) and their tags, TLP tags first.
- A link to the first event on your MISP server.

The platform's usual TLP rules for sending indicators to external sources apply.

## Pushing IOCs

In the IOC list select IOCs from **one case** and choose **Send to MISP** (analysts and above).

- **One event per case.** The first push creates an event titled `SOC Hub case #<id>: <title>` and remembers it
  as a link on the case; later pushes add attributes to the same event.
- The event is created with distribution **your organisation only**, so it is not shared further until someone
  changes it in MISP. Attributes are added with `to_ids` on.
- **TLP:RED IOCs are never sent** (reported as skipped).
- Types that map: IP address, domain, URL, email, registry key, mutex, user agent, and file hashes by length
  (MD5, SHA-1, SHA-256). Other types and other hash lengths are **skipped and reported** with a reason. Attributes
  MISP already holds are counted as pushed.
- If MISP fails partway, the response is an error that says how many were pushed before it; the event link is
  kept, so a retry continues on the same event.
- Every push is audited on the case (`misp_push`): counts and event UUID, or `partial` with counts when it failed
  midway.
