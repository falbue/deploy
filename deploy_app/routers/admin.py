from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select

from deploy_app.db import get_session
from deploy_app.deps import require_admin
from deploy_app.models import User
from deploy_app.schemas import (
    UserCreateRequest,
    UserCreateResponse,
    UserRead,
    UserRoleUpdateRequest,
)
from deploy_app.security import generate_api_key, hash_api_key

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/users", response_model=UserCreateResponse)
def admin_create_user(
    body: UserCreateRequest,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> UserCreateResponse:
    existing = session.exec(select(User).where(User.username == body.username)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Пользователь уже существует")

    api_key = generate_api_key()
    user = User(
        username=body.username,
        role=body.role,
        api_key_hash=hash_api_key(api_key),
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    return UserCreateResponse(
        id=user.id or 0, username=user.username, role=user.role, api_key=api_key
    )


@router.get("/users", response_model=list[UserRead])
def admin_list_users(
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[UserRead]:
    users = session.exec(select(User).order_by(col(User.id))).all()
    return [
        UserRead(
            id=user.id or 0,
            username=user.username,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
        )
        for user in users
    ]


@router.patch("/users/{user_id}/role", response_model=UserRead)
def admin_update_role(
    user_id: int,
    body: UserRoleUpdateRequest,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> UserRead:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    user.role = body.role
    session.add(user)
    session.commit()
    session.refresh(user)
    return UserRead(
        id=user.id or 0,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/users/{user_id}/reset-api-key")
def admin_reset_api_key(
    user_id: int,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    new_key = generate_api_key()
    user.api_key_hash = hash_api_key(new_key)
    session.add(user)
    session.commit()
    return {"api_key": new_key}
