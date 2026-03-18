from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from deploy_app.db import get_session
from deploy_app.models import User, UserRole
from deploy_app.security import hash_api_key


def get_user_by_api_key(session: Session, api_key: str) -> User | None:
    key_hash = hash_api_key(api_key)
    return session.exec(select(User).where(User.api_key_hash == key_hash)).first()


def require_auth(
    x_api_key: str = Header(default="", alias="X-API-Key"),
    session: Session = Depends(get_session),
) -> User:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key обязателен"
        )
    user = get_user_by_api_key(session, x_api_key)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный API ключ"
        )
    return user


def require_admin(current_user: User = Depends(require_auth)) -> User:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Только для админов"
        )
    return current_user
