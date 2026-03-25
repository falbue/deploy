import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from deploy_app.config import ENABLE_NGINX_GATEWAY
from deploy_app.db import get_session
from deploy_app.deps import require_auth
from deploy_app.models import Deployment, User
from deploy_app.schemas import (
    NginxCertbotRequest,
    NginxCustomConfigRequest,
    NginxPresetApiRequest,
    NginxPresetPreviewRequest,
)
from deploy_app.services.deployments import can_access_deployment
from deploy_app.services.docker_ops import (
    docker_compose_run_certbot,
    docker_compose_up_no_pull,
    ensure_gateway_stack,
    validate_nginx_config,
)

router = APIRouter(prefix="/deployments", tags=["nginx"])


def ensure_nginx_gateway_enabled() -> None:
    if not ENABLE_NGINX_GATEWAY:
        raise HTTPException(
            status_code=409,
            detail=(
                "Встроенный Nginx gateway отключен через ENABLE_NGINX_GATEWAY=false"
            ),
        )


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "-", value.lower())


def build_repo_slug(owner_repo: str) -> str:
    return slugify(owner_repo.split("/", 1)[1])


def build_owner_slug(owner_username: str) -> str:
    return slugify(owner_username)


def build_app_project_name(owner_repo: str, owner_username: str) -> str:
    return f"{build_owner_slug(owner_username)}-{build_repo_slug(owner_repo)}"


def get_deployment_for_nginx(
    session: Session,
    deployment_id: int,
    current_user: User,
) -> tuple[Deployment, User]:
    deployment = session.get(Deployment, deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Деплой не найден")
    if not can_access_deployment(current_user, deployment):
        raise HTTPException(status_code=403, detail="Нет доступа к деплою")

    owner = session.get(User, deployment.owner_id)
    if not owner:
        raise HTTPException(status_code=500, detail="Владелец деплоя не найден")
    return deployment, owner


def conf_file_path(compose_path: Path, deployment_id: int, domain: str) -> Path:
    conf_name = f"dpl-{deployment_id}-{slugify(domain)}.conf"
    return compose_path.parent / "conf.d" / conf_name


def write_config_with_validation(conf_path: Path, content: str, compose_path: Path) -> None:
    old_content = conf_path.read_text(encoding="utf-8") if conf_path.exists() else None
    conf_path.parent.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(content, encoding="utf-8")

    try:
        validate_nginx_config(compose_path.parent)
    except Exception as exc:
        if old_content is None:
            conf_path.unlink(missing_ok=True)
        else:
            conf_path.write_text(old_content, encoding="utf-8")
        raise HTTPException(status_code=422, detail=f"Nginx config invalid: {exc}") from exc


def render_api_preset_config(
    domain: str,
    app_host: str,
    app_port: int,
    use_ssl: bool,
    force_https: bool,
) -> str:
    acme_block = """
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
"""
    proxy_block = f"""
    location / {{
        proxy_pass http://{app_host}:{app_port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
"""

    if not use_ssl:
        return f"""server {{
    listen 80;
    server_name {domain};
{acme_block}{proxy_block}}}
"""

    https_redirect = ""
    if force_https:
        https_redirect = """
    location / {
        return 301 https://$host$request_uri;
    }
"""

    return f"""server {{
    listen 80;
    server_name {domain};
{acme_block}{https_redirect}}}

server {{
    listen 443 ssl http2;
    server_name {domain};

    ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{domain}/privkey.pem;

{proxy_block}}}
"""


@router.post("/{deployment_id}/nginx/preset-api")
def set_nginx_preset_api(
    deployment_id: int,
    body: NginxPresetApiRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    ensure_nginx_gateway_enabled()
    deployment, owner = get_deployment_for_nginx(session, deployment_id, current_user)
    compose_path = ensure_gateway_stack()

    project_name = build_app_project_name(deployment.owner_repo, owner.username)
    app_host = f"{project_name}-app-1"
    config = render_api_preset_config(
        domain=body.domain,
        app_host=app_host,
        app_port=5000,
        use_ssl=False,
        force_https=body.force_https,
    )

    conf_path = conf_file_path(compose_path, deployment_id, body.domain)
    write_config_with_validation(conf_path, config, compose_path)

    docker_compose_up_no_pull(compose_path)
    return {"status": "ok", "config_path": str(conf_path)}


@router.post("/{deployment_id}/nginx/preview/preset-api")
def preview_nginx_preset_api(
    deployment_id: int,
    body: NginxPresetPreviewRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    deployment, owner = get_deployment_for_nginx(session, deployment_id, current_user)
    project_name = build_app_project_name(deployment.owner_repo, owner.username)
    app_host = f"{project_name}-app-1"
    config = render_api_preset_config(
        domain=body.domain,
        app_host=app_host,
        app_port=5000,
        use_ssl=body.use_ssl,
        force_https=body.force_https,
    )
    return {"config": config}


@router.put("/{deployment_id}/nginx/custom")
def set_nginx_custom_config(
    deployment_id: int,
    body: NginxCustomConfigRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    ensure_nginx_gateway_enabled()
    get_deployment_for_nginx(session, deployment_id, current_user)
    compose_path = ensure_gateway_stack()

    conf_path = conf_file_path(compose_path, deployment_id, body.domain)
    write_config_with_validation(conf_path, body.content.strip() + "\n", compose_path)

    docker_compose_up_no_pull(compose_path)
    return {"status": "ok", "config_path": str(conf_path)}


@router.post("/{deployment_id}/nginx/certbot")
def activate_certbot(
    deployment_id: int,
    body: NginxCertbotRequest,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    ensure_nginx_gateway_enabled()
    deployment, owner = get_deployment_for_nginx(session, deployment_id, current_user)
    compose_path = ensure_gateway_stack()

    project_name = build_app_project_name(deployment.owner_repo, owner.username)
    app_host = f"{project_name}-app-1"

    pre_config = render_api_preset_config(
        domain=body.domain,
        app_host=app_host,
        app_port=5000,
        use_ssl=False,
        force_https=False,
    )
    conf_path = conf_file_path(compose_path, deployment_id, body.domain)
    write_config_with_validation(conf_path, pre_config, compose_path)
    docker_compose_up_no_pull(compose_path)

    docker_compose_run_certbot(
        compose_path=compose_path,
        domain=body.domain,
        email=body.email,
        staging=body.staging,
    )

    ssl_config = render_api_preset_config(
        domain=body.domain,
        app_host=app_host,
        app_port=5000,
        use_ssl=True,
        force_https=True,
    )
    write_config_with_validation(conf_path, ssl_config, compose_path)
    docker_compose_up_no_pull(compose_path)

    return {"status": "ok", "config_path": str(conf_path)}


@router.delete("/{deployment_id}/nginx")
def delete_nginx_config(
    deployment_id: int,
    domain: str,
    current_user: User = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict[str, str]:
    ensure_nginx_gateway_enabled()
    get_deployment_for_nginx(session, deployment_id, current_user)
    compose_path = ensure_gateway_stack()

    conf_path = conf_file_path(compose_path, deployment_id, domain)
    if conf_path.exists():
        old_content = conf_path.read_text(encoding="utf-8")
        conf_path.unlink()
        try:
            validate_nginx_config(compose_path.parent)
        except Exception as exc:
            conf_path.write_text(old_content, encoding="utf-8")
            raise HTTPException(status_code=422, detail=f"Nginx config invalid: {exc}") from exc

    docker_compose_up_no_pull(compose_path)
    return {"status": "ok"}
