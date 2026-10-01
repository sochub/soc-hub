# Threat-Intel Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an analyst opens an IOC or artifact, show verdicts from VirusTotal, URLhaus, ThreatFox, RDAP and crt.sh. For IOCs, also suggest a threat level and tags the analyst can apply with one click.

**Architecture:**
- Pure modules handle normalisation, gating and suggestions (`app/enrichment/indicators.py`, `suggest.py`).
- Source modules sit behind one `Source` interface (`app/enrichment/sources/`).
- A Redis fixed-window rate limiter caps calls per source.
- A Celery task, `enrich_indicator`, runs the lookups. An SQLAlchemy session hook enqueues it after any commit that creates an IOC or artifact, or changes its value, type or TLP.
- Results are cached in `enrichment_results`.
- Per-tenant settings and keys live in `tenant_enrichment_configs`. The keys are Fernet-encrypted.
- The admin config API and the result/run API live in `app/api/v1/enrichment.py`.
- The React UI is a Threat-intel card on Integrations, plus an enrichment panel on IOCs and on case artifacts.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Celery, redis.asyncio, httpx; React 19, TanStack Query, Tailwind.

**Spec:** `docs/superpowers/specs/2026-10-01-threat-intel-enrichment-design.md`

## Global Constraints

- Indicator types: `ip`, `domain`, `url`, `file_hash`. IOC `ip_address` maps to `ip`. Every other type is not enrichable.
- Sources: `virustotal`, `urlhaus`, `threatfox`, `rdap`, `crtsh`.
  - Keyed sources: VT uses key `virustotal_api_key`. urlhaus and threatfox share key `abusech_auth_key`.
- Result statuses: `pending`, `ok`, `not_found`, `error`, `rate_limited`, `skipped`.
- Verdicts: `malicious`, `suspicious`, `harmless`, `unknown`.
- TLP order is `white` < `green` < `amber` < `red`. `auto_max_tlp` additionally allows `none`, which never matches.
- Defaults:

  | Setting | Default |
  |---|---|
  | `auto_max_tlp` | `green` |
  | `artifact_tlp` | `amber` |
  | `cache_ttl_hours` | 24 (range 1–720) |
  | `vt_per_minute` | 4 (range 1–1000) |
  | `vt_per_day` | 500 (range 1–1,000,000) |
  | `sources` | all `true` |
  | `internal_domains` | `[]` |

- Rate limits:

  | Source | Limit |
  |---|---|
  | VT | `vt_per_minute`/60s **and** `vt_per_day`/86400s |
  | urlhaus | 60/60s |
  | threatfox | 60/60s |
  | rdap | 30/60s |
  | crtsh | 1/5s, global key |

- HTTP: timeout 15s, `follow_redirects=False`, fixed `https://` hosts.
  - RDAP is the one exception. It follows ≤3 redirects, each hop `https` and passing `app.workflows.ssrf.assert_url_allowed(url, [])`.
- Never log indicator values, keys or response bodies.
  - Each lookup logs exactly one line: `ti_lookup tenant=%s source=%s type=%s outcome=%s duration_ms=%d`.
  - Errors are passed through `app.ai.errors.scrub(text, secrets)`, which also caps them at 300 characters.
- Keys are write-only. No API response ever contains a key value; only `credentials_set` booleans.
- Cross-tenant access returns 404.
- Roles:
  - config endpoints: `require_admin`
  - result reads: `get_current_active_user`
  - run: `require_analyst_or_above`
- Migration revision `f4a5b6c7d8e9`, `down_revision = "e3f4a5b6c7d8"`.
- Tests:
  - Never touch the real network: use `httpx.MockTransport` and monkeypatch.
  - Tests that write the live dev DB delete only the exact row ids they created.
  - Redis test keys use a unique `ti:test:<hex>` prefix and are deleted afterwards.
- Run backend tests with `docker compose exec -T backend python -m pytest tests/<file> -q`. After backend changes: `docker restart case_management-backend-1 case_management-worker-1`.
- Commits end with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Use `perl -e 'alarm 60; exec @ARGV' git commit ...`. If signing times out, leave the changes staged and report it.

## Review Focus

1. **Odd but valid values.** Uppercase hashes, `HTTP://Example.COM/a#frag`, IPv6 (`2001:DB8::1`), and domains with a trailing dot must normalise to one canonical value. Otherwise the same indicator gets duplicate cache rows. → Task 1 tests.
2. **Garbage values.** Things like `not-a-hash`, `999.1.1.1` or an empty string on an `ip`/`file_hash` IOC must be "not enrichable" (422 on run, no task enqueued), not a 500 or a junk lookup. → Task 1 and Task 7 tests.
3. **Source returns HTML or invalid JSON.** This happens behind a captive portal, with a crt.sh 502, or with abuse.ch maintenance pages. The result must be `error` with a short message, and the other sources must still complete. → Tasks 3 and 4 (malformed body) and Task 6 (independence).
4. **IOC saved with TLP amber, then lowered to green.** The edit must auto-enqueue enrichment. Editing only the description must not. → Task 6 hook tests.
5. **Running the same IOC twice quickly.** Two clicks or two saves must produce one lookup per source, not two (dedupe lock). → Task 6.

---

## File structure

```
backend/app/enrichment/__init__.py          (empty)
backend/app/enrichment/indicators.py        normalise, enrichable, tlp_allows, should_skip     (T1)
backend/app/enrichment/suggest.py           suggestion rules                                    (T1)
backend/app/models/tenant_enrichment_config.py, enrichment_result.py                           (T2)
backend/alembic/versions/f4a5b6c7d8e9_threat_intel_enrichment.py                               (T2)
backend/app/enrichment/config.py            EnrichmentSettings, load_settings                    (T2)
backend/app/enrichment/sources/__init__.py  REGISTRY                                             (T3, T4)
backend/app/enrichment/sources/base.py      LookupResult, http_client, parse_json               (T3)
backend/app/enrichment/sources/virustotal.py, abusech.py                                         (T3)
backend/app/enrichment/sources/rdap.py, crtsh.py                                                 (T4)
backend/app/enrichment/ratelimit.py         RateLimiter                                          (T5)
backend/app/tasks/enrichment.py             enrich_indicator task, run_enrichment                (T6)
backend/app/enrichment/hooks.py             session after_flush/after_commit enqueue            (T6)
backend/tests/conftest.py                   autouse: disable real enqueue in tests              (T6)
backend/app/schemas/enrichment.py, backend/app/api/v1/enrichment.py                             (T7)
frontend/src/features/integrations/ThreatIntelCard.tsx                                           (T8)
frontend/src/features/enrichment/EnrichmentPanel.tsx                                             (T8)
docs/configuration.md, docs/features.md                                                          (T9)
```

---

### Task 1: Indicator normalisation, gating and suggestion rules (pure)

**Files:**
- Create: `backend/app/enrichment/__init__.py` (empty), `backend/app/enrichment/indicators.py`, `backend/app/enrichment/suggest.py`
- Test: `backend/tests/test_ti_indicators.py`, `backend/tests/test_ti_suggest.py`

**Interfaces:**
- Produces:
  - `normalise(raw_type: str, value: str) -> Optional[tuple[str, str]]`: returns `(itype, value)` or None when the value is not enrichable.
  - `tlp_allows(tlp: str, max_tlp: str) -> bool`
  - `should_skip(itype: str, value: str, internal_domains: list[str]) -> bool`
  - `TLP_ORDER`
  - `suggest(current_level: str, current_tags: list[str], results: list[dict]) -> Optional[dict]`: returns `{"threat_level": str|None, "tags": list[str]}` or None. Each result dict has keys `source, status, verdict, summary`.

- [ ] **Step 1: Write failing tests** `backend/tests/test_ti_indicators.py`:

```python
import pytest
from app.enrichment.indicators import normalise, tlp_allows, should_skip


@pytest.mark.parametrize("raw_type,value,expected", [
    ("ip_address", " 8.8.8.8 ", ("ip", "8.8.8.8")),
    ("ip", "2001:DB8:0:0::1", ("ip", "2001:db8::1")),
    ("domain", "Example.COM.", ("domain", "example.com")),
    ("url", "HTTP://Example.COM/a/B?x=1#frag", ("url", "http://example.com/a/B?x=1")),
    ("file_hash", "D41D8CD98F00B204E9800998ECF8427E", ("file_hash", "d41d8cd98f00b204e9800998ecf8427e")),
    ("file_hash", "a" * 64, ("file_hash", "a" * 64)),
])
def test_normalise_ok(raw_type, value, expected):
    assert normalise(raw_type, value) == expected


@pytest.mark.parametrize("raw_type,value", [
    ("ip_address", "999.1.1.1"), ("ip", ""), ("file_hash", "not-a-hash"), ("file_hash", "abc"),
    ("domain", "no spaces.com"), ("domain", ""), ("url", "notaurl"), ("url", "ftp://x.com/a"),
    ("email", "a@b.com"), ("registry_key", "HKLM\\x"), ("mutex", "m"), ("other", "x"),
])
def test_normalise_rejects(raw_type, value):
    assert normalise(raw_type, value) is None


def test_tlp_allows():
    assert tlp_allows("white", "green") and tlp_allows("green", "green")
    assert not tlp_allows("amber", "green") and not tlp_allows("red", "amber")
    assert not tlp_allows("white", "none")
    assert not tlp_allows("bogus", "red")


@pytest.mark.parametrize("itype,value", [
    ("ip", "10.1.2.3"), ("ip", "127.0.0.1"), ("ip", "169.254.1.1"), ("ip", "::1"), ("ip", "224.0.0.1"),
    ("ip", "0.0.0.0"), ("domain", "corp.local"), ("domain", "a.b.corp.local"), ("url", "https://x.corp.local/p"),
])
def test_should_skip(itype, value):
    assert should_skip(itype, value, ["corp.local"])


def test_should_not_skip_public():
    assert not should_skip("ip", "8.8.8.8", ["corp.local"])
    assert not should_skip("domain", "notcorp.local.evil.com", ["corp.local"])
    assert not should_skip("file_hash", "a" * 32, ["corp.local"])
```

`backend/tests/test_ti_suggest.py`:

```python
from app.enrichment.suggest import suggest


def r(source, verdict, summary=None, status="ok"):
    return {"source": source, "status": status, "verdict": verdict, "summary": summary or {}}


def test_critical_needs_vt15_and_abusech_hit():
    res = [r("virustotal", "malicious", {"malicious": 20}), r("threatfox", "malicious", {"malware_printable": "Emotet"})]
    assert suggest("medium", [], res) == {"threat_level": "critical", "tags": ["malware:emotet"]}


def test_high_on_any_malicious():
    assert suggest("low", [], [r("virustotal", "malicious", {"malicious": 5})])["threat_level"] == "high"


def test_medium_on_suspicious_only():
    assert suggest("low", [], [r("rdap", "suspicious")]) == {"threat_level": "medium", "tags": []}


def test_never_lowers_and_none_when_nothing_new():
    assert suggest("critical", [], [r("virustotal", "malicious", {"malicious": 5})]) is None
    assert suggest("high", ["malware:emotet"], [r("threatfox", "malicious", {"malware_printable": "Emotet"})]) is None


def test_tags_dedup_cap_and_order():
    res = [r("threatfox", "malicious", {"malware_printable": "QakBot"}),
           r("urlhaus", "malicious", {"threat": "malware_download"}),
           r("virustotal", "malicious", {"malicious": 3, "popular_threat_label": "Trojan.QakBot/Generic"})]
    out = suggest("high", ["urlhaus:malware_download"], res)
    assert out == {"threat_level": None, "tags": ["malware:qakbot", "trojan.qakbot/generic"]}


def test_ignores_non_ok_results():
    assert suggest("low", [], [r("virustotal", "malicious", {"malicious": 50}, status="error")]) is None
```

- [ ] **Step 2: Run.** Expect FAIL with `ModuleNotFoundError: app.enrichment`.
Run: `docker compose exec -T backend python -m pytest tests/test_ti_indicators.py tests/test_ti_suggest.py -q`

- [ ] **Step 3: Implement** `backend/app/enrichment/indicators.py`:

