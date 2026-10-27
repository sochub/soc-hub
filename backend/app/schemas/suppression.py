import ipaddress
import re
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator
from app.models.suppression_rule import SUPPRESSION_OPS

FIELD_RE = re.compile(r"^(source|title|severity|payload(\.[A-Za-z0-9_\-@$]{1,64}){1,8})$")


class Condition(BaseModel):
    field: str = Field(description="source, title, severity, or payload.<dotted.path>")
    op: str
    value: str = Field(max_length=500)

    @field_validator("field")
    @classmethod
    def _field(cls, v):
        if not FIELD_RE.match(v):
            raise ValueError("field must be source, title, severity or payload.<dotted.path>")
        return v

    @field_validator("op")
    @classmethod
    def _op(cls, v):
        if v not in SUPPRESSION_OPS:
            raise ValueError(f"op must be one of {', '.join(SUPPRESSION_OPS)}")
        return v

    @model_validator(mode="after")
    def _cidr(self):
        if self.op == "in_cidr":
            try:
                ipaddress.ip_network(self.value.strip(), strict=False)
            except ValueError:
                raise ValueError(f"'{self.value}' is not a CIDR range") from None
        elif not self.value.strip():
            raise ValueError("value must not be empty")
        return self


Conditions = List[Condition]


class SuppressionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=2000)
    conditions: Conditions = Field(min_length=1, max_length=10)
    enabled: bool = True
    expires_at: Optional[datetime] = None
    created_from_alert_id: Optional[int] = None


class SuppressionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    reason: Optional[str] = Field(default=None, max_length=2000)
    conditions: Optional[Conditions] = Field(default=None, min_length=1, max_length=10)
    enabled: Optional[bool] = None
    expires_at: Optional[datetime] = None


class SuppressionPreview(BaseModel):
    conditions: Conditions = Field(min_length=1, max_length=10)


class SuppressionOut(BaseModel):
    id: int
    name: str
    reason: Optional[str] = None
    conditions: list
    enabled: bool
    expires_at: Optional[datetime] = None
    hit_count: int
    last_hit_at: Optional[datetime] = None
    created_from_alert_id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
