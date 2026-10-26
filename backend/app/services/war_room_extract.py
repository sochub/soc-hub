"""Pure text extraction for SOC-bot war-room suggestions: artifacts (indicators,
hostnames, usernames) and incident-milestone times from a Slack message.
No DB, no network."""
import html
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.utils.copilot_heuristics import detect_indicators

# ---- Slack normalisation --------------------------------------------------

_SLACK_REF = re.compile(r"<[@#!][^>]*>")                  # <@U1> <#C1|ir> <!here>
_SLACK_LINK = re.compile(r"<([^|<>\s]+)(?:\|[^<>]*)?>")   # <url|label> / <url>


def normalize_slack_text(text: str) -> str:
    text = _SLACK_REF.sub(" ", text or "")
    text = _SLACK_LINK.sub(lambda m: re.sub(r"^mailto:", "", m.group(1)), text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


@dataclass(frozen=True)
class Candidate:
    kind: str          # "artifact" | "milestone"
    payload: dict      # {"type","value"} or {"key","at"}
    value_key: str


def _artifact(type_: str, value: str) -> Candidate:
    return Candidate("artifact", {"type": type_, "value": value}, f"{type_}:{value.lower()}")


# ---- hostnames / usernames ------------------------------------------------

_URL_OR_EMAIL = re.compile(r"https?://\S+|[\w.%+-]+@[\w.-]+\.\w+", re.IGNORECASE)
# FQDN-like token ending in an internal suffix (dc01.corp, srv.eu.lan)
_INTERNAL_FQDN = re.compile(r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:local|corp|internal|lan|ad)\b", re.IGNORECASE)
# letters-hyphen-digits (WS-FIN-07): >=2 letters first, digit-only last group
_NAME_NUM = re.compile(r"\b[A-Za-z]{2,}(?:-[A-Za-z0-9]+)*-\d+\b")
# Ticket/standard prefixes that look like NAME-123 but are not machines
_NOT_HOST_PREFIX = {"cve", "sha", "md", "iso", "utf", "tls", "ssl", "rfc", "inc", "task", "ticket", "case", "kb", "ir", "soc", "http"}
_HOST_CUE = re.compile(r"\b(?:host|hostname|machine|endpoint)[:\s]+([A-Za-z0-9][\w.-]*)", re.IGNORECASE)

_DOMAIN_USER = re.compile(r"\b[A-Za-z][\w-]*\\[A-Za-z0-9][\w.-]*")   # ACME\jdoe
_USER_CUE = re.compile(r"\b(?:user(?:name)?|account|login)[:\s]+([A-Za-z0-9][\w.-]*)", re.IGNORECASE)
# "reset password for jdoe", "disable the account for jdoe" (up to 2 words between)
_USER_VERB = re.compile(r"\b(?:reset|disabl|lock|suspend)\w*(?:\s+\w+){0,2}?\s+for\s+([A-Za-z0-9][\w.-]*)", re.IGNORECASE)

# Words that follow a cue but are not names ("host is down", "user logged in")
_FILLER = {"is", "was", "are", "has", "had", "the", "a", "an", "and", "to", "of", "in", "on", "at", "that", "this",
           "with", "from", "for", "it", "its", "appears", "seems", "logged", "locked", "name", "not", "also", "just"}


def _clean_token(tok: str) -> str:
    return tok.rstrip(".-_")


def _cue_values(pattern: re.Pattern, text: str) -> list[str]:
    out = []
    for m in pattern.finditer(text):
        tok = _clean_token(m.group(1))
        if len(tok) >= 2 and tok.lower() not in _FILLER and not tok.replace(".", "").isdigit():
            out.append(tok)
    return out


def _hostnames(text: str) -> list[str]:
    found = [m.group(0) for m in _INTERNAL_FQDN.finditer(text)]
    found += [m.group(0) for m in _NAME_NUM.finditer(text)
              if m.group(0).split("-")[0].lower() not in _NOT_HOST_PREFIX]
    found += _cue_values(_HOST_CUE, text)
    return found


def _usernames(text: str) -> list[str]:
    found = [m.group(0) for m in _DOMAIN_USER.finditer(text)]
    found += _cue_values(_USER_CUE, text) + _cue_values(_USER_VERB, text)
    return found


# ---- milestones ------------------------------------------------------------

_KEYWORD_KEY = {"first seen": "first_seen", "detected": "detected", "contained": "contained",
                "isolated": "contained", "recovered": "recovered", "restored": "recovered"}
# keyword, <=15 non-digit chars, optional "yesterday", time (14:32 / 2:32pm), optional UTC/Z
_MILESTONE = re.compile(
    r"\b(?P<kw>first seen|detected|contained|isolated|recovered|restored)\b"
    r"[^\d\n]{0,15}?"
    r"(?P<yest>yesterday\s+(?:at\s+)?)?"
    r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>am|pm)?\s*(?P<z>utc|z)?(?![\w])",
    re.IGNORECASE,
)
_UTC = ZoneInfo("UTC")


def _milestone_at(m: re.Match, tz: ZoneInfo, message_at: datetime) -> str | None:
    hour, minute, ap = int(m["h"]), int(m["m"]), (m["ap"] or "").lower()
    if minute > 59 or (ap and not 1 <= hour <= 12) or hour > 23:
        return None
    if ap:
        hour = hour % 12 + (12 if ap == "pm" else 0)
    zone = _UTC if m["z"] else tz
    if message_at.tzinfo is None:
        message_at = message_at.replace(tzinfo=_UTC)
    now = message_at.astimezone(zone)
    day = now.date() - timedelta(days=1 if m["yest"] else 0)
    at = datetime.combine(day, time(hour, minute), tzinfo=zone)
    if not m["yest"] and at > now:   # a bare time cannot be in the future
        at = datetime.combine(day - timedelta(days=1), time(hour, minute), tzinfo=zone)
    return at.isoformat()


# ---- entry point -------------------------------------------------------------

def extract_candidates(text: str, *, tz: ZoneInfo, message_at: datetime,
                       exclude_emails: set[str] = frozenset()) -> list[Candidate]:
    text = normalize_slack_text(text)
    excluded = {e.lower() for e in exclude_emails}
    indicators = detect_indicators(text, limit=100)
    # hostname/username scans must not read inside URLs or emails
    bare = _URL_OR_EMAIL.sub(" ", text)
    hosts = _hostnames(bare)
    host_set = {h.lower() for h in hosts}
    indicator_values = {i["value"].lower() for i in indicators}

    out: dict[str, Candidate] = {}

    def add(c: Candidate) -> None:
        out.setdefault(c.value_key, c)

    for h in hosts:
        # a cue token that is already a public indicator (e.g. "host evil.com") stays an indicator
        if h.lower() not in indicator_values or _INTERNAL_FQDN.fullmatch(h):
            add(_artifact("hostname", h))
    for i in indicators:
        v = i["value"]
        if i["artifact_type"] == "email" and v.lower() in excluded:
            continue
        if i["artifact_type"] == "domain" and any(v.lower() == h or h.endswith("." + v.lower()) for h in host_set):
            continue
        add(_artifact(i["artifact_type"], v))
    for u in _usernames(bare):
        add(_artifact("username", u))

    for m in _MILESTONE.finditer(text):
        key = _KEYWORD_KEY[m["kw"].lower()]
        at = _milestone_at(m, tz, message_at)
        if at and key not in out:
            out[key] = Candidate("milestone", {"key": key, "at": at}, key)
    return list(out.values())