```python
"""Pure indicator helpers: normalisation, TLP gating and never-send-out rules."""
import ipaddress
import re
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

TLP_ORDER = ("white", "green", "amber", "red")
_TYPE_MAP = {"ip_address": "ip", "ip": "ip", "domain": "domain", "url": "url", "file_hash": "file_hash"}
_DOMAIN_RX = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$")
_HASH_RX = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$")


def _domain(v: str) -> Optional[str]:
    v = v.strip().lower().rstrip(".")
    return v if _DOMAIN_RX.match(v) else None


def normalise(raw_type: str, value: str) -> Optional[Tuple[str, str]]:
    itype = _TYPE_MAP.get((raw_type or "").lower())
    v = (value or "").strip()
    if not itype or not v:
        return None
    if itype == "ip":
        try:
            return itype, ipaddress.ip_address(v).compressed
        except ValueError:
            return None
    if itype == "domain":
        d = _domain(v)
        return (itype, d) if d else None
    if itype == "file_hash":
        h = v.lower()
        return (itype, h) if _HASH_RX.match(h) else None
    parts = urlsplit(v)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    netloc = parts.netloc.rsplit("@", 1)[-1].lower()
    return itype, urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))


def tlp_allows(tlp: str, max_tlp: str) -> bool:
    if tlp not in TLP_ORDER or max_tlp not in TLP_ORDER:
        return False
    return TLP_ORDER.index(tlp) <= TLP_ORDER.index(max_tlp)


def _under(host: str, internal_domains) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in (x.lower().strip(".") for x in internal_domains if x))


def should_skip(itype: str, value: str, internal_domains) -> bool:
    """True when the value must never leave the system (private/reserved IPs, internal domains)."""
    if itype == "ip":
        ip = ipaddress.ip_address(value)
        return not ip.is_global or ip.is_multicast
    if itype == "domain":
        return _under(value, internal_domains)
    if itype == "url":
        host = urlsplit(value).hostname or ""
        try:
            ip = ipaddress.ip_address(host)
            return not ip.is_global or ip.is_multicast
        except ValueError:
            return _under(host, internal_domains)
    return False
```

`backend/app/enrichment/suggest.py`:

```python
"""Suggestion rules: raise-only threat level + new tags from enrichment results."""
from typing import List, Optional

LEVELS = ("info", "low", "medium", "high", "critical")


def _rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else LEVELS.index("medium")


def suggest(current_level: str, current_tags: List[str], results: List[dict]) -> Optional[dict]:
    ok = [r for r in results if r.get("status") == "ok"]
    by = {r["source"]: r for r in ok}
    vt_mal = int((by.get("virustotal", {}).get("summary") or {}).get("malicious") or 0)
    abuse_hit = any(by.get(s, {}).get("verdict") == "malicious" for s in ("urlhaus", "threatfox"))
    verdicts = {r.get("verdict") for r in ok}
    level = None
    if vt_mal >= 15 and abuse_hit:
        level = "critical"
    elif "malicious" in verdicts:
        level = "high"
    elif "suspicious" in verdicts:
        level = "medium"
    if level and _rank(level) <= _rank(current_level):
        level = None

    have = {t.lower() for t in current_tags or []}
    candidates = []
    tf = (by.get("threatfox", {}).get("summary") or {}).get("malware_printable")
    if tf:
        candidates.append(f"malware:{tf.lower()}")
    uh = (by.get("urlhaus", {}).get("summary") or {}).get("threat")
    if uh:
        candidates.append(f"urlhaus:{uh.lower()}")
    vt_label = (by.get("virustotal", {}).get("summary") or {}).get("popular_threat_label")
    if vt_label:
        candidates.append(vt_label.lower())
    tags = []
    for t in candidates:
        if t not in have and t not in tags:
            tags.append(t)
    tags = tags[:5]
    if not level and not tags:
        return None
    return {"threat_level": level, "tags": tags}
```

- [ ] **Step 4: Run.** Expect PASS. Same command as Step 2.

- [ ] **Step 5: Commit**

```bash
git add backend/app/enrichment backend/tests/test_ti_indicators.py backend/tests/test_ti_suggest.py
git commit -m "feat(ti): indicator normalisation, TLP gating and suggestion rules"
```

---

### Task 2: Models, migration and tenant settings loader

**Files:**
- Create: `backend/app/models/tenant_enrichment_config.py`, `backend/app/models/enrichment_result.py`, `backend/alembic/versions/f4a5b6c7d8e9_threat_intel_enrichment.py`, `backend/app/enrichment/config.py`
- Modify: `backend/app/db/base.py`. Import the two models the same way `tenant_ai_config` is imported; find that line with `grep -n tenant_ai_config backend/app/db/base.py`.
- Test: `backend/tests/test_ti_config.py`

**Interfaces:**
- Consumes: `app.utils.crypto.encrypt/decrypt`, `app.db.session.AsyncSessionLocal`.
- Produces:
  - Models `TenantEnrichmentConfig`, `EnrichmentResult`, with the columns listed in the spec.
  - `DEFAULTS: dict`
  - `SOURCES = ("virustotal", "urlhaus", "threatfox", "rdap", "crtsh")`
  - `SOURCE_KEY = {"virustotal": "virustotal_api_key", "urlhaus": "abusech_auth_key", "threatfox": "abusech_auth_key"}`
  - `@dataclass EnrichmentSettings(auto_max_tlp, artifact_tlp, cache_ttl_hours, sources: dict, vt_per_minute, vt_per_day, internal_domains: list, keys: dict, key_error: bool)`
  - `async def load_settings(db, tenant_id: int) -> EnrichmentSettings`
  - `def runnable_sources(s: EnrichmentSettings) -> list[str]`: returns the sources that are enabled and, for keyed sources, have their key set.

- [ ] **Step 1: Models.**

`backend/app/models/tenant_enrichment_config.py`:

```python
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base_class import Base


class TenantEnrichmentConfig(Base):
    __tablename__ = "tenant_enrichment_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    auto_max_tlp = Column(String, nullable=False, server_default="green")
    artifact_tlp = Column(String, nullable=False, server_default="amber")
    cache_ttl_hours = Column(Integer, nullable=False, server_default="24")
    sources = Column(JSON, nullable=True)
    vt_per_minute = Column(Integer, nullable=False, server_default="4")
    vt_per_day = Column(Integer, nullable=False, server_default="500")
    internal_domains = Column(JSON, nullable=True)
    credentials_enc = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
```

`backend/app/models/enrichment_result.py`:

```python
from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.db.base_class import Base


class EnrichmentResult(Base):
    __tablename__ = "enrichment_results"
    __table_args__ = (UniqueConstraint("tenant_id", "indicator_type", "indicator_value", "source",
                                       name="uq_enrichment_result"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    indicator_type = Column(String, nullable=False)
    indicator_value = Column(String, nullable=False)
    source = Column(String, nullable=False)
    status = Column(String, nullable=False)
    verdict = Column(String, nullable=True)
    score = Column(String, nullable=True)
    summary = Column(JSON, nullable=True)
    link = Column(String, nullable=True)
    error = Column(String, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
```

`updated_at` is set explicitly on every upsert. The UI uses it to detect `pending` rows that have stalled for more than 10 minutes.

- [ ] **Step 2: Migration** `backend/alembic/versions/f4a5b6c7d8e9_threat_intel_enrichment.py`:

```python
"""threat intel enrichment tables

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""
import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_enrichment_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("auto_max_tlp", sa.String(), nullable=False, server_default="green"),
        sa.Column("artifact_tlp", sa.String(), nullable=False, server_default="amber"),
        sa.Column("cache_ttl_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("sources", sa.JSON(), nullable=True),
        sa.Column("vt_per_minute", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("vt_per_day", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("internal_domains", sa.JSON(), nullable=True),
        sa.Column("credentials_enc", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tenant_enrichment_configs_id", "tenant_enrichment_configs", ["id"])
    op.create_table(
        "enrichment_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("indicator_type", sa.String(), nullable=False),
        sa.Column("indicator_value", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("score", sa.String(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("link", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "indicator_type", "indicator_value", "source", name="uq_enrichment_result"),
    )
    op.create_index("ix_enrichment_results_id", "enrichment_results", ["id"])
    op.create_index("ix_enrichment_results_tenant_id", "enrichment_results", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("enrichment_results")
    op.drop_table("tenant_enrichment_configs")
```

Run `docker compose exec -T backend alembic upgrade head`, then `alembic downgrade -1`, then `alembic upgrade head`. Expected: no errors, and `alembic heads` shows `f4a5b6c7d8e9 (head)`.

- [ ] **Step 3: Write failing test** `backend/tests/test_ti_config.py`:

```python
import json
import logging
import secrets

import pytest
from sqlalchemy import delete

from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import DEFAULTS, load_settings, runnable_sources
from app.models.tenant import Tenant
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.utils.crypto import encrypt


@pytest.mark.asyncio
async def test_defaults_without_row_and_keyed_sources_inactive():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.commit()
        try:
            s = await load_settings(db, t.id)
            assert s.auto_max_tlp == "green" and s.artifact_tlp == "amber" and s.cache_ttl_hours == 24
            assert s.vt_per_minute == 4 and s.vt_per_day == 500 and s.keys == {}
            assert runnable_sources(s) == ["rdap", "crtsh"]
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == t.id))
            await db.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_row_with_keys_and_toggles(caplog):
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.flush()
        db.add(TenantEnrichmentConfig(
            tenant_id=t.id, auto_max_tlp="amber", sources={**DEFAULTS["sources"], "crtsh": False},
            internal_domains=["corp.local"],
            credentials_enc=encrypt(json.dumps({"virustotal_api_key": "vt-k", "abusech_auth_key": "ab-k"}))))
        await db.commit()
        try:
            s = await load_settings(db, t.id)
            assert s.auto_max_tlp == "amber" and s.internal_domains == ["corp.local"]
            assert s.keys == {"virustotal_api_key": "vt-k", "abusech_auth_key": "ab-k"}
            assert runnable_sources(s) == ["virustotal", "urlhaus", "threatfox", "rdap"]
            # undecryptable credentials -> no keys, key_error, ERROR log with tenant id only
            row = (await db.get(TenantEnrichmentConfig, (await db.execute(
                TenantEnrichmentConfig.__table__.select().where(TenantEnrichmentConfig.tenant_id == t.id))).first().id))
            row.credentials_enc = "garbage"
            await db.commit()
            caplog.set_level(logging.ERROR, logger="app.enrichment.config")
            s = await load_settings(db, t.id)
            assert s.keys == {} and s.key_error is True
            msgs = [r.getMessage() for r in caplog.records if r.name == "app.enrichment.config"]
            assert msgs == [f"ti_config decrypt_failed tenant={t.id}"]
        finally:
            await db.execute(delete(Tenant).where(Tenant.id == t.id))
            await db.commit()
    await engine.dispose()
```

- [ ] **Step 4: Run.** Expect FAIL with `ModuleNotFoundError: app.enrichment.config`.

- [ ] **Step 5: Implement** `backend/app/enrichment/config.py`:

```python
"""Per-tenant enrichment settings with defaults; keys decrypted only when loaded."""
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List

from sqlalchemy import select

from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.utils.crypto import decrypt

logger = logging.getLogger(__name__)

SOURCES = ("virustotal", "urlhaus", "threatfox", "rdap", "crtsh")
SOURCE_KEY = {"virustotal": "virustotal_api_key", "urlhaus": "abusech_auth_key", "threatfox": "abusech_auth_key"}
KEY_FIELDS = ("virustotal_api_key", "abusech_auth_key")
DEFAULTS = {
    "auto_max_tlp": "green", "artifact_tlp": "amber", "cache_ttl_hours": 24,
    "sources": {s: True for s in SOURCES}, "vt_per_minute": 4, "vt_per_day": 500, "internal_domains": [],
}


@dataclass
class EnrichmentSettings:
    auto_max_tlp: str = DEFAULTS["auto_max_tlp"]
    artifact_tlp: str = DEFAULTS["artifact_tlp"]
    cache_ttl_hours: int = DEFAULTS["cache_ttl_hours"]
    sources: Dict[str, bool] = field(default_factory=lambda: dict(DEFAULTS["sources"]))
    vt_per_minute: int = DEFAULTS["vt_per_minute"]
    vt_per_day: int = DEFAULTS["vt_per_day"]
    internal_domains: List[str] = field(default_factory=list)
    keys: Dict[str, str] = field(default_factory=dict)
    key_error: bool = False


def decrypt_keys(enc, tenant_id) -> tuple:
    if not enc:
        return {}, False
    try:
        data = json.loads(decrypt(enc))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return {k: v for k, v in data.items() if k in KEY_FIELDS and v}, False
    except Exception:
        logger.error("ti_config decrypt_failed tenant=%s", tenant_id)
        return {}, True


async def load_settings(db, tenant_id: int) -> EnrichmentSettings:
    row = (await db.execute(select(TenantEnrichmentConfig).where(
        TenantEnrichmentConfig.tenant_id == tenant_id))).scalars().first()
    if row is None:
        return EnrichmentSettings()
    keys, key_error = decrypt_keys(row.credentials_enc, tenant_id)
    return EnrichmentSettings(
        auto_max_tlp=row.auto_max_tlp, artifact_tlp=row.artifact_tlp, cache_ttl_hours=row.cache_ttl_hours,
        sources={**DEFAULTS["sources"], **(row.sources or {})}, vt_per_minute=row.vt_per_minute,
        vt_per_day=row.vt_per_day, internal_domains=list(row.internal_domains or []), keys=keys, key_error=key_error)


def runnable_sources(s: EnrichmentSettings) -> List[str]:
    out = []
    for src in SOURCES:
        if not s.sources.get(src, True):
            continue
        key = SOURCE_KEY.get(src)
        if key and not s.keys.get(key):
            continue
        out.append(src)
    return out
```

