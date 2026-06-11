from pydantic import BaseModel


class MembershipOut(BaseModel):
    tenant_id: int
    tenant_name: str
    tenant_slug: str
    role: str
