"""Template-time secret references: render to an opaque placeholder; anything else fails.

The real secret value is never in the template context. `secrets.NAME` yields a SecretRef whose
only observable form is the placeholder text `⟦secret:NAME⟧`, resolved later at HTTP send time.
"""
import re

from app.workflows.templating import TemplateError

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
PLACEHOLDER_RE = re.compile(r"⟦secret:([A-Z][A-Z0-9_]{1,63})⟧")
_MSG = "secrets can only be inserted, not transformed"


def placeholder(name: str) -> str:
    return f"⟦secret:{name}⟧"


def _deny(*_a, **_k):
    raise TemplateError(_MSG)


class SecretRef:
    __slots__ = ("_name",)

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)

    def __str__(self):
        return placeholder(self._name)

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
    __slots__ = ()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if not NAME_RE.match(name):
            raise TemplateError(f"Invalid secret name {name}")
        return SecretRef(name)

    def __getitem__(self, name):
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise TemplateError(f"Invalid secret name {name}")
        return SecretRef(name)

    def __repr__(self):
        return "SecretsNamespace()"
