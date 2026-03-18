import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, col, select

from deploy_app.config import DB_ROOT
from deploy_app.db import get_session
from deploy_app.deps import require_auth
from deploy_app.models import DatabaseInstance, User, UserRole
from deploy_app.routers.deployments import get_deployment_or_404
from deploy_app.schemas import DatabaseCreateRequest, DatabaseRead
from deploy_app.services.deployments import allocate_db_port, can_access_deployment
from deploy_app.services.docker_ops import (
    docker_compose_apply,
    docker_compose_down,
    render_db_compose,
)

router = APIRouter(prefix="/databases", tags=["databases"])


def get_database_or_404(session: Session, database_id: int) -> DatabaseInstance:
    db_instance = session.get(DatabaseInstance, database_id)
    if not db_instance:
        raise HTTPException(status_code=404, detail="База данных не найдена")
    return db_instance


def can_access_database(current_user: User, db_instance: DatabaseInstance) -> bool:
    return current_user.role == UserRole.ADMIN or db_instance.owner_id == current_user.id


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

    user_id = current_user.id or 0
    host_port = allocate_db_port(session, current_user)
    service_name = f"db-u{user_id}-{safe_name}"
    db_dir = DB_ROOT / f"u{user_id}" / safe_name
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


@router.post("/{database_id}/apply")
def apply_database(
    database_id: int,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    db_instance = get_database_or_404(session, database_id)
    if not can_access_database(current_user, db_instance):
        raise HTTPException(status_code=403, detail="Нет доступа к базе данных")

    compose_path = Path(db_instance.compose_path)
    if not compose_path.exists():
        raise HTTPException(status_code=404, detail="docker-compose.yml не найден")

    try:
        docker_compose_apply(compose_path)
        db_instance.status = "running"
        session.add(db_instance)
        session.commit()
    except Exception as exc:
        db_instance.status = f"error: {exc}"
        session.add(db_instance)
        session.commit()
        raise HTTPException(
            status_code=500, detail=f"Не удалось запустить БД: {exc}"
        ) from exc

    return {"status": "ok"}


@router.delete("/{database_id}")
def delete_database(
    database_id: int,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    db_instance = get_database_or_404(session, database_id)
    if not can_access_database(current_user, db_instance):
        raise HTTPException(status_code=403, detail="Нет доступа к базе данных")

    compose_path = Path(db_instance.compose_path)
    if compose_path.exists():
        try:
            docker_compose_down(compose_path, remove_volumes=True)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Не удалось удалить БД контейнер: {exc}"
            ) from exc

    db_dir = compose_path.parent
    if db_dir.exists():
        shutil.rmtree(db_dir, ignore_errors=True)

    session.delete(db_instance)
    session.commit()
    return {"status": "ok"}