`runnable_sources` excludes keyed sources while `key_error` is True. The task (Task 6) records an `error` row for those sources, with the text "credentials could not be decrypted — re-enter in Integrations", so the admin can see the problem.

- [ ] **Step 6: Run the test, then the full suite.** Expect PASS. Run: `docker compose exec -T backend python -m pytest tests/test_ti_config.py -q && docker compose exec -T backend python -m pytest -q`

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/tenant_enrichment_config.py backend/app/models/enrichment_result.py backend/app/db/base.py \
  backend/alembic/versions/f4a5b6c7d8e9_threat_intel_enrichment.py backend/app/enrichment/config.py backend/tests/test_ti_config.py
git commit -m "feat(ti): enrichment tables, migration and tenant settings loader"
```

---

### Task 3: Source framework, VirusTotal and abuse.ch (URLhaus, ThreatFox)

**Files:**
- Create: `backend/app/enrichment/sources/__init__.py`, `base.py`, `virustotal.py`, `abusech.py`
- Test: `backend/tests/test_ti_sources_keyed.py`

**Interfaces:**
- Produces:
  - `@dataclass LookupResult(status: str, verdict: Optional[str] = None, score: Optional[str] = None, summary: dict = {}, link: Optional[str] = None, error: Optional[str] = None)`
  - `def http_client(transport=None, follow_redirects=False) -> httpx.AsyncClient` (timeout 15s)
  - `def parse_json(resp) -> dict`: raises `SourceError("unexpected response from <host>")` when the body is not JSON or not a dict.
  - `class SourceError(Exception)`
  - `async def lookup(itype, value, key, *, transport=None) -> LookupResult`, one per source module.
  - `REGISTRY: dict[str, module]`, mapping source name to a module exposing `NAME`, `TYPES` and `lookup`. `REGISTRY` is filled in `__init__.py`; Task 4 appends `rdap` and `crtsh`.
  - `def status_result(resp) -> Optional[LookupResult]`: maps 401/403 to `error "invalid API key"`, 429 to `rate_limited`, and ≥500 to `error "<source> unavailable (HTTP n)"`.

- [ ] **Step 1: Write failing tests** `backend/tests/test_ti_sources_keyed.py`:

```python
import base64
import json

import httpx
import pytest

from app.enrichment.sources import REGISTRY, abusech, virustotal


def transport(handler):
    return httpx.MockTransport(handler)


def vt_body(mal, sus=0, harmless=0, undetected=0, label=None):
    attrs = {"last_analysis_stats": {"malicious": mal, "suspicious": sus, "harmless": harmless, "undetected": undetected},
             "reputation": -5, "tags": ["a", "b", "c", "d", "e", "f"]}
    if label:
        attrs["popular_threat_classification"] = {"suggested_threat_label": label}
    return {"data": {"attributes": attrs}}


@pytest.mark.asyncio
@pytest.mark.parametrize("stats,verdict", [((3,), "malicious"), ((2,), "suspicious"), ((0, 1), "suspicious"),
                                           ((0, 0, 5, 60), "harmless")])
async def test_vt_verdicts(stats, verdict):
    seen = {}

    def h(req):
        seen["url"], seen["key"] = str(req.url), req.headers.get("x-apikey")
        return httpx.Response(200, json=vt_body(*stats))
    r = await virustotal.lookup("ip", "8.8.8.8", "K", transport=transport(h))
    assert r.status == "ok" and r.verdict == verdict
    assert seen == {"url": "https://www.virustotal.com/api/v3/ip_addresses/8.8.8.8", "key": "K"}
    assert len(r.summary["tags"]) == 5 and r.link == "https://www.virustotal.com/gui/ip-address/8.8.8.8"


@pytest.mark.asyncio
async def test_vt_url_id_and_label_and_score():
    url = "http://example.com/a"
    want = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")

    def h(req):
        assert str(req.url).endswith(f"/api/v3/urls/{want}")
        return httpx.Response(200, json=vt_body(45, 0, 10, 15, label="trojan.emotet/x"))
    r = await virustotal.lookup("url", url, "K", transport=transport(h))
    assert r.score == "45/70" and r.summary["popular_threat_label"] == "trojan.emotet/x"


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status,err", [(404, "not_found", None), (401, "error", "invalid API key"),
                                             (429, "rate_limited", None), (503, "error", "virustotal unavailable (HTTP 503)")])
async def test_vt_status_codes(code, status, err):
    r = await virustotal.lookup("domain", "example.com", "K", transport=transport(lambda req: httpx.Response(code)))
    assert r.status == status and r.error == err


@pytest.mark.asyncio
async def test_vt_malformed_body():
    r = await virustotal.lookup("file_hash", "a" * 64, "K",
                                transport=transport(lambda req: httpx.Response(200, text="<html>portal</html>")))
    assert r.status == "error" and "unexpected response" in r.error


@pytest.mark.asyncio
async def test_urlhaus_host_hit_and_miss():
    def hit(req):
        assert str(req.url) == "https://urlhaus-api.abuse.ch/v1/host/" and req.headers["Auth-Key"] == "AB"
        assert req.content == b"host=example.com"
        return httpx.Response(200, json={"query_status": "ok", "urls": [
            {"threat": "malware_download", "url_status": "online", "tags": ["exe"], "date_added": "2026-09-01"}]})
    r = await abusech.lookup_urlhaus("domain", "example.com", "AB", transport=transport(hit))
    assert r.status == "ok" and r.verdict == "malicious" and r.summary["threat"] == "malware_download"
    miss = transport(lambda req: httpx.Response(200, json={"query_status": "no_results"}))
    assert (await abusech.lookup_urlhaus("url", "http://x.com/", "AB", transport=miss)).status == "not_found"


@pytest.mark.asyncio
async def test_urlhaus_payload_for_hash():
    def h(req):
        assert str(req.url) == "https://urlhaus-api.abuse.ch/v1/payload/" and req.content == b"sha256_hash=" + b"a" * 64
        return httpx.Response(200, json={"query_status": "no_results"})
    assert (await abusech.lookup_urlhaus("file_hash", "a" * 64, "AB", transport=transport(h))).status == "not_found"


@pytest.mark.asyncio
async def test_threatfox_hit_hash_and_miss():
    def h(req):
        body = json.loads(req.content)
        assert body == {"query": "search_hash", "hash": "a" * 32}
        return httpx.Response(200, json={"query_status": "ok", "data": [
            {"malware_printable": "Emotet", "threat_type": "payload", "confidence_level": 75, "first_seen": "x", "tags": None},
            {"malware_printable": "Emotet", "threat_type": "payload", "confidence_level": 90, "first_seen": "y", "tags": ["t"]}]})
    r = await abusech.lookup_threatfox("file_hash", "a" * 32, "AB", transport=transport(h))
    assert r.status == "ok" and r.verdict == "malicious" and r.score == "confidence 90"
    assert r.summary["malware_printable"] == "Emotet"
    miss = transport(lambda req: httpx.Response(200, json={"query_status": "no_result"}))
    assert (await abusech.lookup_threatfox("ip", "1.2.3.4", "AB", transport=miss)).status == "not_found"


@pytest.mark.asyncio
async def test_timeout_is_error():
    def boom(req):
        raise httpx.ReadTimeout("slow")
    r = await virustotal.lookup("ip", "8.8.8.8", "K", transport=transport(boom))
    assert r.status == "error" and r.error == "virustotal timed out"


def test_registry():
    assert {"virustotal", "urlhaus", "threatfox"} <= set(REGISTRY)
    assert REGISTRY["virustotal"].TYPES == frozenset({"ip", "domain", "url", "file_hash"})
```

- [ ] **Step 2: Run.** Expect FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement.**

`backend/app/enrichment/sources/base.py`:

```python
"""Shared pieces for enrichment sources."""
from dataclasses import dataclass, field
from typing import Optional

import httpx


class SourceError(Exception):
    pass


@dataclass
class LookupResult:
    status: str
    verdict: Optional[str] = None
    score: Optional[str] = None
    summary: dict = field(default_factory=dict)
    link: Optional[str] = None
    error: Optional[str] = None


def http_client(transport=None, follow_redirects: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=15.0, follow_redirects=follow_redirects, transport=transport,
                             headers={"User-Agent": "SOC-Hub-Enrichment/1.0"})


def parse_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        raise SourceError(f"unexpected response from {resp.request.url.host}") from None
    if not isinstance(data, dict):
        raise SourceError(f"unexpected response from {resp.request.url.host}")
    return data


def status_result(name: str, resp: httpx.Response) -> Optional[LookupResult]:
    if resp.status_code in (401, 403):
        return LookupResult(status="error", error="invalid API key")
    if resp.status_code == 429:
        return LookupResult(status="rate_limited")
    if resp.status_code >= 500:
        return LookupResult(status="error", error=f"{name} unavailable (HTTP {resp.status_code})")
    return None


async def guarded(name: str, coro) -> LookupResult:
    """Run a source request coroutine, mapping transport failures to error results."""
    try:
        return await coro
    except httpx.TimeoutException:
        return LookupResult(status="error", error=f"{name} timed out")
    except SourceError as e:
        return LookupResult(status="error", error=str(e))
    except httpx.HTTPError as e:
        return LookupResult(status="error", error=f"{name} request failed ({type(e).__name__})")
```

`backend/app/enrichment/sources/virustotal.py`:

```python
import base64

from app.enrichment.sources.base import LookupResult, guarded, http_client, parse_json, status_result

NAME = "virustotal"
TYPES = frozenset({"ip", "domain", "url", "file_hash"})
_API = "https://www.virustotal.com/api/v3"
_PATH = {"ip": "ip_addresses", "domain": "domains", "file_hash": "files", "url": "urls"}
_GUI = {"ip": "ip-address", "domain": "domain", "file_hash": "file", "url": "url"}


