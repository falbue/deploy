from fastapi import APIRouter, Depends

from deploy_app.deps import require_auth
from deploy_app.models import User
from deploy_app.schemas import UserRead

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(require_auth)) -> UserRead:
    return UserRead(
        id=current_user.id or 0,
        username=current_user.username,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
    )
