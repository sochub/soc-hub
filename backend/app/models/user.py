from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.orm import relationship
from app.db.base_class import Base
import enum


class UserRole(str, enum.Enum):
    """Role values. SUPER_ADMIN is represented on the user via is_super_admin;
    the others (admin/analyst/viewer) are stored per-tenant on
    TenantMembership.role.
    """
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String)
    is_active = Column(Boolean, default=True)
    # Global super-admin flag. Tenant-scoped roles live on TenantMembership.
    is_super_admin = Column(Boolean, nullable=False, default=False)

    memberships = relationship(
        "TenantMembership", back_populates="user", cascade="all, delete-orphan"
    )
