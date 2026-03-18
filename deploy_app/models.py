from datetime import datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, SQLModel


class UserRole(str, Enum):
    BASIC = "basic"
    PREMIUM = "premium"
    ADMIN = "admin"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    role: UserRole = Field(default=UserRole.BASIC)
    api_key_hash: str = Field(index=True, unique=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Deployment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(index=True, foreign_key="user.id")
    owner_repo: str = Field(index=True)
    tag: str
    app_port: int = Field(index=True, unique=True)
    deploy_path: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DatabaseInstance(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(index=True, foreign_key="user.id")
    deployment_id: Optional[int] = Field(
        default=None, index=True, foreign_key="deployment.id"
    )
    name: str
    service_name: str = Field(unique=True, index=True)
    host_port: int = Field(unique=True, index=True)
    compose_path: str
    status: str = Field(default="created")
    created_at: datetime = Field(default_factory=datetime.utcnow)
