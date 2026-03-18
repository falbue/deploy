from datetime import datetime
from pathlib import Path
import re
import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, select

from deploy_app.config import DEPLOY_ROOT
from deploy_app.db import get_session
from deploy_app.deps import require_auth
from deploy_app.models import DatabaseInstance, Deployment, User, UserRole
from deploy_app.schemas import (
    DeploymentCreateRequest,
    DeploymentRead,
    DeploymentRedeployRequest,
    EnvPatchRequest,
    EnvReplaceRequest,
)
from deploy_app.services.deployments import (
    allocate_app_port,
    can_access_deployment,
    check_deploy_limit,
    dump_env,
    parse_env,
    validate_owner_repo,
    write_env_file,
)
from deploy_app.services.docker_ops import (
    docker_compose_apply,
    docker_compose_down,
    render_app_compose,
)

router = APIRouter(prefix="/deployments", tags=["deployments"])


def build_app_project_name(owner_repo: str, user_id: int) -> str:
    repo_name = owner_repo.split("/", 1)[1]
    raw_name = f"dpl-u{user_id}-{repo_name}"
    return re.sub(r"[^a-z0-9_-]", "-", raw_name.lower())


def get_deployment_or_404(session: Session, deployment_id: int) -> Deployment:
    deployment = session.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Деплой не найден")
    return deployment


