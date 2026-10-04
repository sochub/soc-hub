"""Template-time secret references: render to an opaque, nonce-bound placeholder; anything else fails.

The real secret value is never in the template context. `secrets.NAME` yields a SecretRef whose
only observable form is the placeholder text `⟦secret:NAME#<nonce>⟧`, resolved later at HTTP send time.

Forgery protection: the runtime generates a fresh random nonce (`secrets.token_hex(8)`) for every
step render, AFTER the context data (case/alert/trigger/step outputs) is fixed. Send-time resolution
uses `placeholder_re(nonce)`, which only matches that exact nonce. Placeholder-looking text that
arrives via data therefore has no nonce or a stale/guessed one and stays inert literal text: data
cannot contain the current nonce because it did not exist when the data was written.

`ANY_PLACEHOLDER_RE` matches any placeholder (with or without a nonce) and is for display/scanning
only. NEVER use it for resolution.
"""
import re

from app.workflows.templating import TemplateError

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}\Z")
NONCE_RE = re.compile(r"^[0-9a-f]{16}\Z")
ANY_PLACEHOLDER_RE = re.compile(r"⟦secret:([A-Z][A-Z0-9_]{1,63})(?:#[0-9a-f]{16})?⟧")  # display only
_MSG = "secrets can only be inserted, not transformed"


def placeholder(name: str, nonce: str) -> str:
    return f"⟦secret:{name}#{nonce}⟧"


def placeholder_re(nonce: str, names=None) -> "re.Pattern[str]":
    """The only regex to use for resolution: matches placeholders carrying exactly this render's nonce.

    With `names` (the names a SecretsNamespace actually handed out in this render), only those names
    match: a template that rewrites one secret's placeholder text into another name's (e.g. via
    `replace`) produces inert text, never the other secret.
    """
    if names is None:
        group = r"[A-Z][A-Z0-9_]{1,63}"
    else:
        valid = sorted(n for n in names if isinstance(n, str) and NAME_RE.match(n))
        group = "|".join(re.escape(n) for n in valid) or "(?!)"
    return re.compile("⟦secret:(" + group + ")#" + re.escape(nonce) + "⟧")


def _deny(*_a, **_k):
    raise TemplateError(_MSG)


class SecretRef:
    __slots__ = ("_name", "_nonce")

    def __init__(self, name: str, nonce: str):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_nonce", nonce)

    def __str__(self):
        return placeholder(self._name, self._nonce)

    __html__ = __str__

    def __repr__(self):
        return f"SecretRef({self._name})"

    def __getattr__(self, item):
        if item.startswith("_"):  # keep Python protocol probes (hasattr/copy) well-behaved
            raise AttributeError(item)
        _deny()

    def __setattr__(self, *_a):
        _deny()

    __delattr__ = __setattr__

    __bool__ = __len__ = __iter__ = __reversed__ = __getitem__ = __contains__ = _deny
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = __hash__ = _deny
    __add__ = __radd__ = __mul__ = __rmul__ = __mod__ = __rmod__ = _deny
    __sub__ = __rsub__ = __truediv__ = __floordiv__ = __pow__ = __neg__ = __pos__ = _deny
    __int__ = __float__ = __index__ = __call__ = _deny

    def __format__(self, spec):
        if spec:
            _deny()
        return str(self)


class SecretsNamespace:
    """`secrets` in a step's render context. Built per step render with that render's nonce.

    `_issued` records every name handed out as a SecretRef; send-time resolution honours only those
    (runtime passes this live set as NodeContext.secret_names). Underscore attributes are invisible
    to the sandbox, so templates cannot read or change it.
    """
    __slots__ = ("_nonce", "_issued")

    def __init__(self, nonce: str):
        if not isinstance(nonce, str) or not NONCE_RE.match(nonce):
            raise ValueError("nonce must be 16 lowercase hex chars (secrets.token_hex(8))")
        object.__setattr__(self, "_nonce", nonce)
        object.__setattr__(self, "_issued", set())

    def _ref(self, name):
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise TemplateError(f"Invalid secret name {name}")
        self._issued.add(name)
        return SecretRef(name, self._nonce)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._ref(name)

    def __getitem__(self, name):
        return self._ref(name)

    def __repr__(self):
        return "SecretsNamespace(...)"
