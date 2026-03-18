from fastapi import APIRouter, Depends, HTTPException

from deploy_app.deps import require_auth
from deploy_app.models import User
from deploy_app.schemas import GhcrLoginRequest, UserRead
from deploy_app.services.deployments import get_docker_config_dir_for_user
from deploy_app.services.docker_ops import docker_login_ghcr, docker_logout_ghcr

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


@router.post("/ghcr/login")
def ghcr_login(
    body: GhcrLoginRequest,
    current_user: User = Depends(require_auth),
) -> dict[str, str]:
    docker_config_dir = get_docker_config_dir_for_user(current_user)
    try:
        docker_login_ghcr(
            docker_config_dir=docker_config_dir,
            github_username=body.github_username,
            github_token=body.github_token,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GHCR login failed: {exc}") from exc
    return {"status": "ok"}


@router.post("/ghcr/logout")
def ghcr_logout(current_user: User = Depends(require_auth)) -> dict[str, str]:
    docker_config_dir = get_docker_config_dir_for_user(current_user)
    try:
        docker_logout_ghcr(docker_config_dir=docker_config_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"GHCR logout failed: {exc}") from exc
    return {"status": "ok"}
