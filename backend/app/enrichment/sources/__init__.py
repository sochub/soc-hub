"""Source registry: name -> object with NAME, TYPES and async lookup(itype, value, key, *, transport=None)."""
from types import SimpleNamespace

from app.enrichment.sources import abusech, virustotal

REGISTRY = {
    "virustotal": virustotal,
    "urlhaus": SimpleNamespace(NAME="urlhaus", TYPES=abusech.URLHAUS_TYPES, lookup=abusech.lookup_urlhaus),
    "threatfox": SimpleNamespace(NAME="threatfox", TYPES=abusech.THREATFOX_TYPES, lookup=abusech.lookup_threatfox),
}
