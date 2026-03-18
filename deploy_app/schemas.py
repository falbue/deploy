from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field as PydanticField

from deploy_app.models import UserRole


class UserCreateRequest(BaseModel):
    username: str = PydanticField(min_length=3, max_length=64)
    role: UserRole = UserRole.BASIC


class UserCreateResponse(BaseModel):
    id: int
    username: str
    role: UserRole
    api_key: str


class UserRead(BaseModel):
    id: int
    username: str
    role: UserRole
    is_active: bool
    created_at: datetime


class UserRoleUpdateRequest(BaseModel):
    role: UserRole


class DeploymentCreateRequest(BaseModel):
    owner_repo: str = PydanticField(description="owner/repo")
    tag: str = PydanticField(min_length=1)
    run_deploy: bool = True


class DeploymentRead(BaseModel):
    id: int
    owner_id: int
    owner_repo: str
    tag: str
    app_port: int
    deploy_path: str
    created_at: datetime
    updated_at: datetime


class DeploymentRedeployRequest(BaseModel):
    tag: str = PydanticField(min_length=1)


class EnvReplaceRequest(BaseModel):
    content: str


class EnvPatchRequest(BaseModel):
    values: dict[str, str]


class DatabaseCreateRequest(BaseModel):
    name: str = PydanticField(min_length=3, max_length=64)
    deployment_id: Optional[int] = None
    postgres_image: str = PydanticField(min_length=1)
    postgres_user: str = PydanticField(min_length=1)
    postgres_password: str = PydanticField(min_length=1)
    postgres_db: str = PydanticField(min_length=1)
    run_deploy: bool = True


class DatabaseRead(BaseModel):
    id: int
    owner_id: int
    deployment_id: Optional[int]
    name: str
    service_name: str
    host_port: int
    compose_path: str
    status: str
    created_at: datetime
