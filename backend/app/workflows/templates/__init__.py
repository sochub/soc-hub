"""Workflow template catalog: JSON files in this directory, installed per tenant via /workflows/templates."""
import copy
import json
import re
from pathlib import Path
from typing import Dict, List

_DIR = Path(__file__).parent
_PARAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,252}$")  # blocks `{{`, `%%`, quotes, whitespace
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}$")  # for params whose key ends in _EMAIL
_cache: List[dict] = []


def reset_cache() -> None:
    _cache.clear()


def load_templates() -> List[dict]:
    if not _cache:  # ponytail: read once per process; call reset_cache() after changing files
        _cache.extend(sorted((json.loads(p.read_text()) for p in _DIR.glob("*.json")), key=lambda t: t["key"]))
    return _cache


def get_template(key: str) -> dict:
    for t in load_templates():
        if t["key"] == key:
            return t
    raise KeyError(key)


def apply_params(template: dict, params: Dict[str, str]) -> dict:
    """Deep copy of the template with `%%KEY%%` replaced in graph, trigger_filter and secret hosts."""
    values = {}
    for p in template.get("params", []):
        v = params.get(p["key"])
        if v is None:
            raise ValueError(f"Missing parameter {p['key']}")
        email = p["key"].endswith("_EMAIL")
        if not isinstance(v, str) or not (_EMAIL_RE if email else _PARAM_RE).fullmatch(v):
            raise ValueError(f"Invalid value for {p['key']}: " + ("use an email address" if email else
                                                                    "use letters, digits and . _ : / - only"))
        values[f"%%{p['key']}%%"] = v

    def sub(x):
        if isinstance(x, str):
            for k, v in values.items():
                x = x.replace(k, v)
            return x
        if isinstance(x, list):
            return [sub(i) for i in x]
        if isinstance(x, dict):
            return {k: sub(v) for k, v in x.items()}
        return x

    out = copy.deepcopy(template)
    for field in ("graph", "trigger_filter", "secrets"):
        out[field] = sub(out.get(field))
    return out
