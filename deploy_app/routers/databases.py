import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, col, select

from deploy_app.config import DB_ROOT
from deploy_app.db import get_session
from deploy_app.deps import require_auth
from deploy_app.models import DatabaseInstance, User, UserRole
from deploy_app.routers.deployments import get_deployment_or_404
from deploy_app.schemas import DatabaseCreateRequest, DatabaseRead
from deploy_app.services.deployments import allocate_db_port, can_access_deployment
from deploy_app.services.docker_ops import docker_compose_apply, render_db_compose

router = APIRouter(prefix="/databases", tags=["databases"])


@router.post("", response_model=DatabaseRead)
def create_database(
    body: DatabaseCreateRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> DatabaseRead:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", body.name.strip()).strip("-")
    if len(safe_name) < 3:
        raise HTTPException(status_code=422, detail="Некорректное имя базы")

    deployment_id = body.deployment_id
    if deployment_id is not None:
        deployment = get_deployment_or_404(session, deployment_id)
        if not can_access_deployment(current_user, deployment):
            raise HTTPException(status_code=403, detail="Нет доступа к деплою")

    duplicate = session.exec(
        select(DatabaseInstance).where(
            DatabaseInstance.owner_id == current_user.id,
            DatabaseInstance.name == safe_name,
        )
    ).first()
    if duplicate:
        raise HTTPException(
            status_code=409, detail="База с таким именем уже существует"
        )

    host_port = allocate_db_port(session, current_user)
    service_name = f"{current_user.username}-{safe_name}-db"
    db_dir = DB_ROOT / current_user.username / safe_name
    db_dir.mkdir(parents=True, exist_ok=True)
    volume_path = db_dir / "postgres"
    volume_path.mkdir(parents=True, exist_ok=True)

    compose_path = db_dir / "docker-compose.yml"
    compose_path.write_text(
        render_db_compose(
            service_name=service_name,
            volume_path=volume_path,
            host_port=host_port,
            postgres_image=body.postgres_image,
            postgres_user=body.postgres_user,
            postgres_password=body.postgres_password,
            postgres_db=body.postgres_db,
        ),
        encoding="utf-8",
    )

    db_instance = DatabaseInstance(
        owner_id=current_user.id or 0,
        deployment_id=deployment_id,
        name=safe_name,
        service_name=service_name,
        host_port=host_port,
        compose_path=str(compose_path),
        status="created",
    )
    session.add(db_instance)
    session.commit()
    session.refresh(db_instance)

    if body.run_deploy:
        try:
            docker_compose_apply(compose_path)
            db_instance.status = "running"
            session.add(db_instance)
            session.commit()
            session.refresh(db_instance)
        except Exception as exc:
            db_instance.status = f"error: {exc}"
            session.add(db_instance)
            session.commit()
            raise HTTPException(
                status_code=500, detail=f"Не удалось поднять БД: {exc}"
            ) from exc

    return DatabaseRead(
        id=db_instance.id or 0,
        owner_id=db_instance.owner_id,
        deployment_id=db_instance.deployment_id,
        name=db_instance.name,
        service_name=db_instance.service_name,
        host_port=db_instance.host_port,
        compose_path=db_instance.compose_path,
        status=db_instance.status,
        created_at=db_instance.created_at,
    )


@router.get("", response_model=list[DatabaseRead])
def list_databases(
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
    deployment_id: int | None = Query(default=None),
) -> list[DatabaseRead]:
    query = select(DatabaseInstance).order_by(col(DatabaseInstance.id))
    if current_user.role != UserRole.ADMIN:
        query = query.where(DatabaseInstance.owner_id == current_user.id)
    if deployment_id is not None:
        query = query.where(DatabaseInstance.deployment_id == deployment_id)

    dbs = session.exec(query).all()
    return [
        DatabaseRead(
            id=db.id or 0,
            owner_id=db.owner_id,
            deployment_id=db.deployment_id,
            name=db.name,
            service_name=db.service_name,
            host_port=db.host_port,
            compose_path=db.compose_path,
            status=db.status,
            created_at=db.created_at,
        )
        for db in dbs
    ]