def _id(itype: str, value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=") if itype == "url" else value


async def _lookup(itype, value, key, transport):
    vid = _id(itype, value)
    async with http_client(transport) as c:
        resp = await c.get(f"{_API}/{_PATH[itype]}/{vid}", headers={"x-apikey": key})
    if resp.status_code == 404:
        return LookupResult(status="not_found")
    bad = status_result(NAME, resp)
    if bad:
        return bad
    attrs = (parse_json(resp).get("data") or {}).get("attributes") or {}
    st = attrs.get("last_analysis_stats") or {}
    mal, sus = int(st.get("malicious") or 0), int(st.get("suspicious") or 0)
    harmless, und = int(st.get("harmless") or 0), int(st.get("undetected") or 0)
    total = mal + sus + harmless + und
    if mal >= 3:
        verdict = "malicious"
    elif mal or sus:
        verdict = "suspicious"
    elif total:
        verdict = "harmless"
    else:
        verdict = "unknown"
    summary = {"malicious": mal, "suspicious": sus, "harmless": harmless, "undetected": und,
               "reputation": attrs.get("reputation"), "tags": list(attrs.get("tags") or [])[:5]}
    label = (attrs.get("popular_threat_classification") or {}).get("suggested_threat_label")
    if label:
        summary["popular_threat_label"] = label
    return LookupResult(status="ok", verdict=verdict, score=f"{mal}/{total}" if total else None, summary=summary,
                        link=f"https://www.virustotal.com/gui/{_GUI[itype]}/{vid}")


async def lookup(itype: str, value: str, key: str, *, transport=None) -> LookupResult:
    return await guarded(NAME, _lookup(itype, value, key, transport))
```

`backend/app/enrichment/sources/abusech.py`:

```python
"""URLhaus and ThreatFox (abuse.ch); both use the same free Auth-Key."""
from app.enrichment.sources.base import LookupResult, guarded, http_client, parse_json, status_result

URLHAUS, THREATFOX = "urlhaus", "threatfox"
URLHAUS_TYPES = frozenset({"url", "domain", "ip", "file_hash"})
THREATFOX_TYPES = frozenset({"url", "domain", "ip", "file_hash"})


async def _urlhaus(itype, value, key, transport):
    if itype == "url":
        path, form = "url", {"url": value}
    elif itype == "file_hash":
        if len(value) not in (32, 64):
            return LookupResult(status="not_found")
        path, form = "payload", {("md5_hash" if len(value) == 32 else "sha256_hash"): value}
    else:
        path, form = "host", {"host": value}
    async with http_client(transport) as c:
        resp = await c.post(f"https://urlhaus-api.abuse.ch/v1/{path}/", data=form, headers={"Auth-Key": key})
    bad = status_result(URLHAUS, resp)
    if bad:
        return bad
    data = parse_json(resp)
    if data.get("query_status") != "ok":
        return LookupResult(status="not_found")
    entries = data.get("urls") or ([data] if path in ("url", "payload") else [])
    first = entries[0] if entries else data
    summary = {"threat": first.get("threat") or data.get("signature") or "malware",
               "url_status": first.get("url_status"), "tags": list(first.get("tags") or [])[:5],
               "first_seen": first.get("date_added") or data.get("firstseen")}
    return LookupResult(status="ok", verdict="malicious", summary=summary,
                        link=f"https://urlhaus.abuse.ch/browse.php?search={value}")


async def _threatfox(itype, value, key, transport):
    body = {"query": "search_hash", "hash": value} if itype == "file_hash" else {"query": "search_ioc", "search_term": value}
    async with http_client(transport) as c:
        resp = await c.post("https://threatfox-api.abuse.ch/api/v1/", json=body, headers={"Auth-Key": key})
    bad = status_result(THREATFOX, resp)
    if bad:
        return bad
    data = parse_json(resp)
    rows = data.get("data") if data.get("query_status") == "ok" else None
    if not rows or not isinstance(rows, list):
        return LookupResult(status="not_found")
    best = max(rows, key=lambda r: int(r.get("confidence_level") or 0))
    summary = {"malware_printable": best.get("malware_printable"), "threat_type": best.get("threat_type"),
               "confidence_level": best.get("confidence_level"), "first_seen": best.get("first_seen"),
               "tags": list(best.get("tags") or [])[:5]}
    return LookupResult(status="ok", verdict="malicious", score=f"confidence {best.get('confidence_level')}",
                        summary=summary, link=f"https://threatfox.abuse.ch/browse.php?search=ioc%3A{value}")


async def lookup_urlhaus(itype, value, key, *, transport=None) -> LookupResult:
    return await guarded(URLHAUS, _urlhaus(itype, value, key, transport))


async def lookup_threatfox(itype, value, key, *, transport=None) -> LookupResult:
    return await guarded(THREATFOX, _threatfox(itype, value, key, transport))
```

`backend/app/enrichment/sources/__init__.py`:

```python
"""Source registry: name -> object with NAME, TYPES and async lookup(itype, value, key, *, transport=None)."""
from types import SimpleNamespace

from app.enrichment.sources import abusech, virustotal

REGISTRY = {
    "virustotal": virustotal,
    "urlhaus": SimpleNamespace(NAME="urlhaus", TYPES=abusech.URLHAUS_TYPES, lookup=abusech.lookup_urlhaus),
    "threatfox": SimpleNamespace(NAME="threatfox", TYPES=abusech.THREATFOX_TYPES, lookup=abusech.lookup_threatfox),
}
```

- [ ] **Step 4: Run.** Expect PASS. Run: `docker compose exec -T backend python -m pytest tests/test_ti_sources_keyed.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/enrichment/sources backend/tests/test_ti_sources_keyed.py
git commit -m "feat(ti): source framework, VirusTotal and abuse.ch lookups"
```

---

### Task 4: RDAP and crt.sh sources (no key)

**Files:**
- Create: `backend/app/enrichment/sources/rdap.py`, `backend/app/enrichment/sources/crtsh.py`
- Modify: `backend/app/enrichment/sources/__init__.py` (add both to `REGISTRY`)
- Test: `backend/tests/test_ti_sources_free.py`

**Interfaces:**
- Consumes: `LookupResult`, `guarded`, `http_client`, `parse_json`, `status_result`, `SourceError` from Task 3, and `app.workflows.ssrf.assert_url_allowed(url, allowlist)`, which raises `SSRFError`.
- Produces: `rdap.lookup(itype, value, key=None, *, transport=None, now=None)` and `crtsh.lookup(itype, value, key=None, *, transport=None, now=None)`. `now` is a `datetime` used for the "<30 days" rule; it defaults to the current UTC time.

- [ ] **Step 1: Write failing tests** `backend/tests/test_ti_sources_free.py`:

```python
from datetime import datetime, timezone

import httpx
import pytest

from app.enrichment.sources import REGISTRY, crtsh, rdap

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def T(h):
    return httpx.MockTransport(h)


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    # assert_url_allowed resolves DNS; tests must not touch the network.
    import app.enrichment.sources.rdap as m

    def fake(url, allowlist):
        if "internal" in url:
            from app.workflows.ssrf import SSRFError
            raise SSRFError("blocked")
        return "203.0.113.10"
    monkeypatch.setattr(m, "assert_url_allowed", fake)


@pytest.mark.asyncio
async def test_rdap_domain_follows_https_redirect_and_flags_young():
    def h(req):
        if req.url.host == "rdap.org":
            return httpx.Response(302, headers={"location": "https://rdap.verisign.com/com/v1/domain/new.com"})
        return httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "2026-09-20T00:00:00Z"}],
                                         "entities": [{"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Reg Inc"]]]}]})
    r = await rdap.lookup("domain", "new.com", transport=T(h), now=NOW)
    assert r.status == "ok" and r.verdict == "suspicious"
    assert r.summary == {"registrar": "Reg Inc", "created": "2026-09-20T00:00:00Z"}


@pytest.mark.asyncio
async def test_rdap_old_domain_unknown_and_404():
    old = T(lambda req: httpx.Response(200, json={"events": [{"eventAction": "registration", "eventDate": "1997-09-15T04:00:00Z"}]}))
    assert (await rdap.lookup("domain", "google.com", transport=old, now=NOW)).verdict == "unknown"
    assert (await rdap.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(404)), now=NOW)).status == "not_found"


@pytest.mark.asyncio
async def test_rdap_ip_summary():
    body = {"name": "GOOGLE", "country": "US", "handle": "NET-8-8-8-0-1", "startAddress": "8.8.8.0", "endAddress": "8.8.8.255"}
    r = await rdap.lookup("ip", "8.8.8.8", transport=T(lambda req: httpx.Response(200, json=body)), now=NOW)
    assert r.verdict == "unknown" and r.summary == {"name": "GOOGLE", "country": "US", "handle": "NET-8-8-8-0-1",
                                                    "range": "8.8.8.0 - 8.8.8.255"}


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["http://rdap.example/domain/x.com", "https://internal.example/domain/x.com"])
async def test_rdap_refuses_unsafe_redirect(location):
    r = await rdap.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(302, headers={"location": location})), now=NOW)
    assert r.status == "error" and r.error == "rdap redirect refused"


@pytest.mark.asyncio
async def test_rdap_too_many_redirects():
    r = await rdap.lookup("domain", "x.com", transport=T(
        lambda req: httpx.Response(302, headers={"location": "https://rdap.example/domain/x.com"})), now=NOW)
    assert r.status == "error" and r.error == "rdap redirect refused"


@pytest.mark.asyncio
async def test_crtsh_young_and_count_and_empty():
    certs = [{"not_before": "2026-09-25T00:00:00"}, {"not_before": "2026-09-28T00:00:00"}]

    def h(req):
        assert str(req.url) == "https://crt.sh/?q=new.com&output=json"
        return httpx.Response(200, json=certs)
    r = await crtsh.lookup("domain", "new.com", transport=T(h), now=NOW)
    assert r.verdict == "suspicious" and r.summary == {"cert_count": 2, "first_cert": "2026-09-25T00:00:00"}
    assert (await crtsh.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(200, json=[])), now=NOW)).status == "not_found"


@pytest.mark.asyncio
async def test_crtsh_502_html():
    r = await crtsh.lookup("domain", "x.com", transport=T(lambda req: httpx.Response(502, text="<html>")), now=NOW)
    assert r.status == "error" and r.error == "crtsh unavailable (HTTP 502)"


def test_registry_complete():
    assert set(REGISTRY) == {"virustotal", "urlhaus", "threatfox", "rdap", "crtsh"}
    assert REGISTRY["crtsh"].TYPES == frozenset({"domain"}) and REGISTRY["rdap"].TYPES == frozenset({"domain", "ip"})
```

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement** `backend/app/enrichment/sources/rdap.py`:

```python
"""RDAP via the rdap.org bootstrap redirector; each redirect hop is SSRF-vetted."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin, urlsplit

from app.enrichment.sources.base import LookupResult, SourceError, guarded, http_client, parse_json, status_result
from app.workflows.ssrf import SSRFError, assert_url_allowed

NAME = "rdap"
TYPES = frozenset({"domain", "ip"})
_MAX_HOPS = 3


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _registrar(data: dict) -> Optional[str]:
    for ent in data.get("entities") or []:
        if "registrar" in (ent.get("roles") or []):
            for item in ((ent.get("vcardArray") or [None, []])[1] or []):
                if isinstance(item, list) and item and item[0] == "fn":
                    return item[-1]
    return None


async def _lookup(itype, value, transport, now):
    url = f"https://rdap.org/{'domain' if itype == 'domain' else 'ip'}/{value}"
    async with http_client(transport) as c:
        for _ in range(_MAX_HOPS + 1):
            resp = await c.get(url, headers={"Accept": "application/rdap+json"})
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            nxt = urljoin(url, resp.headers.get("location", ""))
            if urlsplit(nxt).scheme != "https":
                raise SourceError("rdap redirect refused")
            try:
                assert_url_allowed(nxt, [])
            except SSRFError:
                raise SourceError("rdap redirect refused") from None
            url = nxt
        else:
            raise SourceError("rdap redirect refused")
    if resp.status_code == 404:
        return LookupResult(status="not_found")
    bad = status_result(NAME, resp)
    if bad:
        return bad
    data = parse_json(resp)
    if itype == "ip":
        rng = f"{data.get('startAddress')} - {data.get('endAddress')}" if data.get("startAddress") else None
        summary = {k: v for k, v in {"name": data.get("name"), "country": data.get("country"),
                                      "handle": data.get("handle"), "range": rng}.items() if v}
        return LookupResult(status="ok", verdict="unknown", summary=summary, link=f"https://rdap.org/ip/{value}")
    created = next((e.get("eventDate") for e in data.get("events") or [] if e.get("eventAction") == "registration"), None)
    dt = _parse_dt(created) if created else None
    verdict = "suspicious" if dt and now - dt < timedelta(days=30) else "unknown"
    summary = {k: v for k, v in {"registrar": _registrar(data), "created": created}.items() if v}
    return LookupResult(status="ok", verdict=verdict, summary=summary, link=f"https://rdap.org/domain/{value}")


async def lookup(itype, value, key=None, *, transport=None, now=None) -> LookupResult:
    return await guarded(NAME, _lookup(itype, value, transport, now or datetime.now(timezone.utc)))
```

`backend/app/enrichment/sources/crtsh.py`:

```python
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app.enrichment.sources.base import LookupResult, SourceError, guarded, http_client, status_result

NAME = "crtsh"
TYPES = frozenset({"domain"})


async def _lookup(value, transport, now):
    async with http_client(transport) as c:
        resp = await c.get(f"https://crt.sh/?q={quote(value)}&output=json")
    bad = status_result(NAME, resp)
    if bad:
        return bad
    try:
        certs = resp.json()
    except ValueError:
        raise SourceError("unexpected response from crt.sh") from None
    if not isinstance(certs, list):
        raise SourceError("unexpected response from crt.sh")
    if not certs:
        return LookupResult(status="not_found")
    first = min((c.get("not_before") for c in certs if c.get("not_before")), default=None)
    verdict = "unknown"
    if first:
        try:
            d = datetime.fromisoformat(first).replace(tzinfo=timezone.utc)
            verdict = "suspicious" if now - d < timedelta(days=30) else "unknown"
        except ValueError:
            pass
    return LookupResult(status="ok", verdict=verdict, summary={"cert_count": len(certs), "first_cert": first},
                        link=f"https://crt.sh/?q={quote(value)}")


async def lookup(itype, value, key=None, *, transport=None, now=None) -> LookupResult:
    return await guarded(NAME, _lookup(value, transport, now or datetime.now(timezone.utc)))
```

Append to `backend/app/enrichment/sources/__init__.py`:

```python
from app.enrichment.sources import crtsh, rdap  # noqa: E402

REGISTRY["rdap"] = rdap
REGISTRY["crtsh"] = crtsh
```

- [ ] **Step 4: Run.** Expect PASS. Run: `docker compose exec -T backend python -m pytest tests/test_ti_sources_free.py tests/test_ti_sources_keyed.py -q`

- [ ] **Step 5: Commit**

```bash
git add backend/app/enrichment/sources backend/tests/test_ti_sources_free.py
git commit -m "feat(ti): RDAP and crt.sh sources with vetted RDAP redirects"
```

---

### Task 5: Redis rate limiter

**Files:**
- Create: `backend/app/enrichment/ratelimit.py`
- Test: `backend/tests/test_ti_ratelimit.py`

**Interfaces:**
- Produces:
  - `limits_for(source: str, s: EnrichmentSettings) -> list[tuple[int, int]]`. Returns a list of `(max, window_seconds)`.
  - `class RateLimiter(redis=None, prefix="ti:rl")`
  - `async def acquire(self, tenant_id, source, limits) -> Optional[int]`: returns None when allowed. Otherwise it returns the seconds until the blocking window resets; that value is ≥ 86400/2 when the daily VT window is the blocker.
  - Windows are fixed. `acquire` is atomic across all of a source's windows: either every counter is incremented, or none is.
  - crt.sh uses the global key `{prefix}:global:crtsh:5`.

- [ ] **Step 1: Write failing test** `backend/tests/test_ti_ratelimit.py`:

```python
import secrets

import pytest
import redis.asyncio as aioredis

from app.core.config import settings
from app.enrichment.config import EnrichmentSettings
from app.enrichment.ratelimit import RateLimiter, limits_for


def test_limits_for():
    s = EnrichmentSettings(vt_per_minute=4, vt_per_day=500)
    assert limits_for("virustotal", s) == [(4, 60), (500, 86400)]
    assert limits_for("urlhaus", s) == [(60, 60)] and limits_for("threatfox", s) == [(60, 60)]
    assert limits_for("rdap", s) == [(30, 60)] and limits_for("crtsh", s) == [(1, 5)]


@pytest.mark.asyncio
async def test_acquire_blocks_and_is_atomic():
    r = aioredis.from_url(settings.REDIS_URL)
    prefix = f"ti:test:{secrets.token_hex(4)}"
    rl = RateLimiter(r, prefix=prefix)
    try:
        assert await rl.acquire(1, "virustotal", [(2, 60), (3, 86400)]) is None
        assert await rl.acquire(1, "virustotal", [(2, 60), (3, 86400)]) is None
        wait = await rl.acquire(1, "virustotal", [(2, 60), (3, 86400)])
        assert wait is not None and 0 < wait <= 60
        # the refused call must not have consumed the daily budget
        day = await r.get(f"{prefix}:1:virustotal:86400")
        assert int(day) == 2
        # other tenant unaffected
        assert await rl.acquire(2, "virustotal", [(2, 60)]) is None
        # crtsh is global across tenants
        assert await rl.acquire(1, "crtsh", [(1, 5)]) is None
        assert await rl.acquire(2, "crtsh", [(1, 5)]) is not None
    finally:
        keys = [k async for k in r.scan_iter(f"{prefix}:*")]
        if keys:
            await r.delete(*keys)
        await r.aclose()
```

- [ ] **Step 2: Run.** Expect FAIL.

- [ ] **Step 3: Implement** `backend/app/enrichment/ratelimit.py`:

```python
"""Per-tenant (crt.sh: global) fixed-window rate limits in Redis; all-or-nothing across windows."""
from typing import List, Optional, Tuple

ACQUIRE_LUA = """
for i = 1, #KEYS do
  local count = tonumber(redis.call('GET', KEYS[i]) or '0')
  if count >= tonumber(ARGV[2*i-1]) then
    local ttl = redis.call('TTL', KEYS[i])
    if ttl < 1 then ttl = tonumber(ARGV[2*i]) end
    return ttl
  end