@router.post("", response_model=DeploymentRead)
def create_deployment(
    body: DeploymentCreateRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DeploymentRead:
    validate_owner_repo(body.owner_repo)
    check_deploy_limit(session, current_user)

    existing = session.exec(
        select(Deployment).where(
            Deployment.owner_id == current_user.id,
            Deployment.owner_repo == body.owner_repo,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=409, detail="Этот репозиторий уже задеплоен пользователем"
        )

    user_id = current_user.id or 0
    project_name = build_app_project_name(body.owner_repo, user_id)
    app_port = allocate_app_port(session, current_user)
    deploy_path = DEPLOY_ROOT / project_name
    deploy_path.mkdir(parents=True, exist_ok=True)

    compose_path = deploy_path / "docker-compose.yml"
    compose_path.write_text(
        render_app_compose(project_name, body.owner_repo, body.tag, app_port),
        encoding="utf-8",
    )

    env_path = deploy_path / ".env"
    if not env_path.exists():
        write_env_file(env_path, "")

    deployment = Deployment(
        owner_id=current_user.id or 0,
        owner_repo=body.owner_repo,
        tag=body.tag,
        app_port=app_port,
        deploy_path=str(deploy_path),
        updated_at=datetime.utcnow(),
    )
    session.add(deployment)
    session.commit()
    session.refresh(deployment)

    if body.run_deploy:
        try:
            docker_compose_apply(compose_path)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось выполнить deploy: {exc}"
            ) from exc

    return DeploymentRead(
        id=deployment.id or 0,
        owner_id=deployment.owner_id,
        owner_repo=deployment.owner_repo,
        tag=deployment.tag,
        app_port=deployment.app_port,
        deploy_path=deployment.deploy_path,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )


@router.get("", response_model=list[DeploymentRead])
def list_deployments(
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[DeploymentRead]:
    query = select(Deployment).order_by(col(Deployment.id))
    if current_user.role != UserRole.ADMIN:
        query = query.where(Deployment.owner_id == current_user.id)

    deployments = session.exec(query).all()
    return [
        DeploymentRead(
            id=deployment.id or 0,
            owner_id=deployment.owner_id,
            owner_repo=deployment.owner_repo,
            tag=deployment.tag,
            app_port=deployment.app_port,
            deploy_path=deployment.deploy_path,
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
        )
        for deployment in deployments
    ]


@router.post("/{deployment_id}/redeploy", response_model=DeploymentRead)
def redeploy(
    deployment_id: int,
    body: DeploymentRedeployRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DeploymentRead:
    deployment = get_deployment_or_404(session, deployment_id)
    if not can_access_deployment(current_user, deployment):
        raise HTTPException(status_code=403, detail="Нет доступа к деплою")

    deployment.tag = body.tag
    deployment.updated_at = datetime.utcnow()
    deploy_path = Path(deployment.deploy_path)
    project_name = deploy_path.name
    compose_path = deploy_path / "docker-compose.yml"
    compose_path.write_text(
        render_app_compose(
            project_name,
            deployment.owner_repo,
            deployment.tag,
            deployment.app_port,
        ),
        encoding="utf-8",
    )
    session.add(deployment)
    session.commit()
    session.refresh(deployment)

    try:
        docker_compose_apply(compose_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось выполнить redeploy: {exc}"
        ) from exc

    return DeploymentRead(
        id=deployment.id or 0,
        owner_id=deployment.owner_id,
        owner_repo=deployment.owner_repo,
        tag=deployment.tag,
        app_port=deployment.app_port,
        deploy_path=deployment.deploy_path,
        created_at=deployment.created_at,
        updated_at=deployment.updated_at,
    )


@router.get("/{deployment_id}/env")
def get_env(
    deployment_id: int,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, object]:
    deployment = get_deployment_or_404(session, deployment_id)
    if not can_access_deployment(current_user, deployment):
        raise HTTPException(status_code=403, detail="Нет доступа к деплою")
    env_path = Path(deployment.deploy_path) / ".env"
    content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    return {"deployment_id": deployment.id, "env": parse_env(content), "raw": content}


@router.put("/{deployment_id}/env")
def replace_env(
    deployment_id: int,
    body: EnvReplaceRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    deployment = get_deployment_or_404(session, deployment_id)
    if not can_access_deployment(current_user, deployment):
        raise HTTPException(status_code=403, detail="Нет доступа к деплою")
    env_path = Path(deployment.deploy_path) / ".env"
    write_env_file(env_path, body.content)
    return {"status": "ok"}


@router.patch("/{deployment_id}/env")
def patch_env(
    deployment_id: int,
    body: EnvPatchRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    deployment = get_deployment_or_404(session, deployment_id)
    if not can_access_deployment(current_user, deployment):
        raise HTTPException(status_code=403, detail="Нет доступа к деплою")

    env_path = Path(deployment.deploy_path) / ".env"
    existing_content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    env_values = parse_env(existing_content)
    env_values.update(body.values)
    write_env_file(env_path, dump_env(env_values))
    return {"status": "ok"}


@router.post("/{deployment_id}/apply")
def apply_deployment(
    deployment_id: int,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    deployment = get_deployment_or_404(session, deployment_id)
    if not can_access_deployment(current_user, deployment):
        raise HTTPException(status_code=403, detail="Нет доступа к деплою")

    compose_path = Path(deployment.deploy_path) / "docker-compose.yml"
    if not compose_path.exists():
        raise HTTPException(status_code=404, detail="docker-compose.yml не найден")
    try:
        docker_compose_apply(compose_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Не удалось применить деплой: {exc}"
        ) from exc
    return {"status": "ok"}


@router.delete("/{deployment_id}")
def delete_deployment(
    deployment_id: int,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    deployment = get_deployment_or_404(session, deployment_id)
    if not can_access_deployment(current_user, deployment):
        raise HTTPException(status_code=403, detail="Нет доступа к деплою")

    linked_dbs = session.exec(
        select(DatabaseInstance).where(DatabaseInstance.deployment_id == deployment.id)
    ).all()
    if linked_dbs:
        raise HTTPException(
            status_code=409,
            detail="Сначала удалите связанные базы данных для этого деплоя",
        )

    deploy_path = Path(deployment.deploy_path)
    compose_path = deploy_path / "docker-compose.yml"
    if compose_path.exists():
        try:
            docker_compose_down(compose_path, remove_volumes=True)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось остановить деплой: {exc}"
            ) from exc

    if deploy_path.exists():
        shutil.rmtree(deploy_path, ignore_errors=True)

    session.delete(deployment)
    session.commit()
    return {"status": "ok"}
