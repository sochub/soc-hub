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
