"""Pure text extraction for SOC-bot war-room suggestions: artifacts (indicators,
hostnames, usernames) and incident-milestone times from a Slack message.
No DB, no network."""
import html
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.utils.copilot_heuristics import TLDS, detect_indicators

# ---- Slack normalisation --------------------------------------------------

_SLACK_REF = re.compile(r"<[@#!][^>]*>")                  # <@U1> <#C1|ir> <!here>
_SLACK_LINK = re.compile(r"<([^|<>\s]+)(?:\|([^<>]*))?>")  # <url|label> / <url>
_SCHEME = re.compile(r"^https?://", re.IGNORECASE)


def _autolinked(m: re.Match) -> Optional[str]:
    """The label of a link Slack made itself: a typed `evil.com` arrives as <http://evil.com|evil.com>.
    The person wrote a domain, not a URL. A real typed URL has no label or a different one."""
    label = (m.group(2) or "").strip()
    return label if label and _SCHEME.sub("", m.group(1)).rstrip("/").lower() == label.lower() else None


def normalize_slack_text(text: str) -> str:
    text = _SLACK_REF.sub(" ", text or "")
    text = _SLACK_LINK.sub(lambda m: _autolinked(m) or re.sub(r"^mailto:", "", m.group(1)), text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# Analysts defang indicators so nobody clicks them: hxxp://evil[.]com, 185[.]220[.]101[.]5, bob[at]evil[.]com
_REFANG = ((re.compile(r"\[\.\]|\(\.\)|\{\.\}|\[dot\]", re.IGNORECASE), "."), (re.compile(r"\[:\]"), ":"),
           (re.compile(r"\[@\]|\[at\]", re.IGNORECASE), "@"), (re.compile(r"hxxp(s?)://", re.IGNORECASE), r"http\1://"))
# Slack italics/bold/strike marks hugging a value (_bad.net_, *bad.net*); svc_backup keeps its underscore
_SLACK_MARKS = re.compile(r"(?<![A-Za-z0-9])[_*~]+|[_*~]+(?![A-Za-z0-9])")


def _refang(text: str) -> str:
    for rx, to in _REFANG:
        text = rx.sub(to, text)
    return _SLACK_MARKS.sub("", text)


# Input bounds: Slack allows 40k chars; war-room signal lives in the first few thousand.
MAX_RAW_CHARS = 16000
MAX_TEXT_CHARS = 4000
MAX_TOKEN_CHARS = 512


@dataclass(frozen=True)
class Candidate:
    kind: str          # "artifact" | "milestone"
    payload: dict      # {"type","value"} or {"key","at"}
    value_key: str


def _artifact(type_: str, value: str) -> Candidate:
    return Candidate("artifact", {"type": type_, "value": value}, f"{type_}:{value.lower()}")


# ---- hostnames / usernames ------------------------------------------------
# Precision over recall: every rule below exists to keep ordinary words and file
# names out of the suggestions. Token matching is procedural (split, then test
# each token), so cost stays linear in the (capped) input.

_URL_OR_EMAIL = re.compile(r"https?://\S+|[\w.%+-]+@[\w.-]+\.\w+", re.IGNORECASE)
_TOKEN_SPLIT = re.compile(r"[^\s,;()\[\]{}<>\"'`]+")
_LABEL = re.compile(r"[a-z0-9-]+")
_INTERNAL_SUFFIXES = {"local", "corp", "internal", "lan", "ad"}
# NAME-123 first groups that are products, protocols or ticket ids, not machines
_NOT_HOST_PREFIX = {"cve", "sha", "md", "md5", "iso", "utf", "tls", "ssl", "rfc", "inc", "task", "ticket", "case",
                    "kb", "ir", "soc", "http", "https", "tcp", "udp", "covid", "windows", "win", "python", "office",
                    "aes", "rsa", "des", "sql", "ipv", "ms", "ssh", "ftp", "dns"}
_HOST_CUE = re.compile(r"\b(?:host|hostname|machine|endpoint)(:\s*|\s+)([A-Za-z0-9][\w.-]*)", re.IGNORECASE)

# DOMAIN\user; the lookbehind rejects Windows paths (C:\Users\bob) and UNC shares (\\srv\share)
_DOMAIN_USER = re.compile(r"(?<![\\\w:])[A-Za-z][\w-]*\\[A-Za-z0-9][\w.-]*")
_NOT_DOMAIN = {"users", "windows", "program", "programdata", "system32", "temp", "appdata", "documents"}
_USER_CUE = re.compile(r"\b(?:user(?:name)?|account|login)(:\s*|\s+)([A-Za-z0-9][\w.-]*)", re.IGNORECASE)
# "reset password for jdoe", "disable the account for jdoe" (up to 2 words between)
_USER_VERB = re.compile(r"\b(?:reset|disabl|lock|suspend)\w*(?:\s+\w+){0,2}?\s+for(\s+)([A-Za-z0-9][\w.-]*)", re.IGNORECASE)

# Words that follow a cue but are not names ("host is down", "account takeover")
_FILLER = set("""is was are has had the a an and or to of in on at that this these those with from for it its appears
seems logged locked name not also just can cant could would should will may might be been being by as if so but
protection security learning takeover page example account user users username login logins host hostname machine
machines endpoint endpoints server servers system systems device devices service services password passwords
credentials session sessions activity details info information status issue issues alert alerts attempt attempts
failure failures compromise compromised access admin administrator policy policies agent agents detection response
management portal panel screen form field type types data file files""".split())
_ENGLISH_SUFFIXES = ("ing", "tion", "sion", "ity", "ness", "ment", "able", "ible", "ous")


def _identifier_like(tok: str) -> bool:
    low = tok.lower()
    if len(tok) < 2 or low in _FILLER or tok.replace(".", "").isdigit():
        return False
    if any(c in tok for c in "0123456789.-_"):
        return True
    return not low.endswith(_ENGLISH_SUFFIXES)


def _cue_values(pattern: re.Pattern, text: str) -> list[str]:
    out = []
    for m in pattern.finditer(text):
        tok = m.group(2).rstrip(".-_")
        # a bare word after a cue is usually prose ("the user clicked", "host down"): without the explicit
        # "user:" / "host:" colon the token must look like an identifier
        if _identifier_like(tok) and (":" in m.group(1) or any(c in tok for c in "0123456789._-")):
            out.append(tok)
    return out


def _is_internal_fqdn(tok: str) -> bool:
    labels = tok.lower().split(".")
    if len(labels) < 2 or labels[-1] not in _INTERNAL_SUFFIXES or not all(_LABEL.fullmatch(x) for x in labels):
        return False
    # report.ad / notes.local look like file names: need >=3 labels or a digit on the left
    return len(labels) >= 3 or any(c.isdigit() for c in labels[0])


def _is_name_num(tok: str) -> bool:
    groups = tok.split("-")
    first = groups[0]
    if len(groups) < 2 or not all(g.isalnum() and g.isascii() for g in groups) or not groups[-1].isdigit():
        return False
    if len(groups) == 2 and first.isalpha():
        return False  # JIRA-221 / PROJ-42 are ticket ids; "host SRV-01" is still read through the host cue
    letters = sum(c.isalpha() for c in first)
    if letters < 2 or first.lower() in _NOT_HOST_PREFIX:
        return False
    # all-caps (WS, DC) or a letter+digit mix (SRV2); "Windows-10" and "win-2019" are not machines
    return first.isupper() or any(c.isdigit() for c in first)


def _hostnames(text: str) -> list[str]:
    found = []
    for raw in _TOKEN_SPLIT.findall(text):
        tok = raw.strip(".:!?")
        if not tok or any(c in tok for c in "\\/@=") or len(tok) > 253:
            continue
        if _is_internal_fqdn(tok) or _is_name_num(tok):
            found.append(tok)
    return found + _cue_values(_HOST_CUE, text)


def _usernames(text: str) -> list[str]:
    found = []
    for m in _DOMAIN_USER.finditer(text):
        nxt = text[m.end():m.end() + 1]
        if m.group(0).split("\\")[0].lower() not in _NOT_DOMAIN and nxt != "\\":
            found.append(m.group(0))
    return found + _cue_values(_USER_CUE, text) + _cue_values(_USER_VERB, text)


# ---- milestones ------------------------------------------------------------

_KEYWORD_KEY = {"first seen": "first_seen", "detected": "detected", "contained": "contained",
                "isolated": "contained", "recovered": "recovered", "restored": "recovered"}
# 14:32 / 14:32:00.123 / 2:32pm, or a bare hour only with am/pm ("3pm")
_TIME = (r"(?:(?P<h>\d{1,2}):(?P<m>\d{2})(?::\d{2}(?:\.\d{1,9})?)?\s*(?P<ap>am|pm)?"
         r"|(?P<h2>\d{1,2})\s*(?P<ap2>am|pm))")
# keyword, <=15 non-digit chars, optional "yesterday", optional ISO date, time, optional UTC/Z/offset,
# optional trailing "yesterday" or ISO date
_MILESTONE = re.compile(
    r"\b(?P<kw>first seen|detected|contained|isolated|recovered|restored)\b"
    r"(?P<gap>[^\d\n]{0,15}?)"
    r"(?P<yest>yesterday\s+(?:at\s+)?)?"
    r"(?:(?P<d1>\d{4}-\d{2}-\d{2})[^\d\n]{0,4}?)?"
    + _TIME +
    r"\s*(?P<z>utc|z|[+-]\d{2}:?\d{2})?(?![\w])"
    r"(?:(?P<yest2>\s+yesterday\b)|[ ,]{1,2}(?:on[ ])?(?P<d2>\d{4}-\d{2}-\d{2})(?!\d))?",
    re.IGNORECASE,
)
# "not yet contained", "haven't contained", "will be contained at": the keyword is negated or still ahead
_NOT_DONE = re.compile(
    r"(?:\b(?:not|never|will|won[’']?t|cannot|to be|should|would|could|may|might|must|if|once|until|unless|before|"
    r"hop\w*|expect\w*|plan\w*|try\w*|need\w*|want\w*|eta)|n[’']t)(?:\s+\w+){0,2}\s+$", re.IGNORECASE)
# between keyword and time: a plan ("contained, ETA 16:00"), or a new sentence ("contained. Next sync 14:00")
_NOT_ITS_TIME = re.compile(r"\b(?:eta|next|sync|expected|estimated|planned|due|deadline|until|tomorrow)\b"
                           r"|[!?;]|\.\s+[A-Za-z]", re.IGNORECASE)
_DURATION = re.compile(r"\s*(?:min|hour|hr|sec)", re.IGNORECASE)   # "recovered in 10:30 minutes"
_UTC = timezone.utc
_SKEW = timedelta(minutes=5)   # "contained at 14:32" typed at 14:31 is now, not a plan


def _milestone_at(m: re.Match, tz: Optional[ZoneInfo], message_at: datetime) -> Optional[datetime]:
    """The moment a match names, or None when it is not a past time we can place."""
    hour, minute, ap = int(m["h"] or m["h2"]), int(m["m"] or 0), (m["ap"] or m["ap2"] or "").lower()
    if minute > 59 or (ap and not 1 <= hour <= 12) or hour > 23:
        return None
    if ap:
        hour = hour % 12 + (12 if ap == "pm" else 0)
    explicit, yesterday = m["d1"] or m["d2"], bool(m["yest"] or m["yest2"])
    z = (m["z"] or "").lower()
    if z in ("utc", "z"):
        zone = _UTC
    elif z and m["d1"]:   # offsets only count on an ISO timestamp: "14:00-15:30" is a range
        sign, digits = (-1 if z[0] == "-" else 1), z[1:].replace(":", "")
        zone = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
    else:
        zone = tz
    if zone is None:
        return None   # author's timezone unknown: never guess UTC
    if message_at.tzinfo is None:
        message_at = message_at.replace(tzinfo=_UTC)
    now = message_at.astimezone(zone)
    if explicit:
        try:
            day = date.fromisoformat(explicit)
        except ValueError:
            return None
    else:
        day = now.date() - timedelta(days=1 if yesterday else 0)
    at = datetime.combine(day, time(hour, minute), tzinfo=zone)
    ahead = at.astimezone(_UTC) - message_at.astimezone(_UTC)
    if ahead > _SKEW:
        # only a bare "23:50" written just after midnight means last night; any other time ahead of the
        # message is a plan, not something that happened
        if explicit or yesterday or ahead < timedelta(hours=12):
            return None
        at = datetime.combine(day - timedelta(days=1), time(hour, minute), tzinfo=zone)
    return at


def _milestones(text: str, tz: Optional[ZoneInfo], message_at: datetime) -> list:
    out, seen = [], set()
    for m in _MILESTONE.finditer(text):
        key = _KEYWORD_KEY[m["kw"].lower()]
        if (key in seen or _NOT_DONE.search(text[max(0, m.start() - 40):m.start()]) or _NOT_ITS_TIME.search(m["gap"])
                or _DURATION.match(text, m.end())):
            continue
        at = _milestone_at(m, tz, message_at)
        if at:
            seen.add(key)
            # the time is part of the key so a corrected time is a new suggestion (it supersedes the pending one)
            out.append(Candidate("milestone", {"key": key, "at": at.isoformat()},
                                 f"{key}:{at.astimezone(_UTC).strftime('%Y-%m-%dT%H:%MZ')}"))
    return out


# ---- entry point -------------------------------------------------------------

def _bounded(text: str) -> str:
    """Normalise, then cap length and drop pathological tokens so the regex
    work (including the shared indicator detector) stays cheap on hostile input."""
    text = _refang(normalize_slack_text((text or "")[:MAX_RAW_CHARS])[:MAX_TEXT_CHARS])
    return " ".join(t for t in text.split(" ") if len(t) <= MAX_TOKEN_CHARS)


def _ignored_host(host: Optional[str]) -> bool:
    """This deployment's own address and Slack itself are never indicators."""
    host = (host or "").lower()
    own = {urlsplit(u or "").hostname for u in (settings.PUBLIC_BASE_URL, settings.FRONTEND_URL)}
    return host in own or host == "slack.com" or host.endswith(".slack.com")


def _autolinked_domains(raw: str) -> list:
    """Domains Slack itself linked whose TLD the detector's list does not know (Slack checked the TLD for us)."""
    out = []
    for m in _SLACK_LINK.finditer(raw):
        labels = (_autolinked(m) or "").lower().split(".")
        if len(labels) >= 2 and labels[-1].isalpha() and labels[-1] not in TLDS and all(_LABEL.fullmatch(x) for x in labels):
            out.append(".".join(labels))
    return out


def extract_candidates(text: str, *, tz: Optional[ZoneInfo], message_at: datetime,
                       exclude_emails: set[str] = frozenset()) -> list[Candidate]:
    """tz is the author's timezone; None (unknown) keeps only times that carry their own zone."""
    linked = _autolinked_domains((text or "")[:MAX_RAW_CHARS])
    text = _bounded(text)
    excluded = {e.lower() for e in exclude_emails}
    indicators = detect_indicators(text, limit=100)
    indicators += [{"value": d, "artifact_type": "domain"} for d in linked
                   if not _is_internal_fqdn(d) and d not in {i["value"].lower() for i in indicators}]
    indicator_values = {i["value"].lower() for i in indicators}
    # hostname/username scans must not read inside URLs or emails
    bare = _URL_OR_EMAIL.sub(" ", text)
    # a cue token that is already a public indicator ("host evil.com") stays an indicator
    hosts = [h for h in _hostnames(bare) if h.lower() not in indicator_values or _is_internal_fqdn(h)]
    host_set = {h.lower() for h in hosts}

    out: dict[str, Candidate] = {}

    def add(c: Candidate) -> None:
        out.setdefault(c.value_key, c)

    for h in hosts:
        if not _ignored_host(h):
            add(_artifact("hostname", h))
    for i in indicators:
        v = i["value"]
        if i["artifact_type"] == "email" and v.lower() in excluded:
            continue
        if i["artifact_type"] == "domain" and any(v.lower() == h or h.endswith("." + v.lower()) for h in host_set):
            continue
        if _ignored_host(urlsplit(v).hostname if i["artifact_type"] == "url" else v):
            continue
        add(_artifact(i["artifact_type"], v))
    for u in _usernames(bare):
        add(_artifact("username", u))
    return list(out.values()) + _milestones(text, tz, message_at)