end
for i = 1, #KEYS do
  redis.call('INCR', KEYS[i])
  redis.call('EXPIRE', KEYS[i], ARGV[2*i], 'NX')
end
return -1
"""

_FIXED = {"urlhaus": [(60, 60)], "threatfox": [(60, 60)], "rdap": [(30, 60)], "crtsh": [(1, 5)]}


def limits_for(source: str, s) -> List[Tuple[int, int]]:
    if source == "virustotal":
        return [(s.vt_per_minute, 60), (s.vt_per_day, 86400)]
    return list(_FIXED[source])


class RateLimiter:
    def __init__(self, redis=None, prefix: str = "ti:rl"):
        self._redis = redis
        self.prefix = prefix

    @property
    def redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            from app.core.config import settings
            self._redis = aioredis.from_url(settings.REDIS_URL)
        return self._redis

    async def acquire(self, tenant_id: int, source: str, limits) -> Optional[int]:
        scope = "global" if source == "crtsh" else str(tenant_id)
        keys = [f"{self.prefix}:{scope}:{source}:{w}" for _, w in limits]
        args = [x for m, w in limits for x in (m, w)]
        ttl = int(await self.redis.eval(ACQUIRE_LUA, len(keys), *keys, *args))
        return None if ttl < 0 else ttl
```

- [ ] **Step 4: Run.** Expect PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/enrichment/ratelimit.py backend/tests/test_ti_ratelimit.py
git commit -m "feat(ti): atomic per-tenant Redis rate limiter"
```

---

### Task 6: Enrichment task, auto-trigger hook and kill switch

**Files:**
- Create: `backend/app/tasks/enrichment.py`, `backend/app/enrichment/hooks.py`, `backend/tests/conftest.py`
- Modify:
  - `backend/app/worker.py`: add `import app.tasks.enrichment  # noqa: E402,F401` next to the other task imports.
  - `backend/app/core/config.py`: add `ENRICHMENT_ENABLED: bool = True` next to the `AI_*` settings.
  - `backend/app/main.py`: add `import app.enrichment.hooks  # noqa: F401` after the router includes, so the session listeners register in the API process.
- Test: `backend/tests/test_ti_task.py`, `backend/tests/test_ti_hooks.py`

**Interfaces:**
- Consumes:
  - from Task 1: `normalise`, `tlp_allows`, `should_skip`
  - from Task 2: `load_settings`, `runnable_sources`, `SOURCE_KEY`, `EnrichmentSettings`, `EnrichmentResult`
  - from Tasks 3–4: `REGISTRY`
  - from Task 5: `RateLimiter`, `limits_for`
  - `app.ai.errors.scrub`
- Produces:
  - `async def run_enrichment(db, tenant_id, itype, value, *, sources: Optional[list[str]] = None, force: bool = False, limiter=None, lock=None, now=None) -> dict[str, str]`. It works on an already-normalised (itype, value) and returns `{source: status}`.
  - `def enrich_indicator(tenant_id, itype, value, tlp, force=False)`: the Celery task. It normalises the value, checks the TLP for automatic calls (`force=False`), then calls `run_enrichment`.
  - `async def upsert_result(db, tenant_id, itype, value, source, **fields)`
  - `def enqueue(tenant_id, raw_type, value, tlp, force=False) -> None`, in `app/enrichment/hooks.py`. It calls `enrich_indicator.delay(...)` and never raises.
  - Session listeners on `sqlalchemy.orm.Session`:
    - `after_flush` collects IOCs that are new, or whose `value`, `ioc_type` or `tlp` changed. It also collects artifacts that are new or whose `value` or `artifact_type` changed.
    - `after_commit` calls `enqueue` for each collected item. Artifacts pass `tlp=None`; the task substitutes `artifact_tlp`.
    - `after_rollback` clears the collection.
- Behaviour rules:
  - When `ENRICHMENT_ENABLED` is False, `enqueue` does nothing.
  - The dedupe lock is a Redis `SET ti:lock:{tenant}:{itype}:{sha256(value)} 1 NX EX 60`. If the lock is already held, the task returns without doing anything. Manual runs, which pass `force=True`, skip the lock.
  - Stale check: a source is skipped when its row has `status` in (`ok`, `not_found`) and `fetched_at` is newer than `now - cache_ttl_hours`, unless `force=True`.
  - A `should_skip` value gets status `skipped` for each runnable source, with no network call.
  - A keyed source that is enabled but whose key failed to decrypt gets status `error` with the message "credentials could not be decrypted — re-enter in Integrations".
  - Rate limiting:
    - If `acquire` returns a wait under 3600s, the task re-enqueues that one source with `enrich_indicator.apply_async((tid, itype, value, "white", True), {"only": [src], "attempt": n+1}, countdown=wait)`, up to 10 attempts. After that the status becomes `rate_limited`.
    - A wait of 3600s or more (the daily VT window) gives status `rate_limited` with the error "VirusTotal daily quota reached".
    - Retried tasks run with `force=True` and pass `only`, so their TLP was already approved.
  - Every source writes exactly one log line: `ti_lookup tenant=%s source=%s type=%s outcome=%s duration_ms=%d`.
  - Every error string goes through `scrub(err, list(settings.keys.values()))`.

- [ ] **Step 1: conftest.** `backend/tests/conftest.py` keeps the test suite from enqueueing real Celery tasks, which would run real lookups:

```python
import pytest


@pytest.fixture(autouse=True)
def _no_real_enrichment_enqueue(monkeypatch):
    calls = []
    import app.enrichment.hooks as hooks
    monkeypatch.setattr(hooks, "_delay", lambda *a, **k: calls.append((a, k)))
    yield calls
```

- [ ] **Step 2: Write failing tests** `backend/tests/test_ti_task.py`:

```python
import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import EnrichmentSettings
from app.enrichment.sources.base import LookupResult
from app.models.enrichment_result import EnrichmentResult
from app.models.tenant import Tenant
import app.tasks.enrichment as task

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


class FakeLimiter:
    def __init__(self, waits=None):
        self.waits = waits or {}

    async def acquire(self, tid, source, limits):
        return self.waits.get(source)


class FakeSource:
    def __init__(self, name, types, result=None, exc=None):
        self.NAME, self.TYPES, self.result, self.exc, self.calls = name, frozenset(types), result, exc, 0

    async def lookup(self, itype, value, key=None, **kw):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
async def tenant():
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.commit()
        tid = t.id
    yield tid
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Tenant).where(Tenant.id == tid))
        await db.commit()
    await engine.dispose()


def patch_world(monkeypatch, sources, settings=None, delays=None):
    monkeypatch.setattr(task, "REGISTRY", {s.NAME: s for s in sources})

    async def fake_settings(db, tid):
        return settings or EnrichmentSettings(sources={s.NAME: True for s in sources})
    monkeypatch.setattr(task, "load_settings", fake_settings)
    monkeypatch.setattr(task, "runnable_sources", lambda s: [n for n in s.sources if s.sources[n]])
    monkeypatch.setattr(task, "_retry", lambda *a, **k: (delays if delays is not None else []).append((a, k)))


async def rows(tid):
    async with AsyncSessionLocal() as db:
        return {r.source: r for r in (await db.execute(select(EnrichmentResult).where(
            EnrichmentResult.tenant_id == tid))).scalars()}


@pytest.mark.asyncio
async def test_sources_independent_and_logged(tenant, monkeypatch, caplog):
    good = FakeSource("rdap", {"domain"}, LookupResult(status="ok", verdict="unknown", summary={"x": 1}))
    bad = FakeSource("crtsh", {"domain"}, exc=RuntimeError("boom secret-key-123"))
    patch_world(monkeypatch, [good, bad], EnrichmentSettings(sources={"rdap": True, "crtsh": True},
                                                              keys={"virustotal_api_key": "secret-key-123"}))
    caplog.set_level(logging.INFO, logger="app.tasks.enrichment")
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW)
    assert out == {"rdap": "ok", "crtsh": "error"}
    rs = await rows(tenant)
    assert rs["rdap"].verdict == "unknown" and rs["rdap"].fetched_at is not None
    assert "secret-key-123" not in rs["crtsh"].error
    lines = [r.getMessage() for r in caplog.records if r.name == "app.tasks.enrichment"]
    assert len(lines) == 2 and all(l.startswith(f"ti_lookup tenant={tenant} source=") for l in lines)
    assert not any("example.com" in l for l in lines)


@pytest.mark.asyncio
async def test_cache_fresh_skips_and_force_refreshes(tenant, monkeypatch):
    src = FakeSource("rdap", {"domain"}, LookupResult(status="ok", verdict="unknown"))
    patch_world(monkeypatch, [src])
    async with AsyncSessionLocal() as db:
        await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW)
        await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW + timedelta(hours=1))
        assert src.calls == 1
        await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW + timedelta(hours=25))
        assert src.calls == 2
        await task.run_enrichment(db, tenant, "domain", "example.com", force=True, limiter=FakeLimiter(), now=NOW + timedelta(hours=25))
        assert src.calls == 3


@pytest.mark.asyncio
async def test_private_ip_skipped_without_call(tenant, monkeypatch):
    src = FakeSource("rdap", {"ip"}, LookupResult(status="ok"))
    patch_world(monkeypatch, [src])
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "ip", "10.0.0.1", limiter=FakeLimiter(), now=NOW)
    assert out == {"rdap": "skipped"} and src.calls == 0


@pytest.mark.asyncio
async def test_minute_limit_retries_daily_limit_stops(tenant, monkeypatch):
    vt = FakeSource("virustotal", {"ip"}, LookupResult(status="ok"))
    delays = []
    patch_world(monkeypatch, [vt], delays=delays)
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "ip", "8.8.8.8", limiter=FakeLimiter({"virustotal": 30}), now=NOW)
        assert out == {"virustotal": "pending"} and len(delays) == 1 and delays[0][1]["countdown"] == 30
        out = await task.run_enrichment(db, tenant, "ip", "8.8.8.8", force=True,
                                        limiter=FakeLimiter({"virustotal": 40000}), now=NOW)
    assert out == {"virustotal": "rate_limited"} and vt.calls == 0
    assert (await rows(tenant))["virustotal"].error == "VirusTotal daily quota reached"


@pytest.mark.asyncio
async def test_retry_attempt_cap(tenant, monkeypatch):
    vt = FakeSource("virustotal", {"ip"}, LookupResult(status="ok"))
    delays = []
    patch_world(monkeypatch, [vt], delays=delays)
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "ip", "8.8.8.8", force=True, attempt=10,
                                        limiter=FakeLimiter({"virustotal": 30}), now=NOW)
    assert out == {"virustotal": "rate_limited"} and delays == []


@pytest.mark.asyncio
async def test_key_decrypt_failure_marks_keyed_sources(tenant, monkeypatch):
    rd = FakeSource("rdap", {"domain"}, LookupResult(status="ok", verdict="unknown"))
    patch_world(monkeypatch, [rd], EnrichmentSettings(sources={"rdap": True, "virustotal": True}, key_error=True))
    monkeypatch.setattr(task, "runnable_sources", lambda s: ["rdap"])
    async with AsyncSessionLocal() as db:
        out = await task.run_enrichment(db, tenant, "domain", "example.com", limiter=FakeLimiter(), now=NOW)
    assert out["virustotal"] == "error" and out["rdap"] == "ok"
    assert "re-enter in Integrations" in (await rows(tenant))["virustotal"].error


@pytest.mark.asyncio
async def test_dedupe_lock(monkeypatch):
    seen = []

    async def fake_run(db, *a, **k):
        seen.append(a)
        return {}
    monkeypatch.setattr(task, "run_enrichment", fake_run)

    class Lock:
        def __init__(self):
            self.held = set()

        async def acquire(self, key):
            if key in self.held:
                return False
            self.held.add(key)
            return True
    lock = Lock()
    monkeypatch.setattr(task, "_lock", lambda: lock)
    await task._entry(1, "domain", "Example.com", "white", False)
    await task._entry(1, "domain", "example.com", "white", False)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_entry_tlp_gating_and_artifact_default(monkeypatch):
    seen = []

    async def fake_run(db, *a, **k):
        seen.append(a)
        return {}
    monkeypatch.setattr(task, "run_enrichment", fake_run)

    async def fake_settings(db, tid):
        return EnrichmentSettings(auto_max_tlp="green", artifact_tlp="amber")
    monkeypatch.setattr(task, "load_settings", fake_settings)

    class Always:
        async def acquire(self, key):
            return True
    monkeypatch.setattr(task, "_lock", lambda: Always())
    await task._entry(1, "domain", "a.com", "amber", False)     # auto + amber > green -> no run
    await task._entry(1, "domain", "b.com", None, False)        # artifact -> amber -> no run
    await task._entry(1, "domain", "c.com", "green", False)     # runs
    await task._entry(1, "domain", "d.com", "red", True)        # forced -> runs
    await task._entry(1, "email", "x@y.com", "white", True)     # not enrichable -> no run
    assert [a[2] for a in seen] == ["c.com", "d.com"]
```

`backend/tests/test_ti_hooks.py` checks the hook through the conftest recorder:

```python
import secrets

import pytest
from sqlalchemy import delete

from app.db.session import AsyncSessionLocal, engine
from app.models.artifact import Artifact, ArtifactType
from app.models.ioc import IOC
from app.models.tenant import Tenant


@pytest.mark.asyncio
async def test_hook_enqueues_on_create_and_relevant_changes(_no_real_enrichment_enqueue):
    calls = _no_real_enrichment_enqueue
    async with AsyncSessionLocal() as db:
        t = Tenant(name="wf-test", slug=f"wf-test-{secrets.token_hex(3)}-ti")
        db.add(t)
        await db.commit()
        try:
            ioc = IOC(tenant_id=t.id, ioc_type="domain", value="evil.example", tlp="amber")
            art = Artifact(tenant_id=t.id, artifact_type=ArtifactType.DOMAIN, value="art.example")
            db.add_all([ioc, art])
            await db.commit()
            assert ((t.id, "domain", "evil.example", "amber", False), {}) in calls
            assert ((t.id, "domain", "art.example", None, False), {}) in calls
            calls.clear()
            ioc.description = "notes only"
            await db.commit()
            assert calls == []
            ioc.tlp = "green"
            await db.commit()
            assert calls == [((t.id, "domain", "evil.example", "green", False), {})]
            calls.clear()
            ioc.value = "other.example"
            await db.rollback()
            assert calls == []
        finally:
            await db.execute(delete(IOC).where(IOC.tenant_id == t.id))
            await db.execute(delete(Artifact).where(Artifact.tenant_id == t.id))
            await db.execute(delete(Tenant).where(Tenant.id == t.id))
            await db.commit()
    await engine.dispose()


def test_kill_switch(monkeypatch, _no_real_enrichment_enqueue):
    import app.enrichment.hooks as hooks
    from app.core.config import settings
    monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", False)
    hooks.enqueue(1, "domain", "x.com", "white")
    assert _no_real_enrichment_enqueue == []
```

- [ ] **Step 3: Run.** Expect FAIL with `ModuleNotFoundError`.
Run: `docker compose exec -T backend python -m pytest tests/test_ti_task.py tests/test_ti_hooks.py -q`

- [ ] **Step 4: Implement** `backend/app/enrichment/hooks.py`:

```python
"""Auto-enqueue enrichment after commits that create/change IOCs or artifacts."""
import logging

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.core.config import settings

logger = logging.getLogger(__name__)
_KEY = "ti_pending"


def _delay(tenant_id, raw_type, value, tlp, force):
    from app.tasks.enrichment import enrich_indicator  # late import: worker import cycle
    enrich_indicator.delay(tenant_id, raw_type, value, tlp, force)


def enqueue(tenant_id, raw_type, value, tlp, force=False) -> None:
    if not settings.ENRICHMENT_ENABLED:
        return
    try:
        _delay(tenant_id, raw_type, value, tlp, force)
    except Exception:
        logger.exception("failed to enqueue enrichment for tenant %s", tenant_id)


def _changed(obj, attrs) -> bool:
    st = inspect(obj)
    return any(st.attrs[a].history.has_changes() for a in attrs)


@event.listens_for(Session, "after_flush")
def _collect(session, flush_context):
    from app.models.artifact import Artifact
    from app.models.ioc import IOC
    pending = session.info.setdefault(_KEY, {})
    for obj in list(session.new) + list(session.dirty):
        is_new = obj in session.new
        if isinstance(obj, IOC) and (is_new or _changed(obj, ("value", "ioc_type", "tlp"))):
            pending[("ioc", id(obj))] = (obj.tenant_id, obj.ioc_type, obj.value, obj.tlp)
        elif isinstance(obj, Artifact) and (is_new or _changed(obj, ("value", "artifact_type"))):
            atype = getattr(obj.artifact_type, "value", obj.artifact_type)
            pending[("art", id(obj))] = (obj.tenant_id, atype, obj.value, None)


@event.listens_for(Session, "after_commit")
def _flush_queue(session):
    for tenant_id, raw_type, value, tlp in session.info.pop(_KEY, {}).values():
        enqueue(tenant_id, raw_type, value, tlp)


@event.listens_for(Session, "after_rollback")
def _clear(session):
    session.info.pop(_KEY, None)
```

> Note for the implementer: `after_flush` sees `session.dirty` objects with their history still intact, because history is reset only after the flush completes. Verify this in the hook test; if history turns out to be cleared, switch the listener to `before_flush`, keeping the same body. In the test, the conftest recorder receives `_delay(tenant_id, raw_type, value, tlp, force)` positionally as `((tid, type, value, tlp, False), {})`.

`backend/app/tasks/enrichment.py`:

```python
"""Celery task: run enrichment sources for one indicator, cache results, respect limits."""
import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select

from app.ai.errors import scrub
from app.db.session import AsyncSessionLocal, engine
from app.enrichment.config import SOURCE_KEY, load_settings, runnable_sources
from app.enrichment.indicators import normalise, should_skip, tlp_allows
from app.enrichment.ratelimit import RateLimiter, limits_for
from app.enrichment.sources import REGISTRY
from app.models.enrichment_result import EnrichmentResult
from app.worker import celery_app

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 10
_LOG = "ti_lookup tenant=%s source=%s type=%s outcome=%s duration_ms=%d"
_DECRYPT_MSG = "credentials could not be decrypted — re-enter in Integrations"


async def upsert_result(db, tenant_id, itype, value, source, **fields):
    row = (await db.execute(select(EnrichmentResult).where(
        EnrichmentResult.tenant_id == tenant_id, EnrichmentResult.indicator_type == itype,
        EnrichmentResult.indicator_value == value, EnrichmentResult.source == source))).scalars().first()
    if row is None:
        row = EnrichmentResult(tenant_id=tenant_id, indicator_type=itype, indicator_value=value, source=source)
        db.add(row)
    for k, v in fields.items():
        setattr(row, k, v)
    row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return row


def _retry(tenant_id, itype, value, source, attempt, countdown):
    enrich_indicator.apply_async((tenant_id, itype, value, "white", True),
                                 {"only": [source], "attempt": attempt}, countdown=countdown)


async def run_enrichment(db, tenant_id, itype, value, *, sources: Optional[List[str]] = None, force=False,
                         attempt=0, limiter=None, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    s = await load_settings(db, tenant_id)
    limiter = limiter or RateLimiter()
    secrets_ = list(s.keys.values())
    runnable = [n for n in runnable_sources(s) if n in REGISTRY and itype in REGISTRY[n].TYPES]
    if sources is not None:
        runnable = [n for n in runnable if n in sources]
    out = {}
    if s.key_error:
        for name, key in SOURCE_KEY.items():
            if s.sources.get(name, True) and (sources is None or name in sources):
                await upsert_result(db, tenant_id, itype, value, name, status="error", error=_DECRYPT_MSG)
                out[name] = "error"
    skip = should_skip(itype, value, s.internal_domains)
    fresh_after = now - timedelta(hours=s.cache_ttl_hours)
    for name in runnable:
        if skip:
            await upsert_result(db, tenant_id, itype, value, name, status="skipped", verdict=None, error=None)
            out[name] = "skipped"
            continue
        if not force:
            row = (await db.execute(select(EnrichmentResult).where(
                EnrichmentResult.tenant_id == tenant_id, EnrichmentResult.indicator_type == itype,
                EnrichmentResult.indicator_value == value, EnrichmentResult.source == name))).scalars().first()
            if row and row.status in ("ok", "not_found") and row.fetched_at and row.fetched_at > fresh_after:
                out[name] = row.status
                continue
        wait = await limiter.acquire(tenant_id, name, limits_for(name, s))
        if wait is not None:
            if wait >= 3600:
                err = "VirusTotal daily quota reached" if name == "virustotal" else f"{name} quota reached"
                await upsert_result(db, tenant_id, itype, value, name, status="rate_limited", error=err)
                out[name] = "rate_limited"
            elif attempt >= MAX_ATTEMPTS:
                await upsert_result(db, tenant_id, itype, value, name, status="rate_limited", error=f"{name} rate limit")
                out[name] = "rate_limited"
            else:
                await upsert_result(db, tenant_id, itype, value, name, status="pending", error=None)
                _retry(tenant_id, itype, value, name, attempt + 1, wait)
                out[name] = "pending"
            logger.info(_LOG, tenant_id, name, itype, out[name], 0)
            continue
        start = time.monotonic()
        key = s.keys.get(SOURCE_KEY.get(name, ""), None)
        try:
            res = await REGISTRY[name].lookup(itype, value, key)
        except Exception as e:  # a source bug must never block the others
            res = None
            err = f"{name} failed ({type(e).__name__})"
        if res is None:
            await upsert_result(db, tenant_id, itype, value, name, status="error", error=scrub(err, secrets_))
            out[name] = "error"
        else:
            await upsert_result(db, tenant_id, itype, value, name, status=res.status, verdict=res.verdict,
                                score=res.score, summary=res.summary, link=res.link,
                                error=scrub(res.error, secrets_) if res.error else None,
                                fetched_at=now if res.status in ("ok", "not_found") else None)
            out[name] = res.status
        logger.info(_LOG, tenant_id, name, itype, out[name], int((time.monotonic() - start) * 1000))
    return out


class _RedisLock:
    def __init__(self):
        import redis.asyncio as aioredis

        from app.core.config import settings
        self.r = aioredis.from_url(settings.REDIS_URL)

    async def acquire(self, key: str) -> bool:
        return bool(await self.r.set(key, "1", nx=True, ex=60))


def _lock():
    return _RedisLock()


async def _entry(tenant_id, raw_type, value, tlp, force, only=None, attempt=0):
    norm = normalise(raw_type, value)
    if norm is None:
        return
    itype, v = norm
    async with AsyncSessionLocal() as db:
        if not force:
            s = await load_settings(db, tenant_id)
            if not tlp_allows(tlp or s.artifact_tlp, s.auto_max_tlp):
                return
            if not await _lock().acquire(f"ti:lock:{tenant_id}:{itype}:{hashlib.sha256(v.encode()).hexdigest()}"):
                return
        await run_enrichment(db, tenant_id, itype, v, sources=only, force=force, attempt=attempt)


@celery_app.task(acks_late=True)
def enrich_indicator(tenant_id, raw_type, value, tlp, force=False, only=None, attempt=0):
    async def main():
        try:
            await _entry(tenant_id, raw_type, value, tlp, force, only, attempt)
        finally:
            # asyncio.run() makes a new loop each call; pooled connections from the old loop must go.
            await engine.dispose()
    asyncio.run(main())
```

Two details to keep in mind:
- `test_dedupe_lock` expects a second automatic call for the same value to be ignored. Manual runs (`force=True`) skip the lock entirely, so a user can always re-run.
- `test_entry_tlp_gating_and_artifact_default` patches `load_settings` on the task module, which is why `_entry` imports and calls `load_settings` from the module namespace.

- [ ] **Step 5: Run the task and hook tests, then the full suite.** Expect PASS. Then restart the containers: `docker restart case_management-backend-1 case_management-worker-1`. Then check that the worker registered the task: `docker compose logs --tail=50 worker | grep -i enrich_indicator`. Expected: the task appears in the worker's `[tasks]` list.

- [ ] **Step 6: Commit**

```bash
git add backend/app/tasks/enrichment.py backend/app/enrichment/hooks.py backend/tests/conftest.py \
  backend/tests/test_ti_task.py backend/tests/test_ti_hooks.py backend/app/worker.py backend/app/core/config.py backend/app/main.py
git commit -m "feat(ti): enrichment task with cache, limits and dedupe; auto-enqueue on IOC/artifact changes"
```

---

### Task 7: API (config, results, run) and IOC list verdict

**Files:**
- Create: `backend/app/schemas/enrichment.py`, `backend/app/api/v1/enrichment.py`
- Modify:
  - `backend/app/api/api.py`: `api_router.include_router(enrichment.router, prefix="/enrichment", tags=["enrichment"])`, plus the import.
  - `backend/app/api/v1/iocs.py` (`read_iocs`) and `backend/app/schemas/ioc.py`: add `enrichment_verdict: Optional[str] = None` to the IOC response schema.
- Test: `backend/tests/test_ti_api.py`

**Interfaces:**
- Consumes:
  - from Task 2: `load_settings`, `decrypt_keys`, `DEFAULTS`, `KEY_FIELDS`, `TenantEnrichmentConfig`, `EnrichmentResult`
  - from Task 1: `normalise`, `suggest`, `TLP_ORDER`
  - from Task 6: `run_enrichment`, `upsert_result`, and `hooks.enqueue`. `enqueue` is what `/run` uses; tests patch `hooks._delay`.
  - from Tasks 3–4: `REGISTRY`, for `/config/test`
  - `app.utils.audit.create_audit_log` (find the import path with `grep -rn "def create_audit_log" backend/app`)
- Produces, all under `/api/v1/enrichment`:

| Method and path | Role | Body | Response |
|---|---|---|---|
| `GET /config` | admin | — | `{auto_max_tlp, artifact_tlp, cache_ttl_hours, sources, vt_per_minute, vt_per_day, internal_domains, credentials_set: {virustotal: bool, abusech: bool}}` |
| `PUT /config` | admin | `EnrichmentConfigIn`. `virustotal_api_key` and `abusech_auth_key` are optional; blank keeps the stored value. `clear_virustotal` and `clear_abusech` are bools. | same as GET |
| `DELETE /config` | admin | — | 204 |
| `POST /config/test` | admin | — | `{virustotal: {ok, message}, abusech: {ok, message}}` |
| `GET /{kind}/{id}` (`kind` is `ioc` or `artifact`) | any member | — | `{enrichable, indicator_type, indicator_value, effective_tlp, auto: bool, results: [{source, status, verdict, score, summary, link, error, fetched_at, stalled}], suggestion}` |
| `POST /{kind}/{id}/run` | analyst+ | `{confirm: bool = False}` | 202 `{results: [...]}` |

`GET /{kind}/{id}`: `suggestion` is null for artifacts. `stalled` is true when `status == "pending"` and `updated_at` is more than 10 minutes old.

`POST /{kind}/{id}/run` errors:
- 409 when the effective TLP is amber or red and `confirm` is false. `detail` is `"This sends the value to external threat-intel services (TLP:{tlp}). Confirm to continue."`.
- 422 `"This value can't be enriched"` when the value is not enrichable.
- 404 when the record belongs to another tenant or doesn't exist.

Behaviour of `/run`:
- It upserts `pending` rows for the runnable sources, then calls `hooks.enqueue(tid, raw_type, value, tlp, force=True)`.
- Artifacts pass the tenant's `artifact_tlp` as `tlp`.

`EnrichmentConfigIn` validation:
- `auto_max_tlp` must be in {none, white, green, amber, red}.
- `artifact_tlp` must be in {white, green, amber, red}.
- `cache_ttl_hours` is 1–720, `vt_per_minute` 1–1000, `vt_per_day` 1–1,000,000.
- `sources` keys must be a subset of `SOURCES`, with bool values.
- `internal_domains` is at most 50 entries, each a valid domain via `normalise("domain", d)`; anything else is a 422.

Audit and logging:
- PUT and DELETE call `create_audit_log(..., tenant_id=tid, entity_type="enrichment_config", changes={field names only})`.
- Save, test and delete each log exactly one INFO line: `ti_config <op> tenant=%s outcome=%s`.

`/config/test`:
- It loads the stored keys, then calls `REGISTRY["virustotal"].lookup("ip", "8.8.8.8", key)` and `REGISTRY["urlhaus"].lookup("ip", "8.8.8.8", key)`. Without a key the source reports `{ok: false, message: "No key set"}`.
- Any status other than `error` or `rate_limited` counts as ok. A `rate_limited` status gives the message "quota reached".
- Error messages are scrubbed with the keys.

IOC list (`read_iocs`):
- After loading the page of IOCs, run **one** query over `enrichment_results` for (tenant, type, value) in that page's normalised pairs. Set `enrichment_verdict` to the worst `ok` verdict per IOC, ranked malicious > suspicious > harmless > unknown.
- Use `normalise(ioc.ioc_type, ioc.value)` to build the pairs, and skip IOCs whose value is not enrichable.

- [ ] **Step 1: Write failing test** `backend/tests/test_ti_api.py`. Follow the scenario pattern in `backend/tests/test_ai_api.py`: one temporary tenant, users overridden through `app.dependency_overrides` for `get_current_active_user`, `get_effective_tenant_id`, `require_admin` and `require_analyst_or_above`, an in-process `httpx.ASGITransport`, and cleanup by exact ids. The checks:

```python
# a) GET /config with no row -> defaults, credentials_set both False
# b) PUT /config {virustotal_api_key:"vt-SECRET-1", auto_max_tlp:"amber", internal_domains:["corp.local"]} -> 200;
#    response and DB row never contain "vt-SECRET-1" in plaintext; credentials_set.virustotal True; audit row exists
#    with changes listing field names only (no key value)
# c) PUT with blank virustotal_api_key keeps the key; PUT {clear_virustotal:true} removes it
# d) PUT invalid: auto_max_tlp="purple", cache_ttl_hours=0, vt_per_minute=0, internal_domains=["not a domain"],
#    sources={"shodan": True} -> each 422
# e) role: analyst PUT /config -> 403 (require_admin override raises 403 when role != admin)
# f) create IOC (tlp amber, domain evil.example) and Artifact (domain art.example) directly in DB for the tenant
#    GET /enrichment/ioc/{id} -> enrichable True, results [] , suggestion None, effective_tlp "amber"
# g) POST /enrichment/ioc/{id}/run without confirm -> 409 with "TLP:amber" in detail;
#    with {"confirm": true} -> 202, results all status "pending" for runnable sources (rdap, crtsh with no keys),
#    and the conftest recorder got ((tid, "domain", "evil.example", "amber", True), {})
# h) seed EnrichmentResult rows (virustotal ok malicious summary {"malicious": 20}, threatfox ok malicious
#    summary {"malware_printable": "Emotet"}) for (tid, "domain", "evil.example"); GET -> suggestion
#    {"threat_level": "critical", "tags": ["malware:emotet"]}
# i) artifact GET -> suggestion None; run on artifact (artifact_tlp default amber) without confirm -> 409
# j) IOC with ioc_type "email" -> GET enrichable False; run -> 422
# k) cross-tenant: an IOC id from another temp tenant -> GET 404 and run 404
# l) GET /iocs/ -> the seeded IOC has enrichment_verdict "malicious"; an IOC without results has None
# m) stalled: a pending row with updated_at = now - 11 min -> GET shows stalled True
# n) POST /config/test with monkeypatched REGISTRY lookups (virustotal returns LookupResult(status="error",
#    error="invalid API key"), urlhaus returns ok) -> {virustotal:{ok:false,message:"invalid API key"},
#    abusech:{ok:true,...}}
# o) logging: exactly one "ti_config save|test|delete tenant=<tid> outcome=..." line per op; no key text in any log
```

Each lettered check above becomes an `assert` block inside one `_scenario` coroutine, exactly as `test_ai_api.py` does. Seed rows by id with `AsyncSessionLocal`. In `finally`, delete:
- the `EnrichmentResult` rows for the temp tenants (by `tenant_id IN ids`)
- the IOCs and artifacts created
- the config rows
- the audit rows for those tenants
- the users and tenants, by id

- [ ] **Step 2: Run.** Expect FAIL with 404 on `/api/v1/enrichment/config`.

- [ ] **Step 3: Implement** `backend/app/schemas/enrichment.py`:

```python
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.enrichment.config import SOURCES
from app.enrichment.indicators import normalise

_AUTO = {"none", "white", "green", "amber", "red"}
_TLP = {"white", "green", "amber", "red"}


class EnrichmentConfigIn(BaseModel):
    auto_max_tlp: str = "green"
    artifact_tlp: str = "amber"
    cache_ttl_hours: int = Field(24, ge=1, le=720)
    sources: Dict[str, bool] = Field(default_factory=lambda: {s: True for s in SOURCES})
    vt_per_minute: int = Field(4, ge=1, le=1000)
    vt_per_day: int = Field(500, ge=1, le=1_000_000)
    internal_domains: List[str] = Field(default_factory=list, max_length=50)
    virustotal_api_key: Optional[str] = Field(None, max_length=200)
    abusech_auth_key: Optional[str] = Field(None, max_length=200)
    clear_virustotal: bool = False
    clear_abusech: bool = False

    @field_validator("auto_max_tlp")
    @classmethod
    def _auto(cls, v):
        if v not in _AUTO:
            raise ValueError("auto_max_tlp must be one of none, white, green, amber, red")
        return v

    @field_validator("artifact_tlp")
    @classmethod
    def _tlp(cls, v):
        if v not in _TLP:
            raise ValueError("artifact_tlp must be one of white, green, amber, red")
        return v

    @field_validator("sources")
    @classmethod
    def _sources(cls, v):
        unknown = set(v) - set(SOURCES)
        if unknown:
            raise ValueError(f"unknown sources: {', '.join(sorted(unknown))}")
        return v

    @field_validator("internal_domains")
    @classmethod
    def _domains(cls, v):
        out = []
        for d in v:
            n = normalise("domain", d)
            if n is None:
                raise ValueError(f"not a domain: {d[:60]}")
            out.append(n[1])
        return out


class RunIn(BaseModel):
    confirm: bool = False
```

`backend/app/api/v1/enrichment.py`: write the router to the interface table above. The required structure:

```python
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.errors import scrub
from app.api import deps
from app.enrichment import hooks
from app.enrichment.config import DEFAULTS, KEY_FIELDS, SOURCE_KEY, decrypt_keys, load_settings, runnable_sources
from app.enrichment.indicators import normalise
from app.enrichment.sources import REGISTRY
from app.enrichment.suggest import suggest
from app.models.artifact import Artifact
from app.models.enrichment_result import EnrichmentResult
from app.models.ioc import IOC
from app.models.tenant_enrichment_config import TenantEnrichmentConfig
from app.models.user import User
from app.schemas.enrichment import EnrichmentConfigIn, RunIn
from app.tasks.enrichment import upsert_result
from app.utils.crypto import encrypt
# from <found path> import create_audit_log

router = APIRouter()
logger = logging.getLogger(__name__)
_CONFIRM = "This sends the value to external threat-intel services (TLP:{tlp}). Confirm to continue."


def _log(op, tid, outcome):
    logger.info("ti_config %s tenant=%s outcome=%s", op, tid, outcome)


def _out(row, keys) -> dict:
    base = {k: (getattr(row, k) if row is not None and getattr(row, k) is not None else DEFAULTS[k])
            for k in ("auto_max_tlp", "artifact_tlp", "cache_ttl_hours", "vt_per_minute", "vt_per_day")}
    base["sources"] = {**DEFAULTS["sources"], **((row.sources if row else None) or {})}
    base["internal_domains"] = list((row.internal_domains if row else None) or [])
    base["credentials_set"] = {"virustotal": bool(keys.get("virustotal_api_key")),
                               "abusech": bool(keys.get("abusech_auth_key"))}
    return base


async def _target(db, kind, obj_id, tid):
    model = IOC if kind == "ioc" else Artifact if kind == "artifact" else None
    if model is None:
        raise HTTPException(status_code=404, detail="Not found")
    obj = (await db.execute(select(model).where(model.id == obj_id, model.tenant_id == tid))).scalars().first()
    if obj is None:
        raise HTTPException(status_code=404, detail="Not found")
    raw_type = obj.ioc_type if kind == "ioc" else getattr(obj.artifact_type, "value", obj.artifact_type)
    return obj, raw_type
```

Then write the six handlers to the table. Some rules apply to every handler:
- Every handler takes `tenant_id: int = Depends(deps.get_effective_tenant_id)` and the role dependency named in the table.
- The `effective_tlp` is `obj.tlp` for IOCs and `settings.artifact_tlp` for artifacts.
- `/run` writes pending rows by calling `upsert_result(db, tid, itype, v, name, status="pending", error=None)` for each source in `runnable_sources(s)` that supports `itype`. It then calls `hooks.enqueue(tid, raw_type, obj.value, effective_tlp, force=True)` and returns `Response(status_code=202, ...)`, with the JSON body built via `json.dumps` of the rows, using ISO datetimes.
- The `suggestion` field calls `suggest(obj.threat_level, obj.tags or [], [r.__dict__-like dicts with source/status/verdict/summary])`.

Also add the `enrichment_verdict` field to the IOC list response in `iocs.py`:

```python
_RANK = {"malicious": 3, "suspicious": 2, "harmless": 1, "unknown": 0}
pairs = {}
for ioc in iocs:
    n = normalise(ioc.ioc_type, ioc.value)
    if n:
        pairs[ioc.id] = n
verdicts = {}
if pairs:
    rows = (await db.execute(select(EnrichmentResult.indicator_type, EnrichmentResult.indicator_value,
                                    EnrichmentResult.verdict).where(
        EnrichmentResult.tenant_id == tenant_id, EnrichmentResult.status == "ok",
        EnrichmentResult.indicator_value.in_({v for _, v in pairs.values()})))).all()
    for t, v, verdict in rows:
        if verdict and _RANK.get(verdict, -1) > _RANK.get(verdicts.get((t, v)), -1):
            verdicts[(t, v)] = verdict
return [{**ioc_schema.IOC.model_validate(i).model_dump(), "enrichment_verdict": verdicts.get(pairs.get(i.id))}
        for i in iocs]
```

Check how `read_iocs` currently builds its response, and keep its filters and pagination unchanged.

- [ ] **Step 4: Run the API test, then the full suite.** Expect PASS. Then restart the backend and worker.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/enrichment.py backend/app/api/v1/enrichment.py backend/app/api/api.py \
  backend/app/api/v1/iocs.py backend/app/schemas/ioc.py backend/tests/test_ti_api.py
git commit -m "feat(ti): enrichment config, results and run API; IOC list verdict"
```

---

### Task 8: Frontend — Threat-intel card, enrichment panel, suggestion chip, IOC list dot

**Files:**
- Create: `frontend/src/features/integrations/ThreatIntelCard.tsx`, `frontend/src/features/enrichment/EnrichmentPanel.tsx`
- Modify:
  - `frontend/src/features/integrations/Integrations.tsx`: render `<ThreatIntelCard key={me?.active_tenant_id ?? 'none'} />` directly below `<AIProviderCard .../>`, inside the same admin branch.
  - `frontend/src/features/iocs/IOCList.tsx`: add a verdict dot column and an expandable row that renders `<EnrichmentPanel kind="ioc" id={ioc.id} ... />`.
  - `frontend/src/features/cases/CaseDetail.tsx`: in the artifacts tab, add a per-artifact "Intel" toggle that renders `<EnrichmentPanel kind="artifact" id={artifact.id} />`.
  - `frontend/src/types/index.ts`: add the types below.

**Interfaces:**
- Consumes the Task 7 API exactly. Read `backend/app/api/v1/enrichment.py` and `backend/app/schemas/enrichment.py` for the shapes.
- Types:

```ts
export type EnrichmentResultRow = {
  source: 'virustotal' | 'urlhaus' | 'threatfox' | 'rdap' | 'crtsh';
  status: 'pending' | 'ok' | 'not_found' | 'error' | 'rate_limited' | 'skipped';
  verdict: 'malicious' | 'suspicious' | 'harmless' | 'unknown' | null;
  score: string | null; summary: Record<string, unknown>; link: string | null;
  error: string | null; fetched_at: string | null; stalled: boolean;
};
export type EnrichmentView = {
  enrichable: boolean; indicator_type: string | null; indicator_value: string | null;
  effective_tlp: string; auto: boolean; results: EnrichmentResultRow[];
  suggestion: { threat_level: string | null; tags: string[] } | null;
};
```

`IOC` gains `enrichment_verdict?: string | null`.

**`EnrichmentPanel` props and behaviour.** Props: `{ kind: 'ioc' | 'artifact'; id: number; canRun: boolean; ioc?: IOC }`.

Data:
- `useQuery(['enrichment', kind, id], GET /enrichment/{kind}/{id}, { refetchInterval: (q) => q.state.data?.results.some(r => r.status === 'pending' && !r.stalled) ? 3000 : false })`.

Rendering:
- If `!enrichable`, show a muted "Not enrichable (type)" line and nothing else.
- Otherwise show one row per result:
  - source label, using the names VirusTotal, URLhaus, ThreatFox, RDAP and crt.sh;
  - a verdict badge: malicious → red, suspicious → amber, harmless → green, unknown → zinc;
  - `score` in `.num`;
  - up to 3 summary facts as `key: value`, skipping arrays longer than 3;
  - a `link` opening in a new tab with `rel="noopener noreferrer"`;
  - `fetched_at` shown as a relative time.
- Status texts:

  | Status | Text |
  |---|---|
  | `pending` | "Checking…" |
  | `pending` with `stalled` | "Stalled — re-check" |
  | `not_found` | "No record" |
  | `skipped` | "Not sent (private or internal)" |
  | `rate_limited` | the `error` text, or "Quota reached" |
  | `error` | the `error` text |

- With no results, show "Not checked yet". When `auto` is false, add "(TLP:{tlp} — run manually)".

Running a lookup:
- Show an Enrich button when there are no results and Re-check otherwise, rendered only when `canRun` is true.
- Clicking it calls `POST /enrichment/{kind}/{id}/run`.
- On a 409, show an inline confirm with the response `detail` and **Send** / **Cancel** buttons. Send retries with `{confirm: true}`. Use an inline confirm, not `window.confirm`.
- After 202, invalidate `['enrichment', kind, id]`.

Suggestion (only when `kind === 'ioc'` and `suggestion` is set):
- Render an amber chip: "Suggest: {threat_level?} {+tags}" with an **Apply** button.
- Apply sends `PUT /iocs/{id}` with `{threat_level: suggestion.threat_level ?? ioc.threat_level, tags: [...ioc.tags, ...suggestion.tags]}`. Check `backend/app/schemas/ioc.py::IOCUpdate`; it accepts both fields.
- Then invalidate `['iocs']` and `['enrichment', 'ioc', id]`.

Who can run (`canRun`): `currentUser.role` is `admin` or `analyst`, or the user `is_super_admin`. Read it the way IOCList already reads the current user. Viewers do not get the button.

**`ThreatIntelCard`.** Copy the structure and light-theme classes of `AIProviderCard.tsx`: `bg-white border border-zinc-200 p-5`, `label-mono`, `accent-600`.
- Fields:
  - VirusTotal API key and abuse.ch Auth-Key: `type=password autoComplete=off`, never pre-filled, with a "· set" hint and a "Clear" checkbox. A blank value is omitted from the PUT.
  - a toggle per source;
  - "Auto-enrich up to TLP", a select of none/white/green/amber/red;
  - "Treat artifacts as TLP", a select of white/green/amber/red;
  - cache hours, VT per minute and VT per day, as numbers;
  - internal domains, a textarea with one per line.
- Buttons:
  - **Save**: PUT. Show the detail of a 422, including the first `msg` when it is a pydantic array.
  - **Test keys**: POST `/config/test`, showing the result per source.
  - **Reset to defaults**: DELETE, with an inline confirm.
- Under the abuse.ch field, a one-line note: "Free key from auth.abuse.ch — enables URLhaus and ThreatFox."

**IOC list dot.** A small dot before the value. Colours: `malicious` red-600, `suspicious` amber-500, `harmless` green-600; nothing when null. Add `title={enrichment_verdict}` for accessibility.

- [ ] **Step 1:** Add the types to `frontend/src/types/index.ts`.
- [ ] **Step 2:** Write `EnrichmentPanel.tsx` to the behaviour above.
- [ ] **Step 3:** Write `ThreatIntelCard.tsx` to the behaviour above.
- [ ] **Step 4:** Wire the panel into `Integrations.tsx`, `IOCList.tsx` and `CaseDetail.tsx` as described in Files.
- [ ] **Step 5: Verify.** `cd frontend && npm run build && npx eslint src/features/enrichment src/features/integrations/ThreatIntelCard.tsx src/features/iocs/IOCList.tsx`. Expected: the build passes, with no new lint errors in these files. Then rebuild the container with `docker compose up -d --no-deps --build frontend` and check `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/integrations`, which should print 200. If the frontend uses another port, find it with `docker compose port frontend 80`.
- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(ti): threat-intel settings card, enrichment panel, suggestion chip and IOC verdict dot"
```

---

### Task 9: Docs and end-to-end smoke

**Files:**
- Modify: `docs/configuration.md` (add a "Threat-intel enrichment" section), `docs/features.md` (add a short entry)

- [ ] **Step 1: Docs.** `docs/configuration.md` gets a "Threat-intel enrichment" section covering:
  - the sources and the key each needs (VirusTotal key; free abuse.ch Auth-Key from auth.abuse.ch for URLhaus and ThreatFox; RDAP and crt.sh need none);
  - the TLP rules (automatic lookups up to `auto_max_tlp`, default green; artifacts treated as `artifact_tlp`, default amber; manual runs at amber/red require confirmation);
  - what is never sent out (private/reserved IPs, `internal_domains`);
  - the cache TTL;
  - the rate limits and the VirusTotal free-tier numbers;
  - the `ENRICHMENT_ENABLED` kill switch;
  - that only lookups are made (no uploads);
  - that values and keys are never logged.

  `docs/features.md` gets a short "Threat-intel enrichment" entry.

- [ ] **Step 2: Smoke test, with no real network.** Inside the backend container, run a script that:
  1. Creates a temp tenant `wf-test-<hex>-ti` and an IOC (`domain`, `evil.example`, tlp `green`) for it.
  2. Monkeypatches `REGISTRY` in `app.tasks.enrichment` with fake sources: rdap returns ok/unknown; crtsh returns ok/suspicious with summary `{"cert_count": 1, "first_cert": "<today>"}`.
  3. Calls `await run_enrichment(db, tid, "domain", "evil.example")` and prints the returned statuses.
  4. Calls the API in-process with `GET /enrichment/ioc/{id}` (dependency overrides as in the tests) and prints the `suggestion`, which should be `{"threat_level": "medium", ...}` because the IOC's level is lower than medium. If the IOC is created with `threat_level="low"`, the suggestion is `medium`.
  5. Deletes the results, IOC and tenant by id, and prints the `wf-test-%` tenant count, which must be 0.

  Record the output in the report.
- [ ] **Step 3: Verify the worker task is registered.** `docker compose exec -T worker celery -A app.worker inspect registered 2>/dev/null | grep enrich_indicator`. If the worker service name differs, find it with `docker compose ps`.
- [ ] **Step 4: Commit**

```bash
git add docs/configuration.md docs/features.md
git commit -m "docs(ti): threat-intel enrichment configuration and feature notes"
```
