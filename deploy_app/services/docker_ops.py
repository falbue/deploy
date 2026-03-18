import os
import subprocess
from pathlib import Path

from deploy_app.config import DB_NET_NAME, NGINX_GATEWAY_ROOT, WEB_NET_NAME


def ensure_external_network(network_name: str) -> None:
    inspect = subprocess.run(
        ["docker", "network", "inspect", network_name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inspect.returncode == 0:
        return

    create = subprocess.run(
        ["docker", "network", "create", network_name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if create.returncode != 0:
        raise RuntimeError(
            f"Не удалось создать сеть '{network_name}'. "
            f"STDOUT: {create.stdout} STDERR: {create.stderr}"
        )


def _build_run_env(docker_config_dir: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if docker_config_dir is not None:
        docker_config_dir.mkdir(parents=True, exist_ok=True)
        env["DOCKER_CONFIG"] = str(docker_config_dir)
    return env


def render_app_compose(
    project_name: str,
    owner_repo: str,
    tag: str,
    app_port: int,
) -> str:
    return f"""name: {project_name}

services:
  app:
    image: ghcr.io/{owner_repo}:{tag}
    env_file:
      - .env
    environment:
      - IN_DOCKER=1
    ports:
      - \"{app_port}:5000\"
    restart: unless-stopped
    networks:
      - {DB_NET_NAME}
      - {WEB_NET_NAME}
    volumes:
      - ./data:/data

networks:
  {DB_NET_NAME}:
    external: true
  {WEB_NET_NAME}:
    external: true
"""


def render_db_compose(
    service_name: str,
    volume_path: Path,
    host_port: int,
    postgres_image: str,
    postgres_user: str,
    postgres_password: str,
    postgres_db: str,
) -> str:
    return f"""services:
  {service_name}:
    image: {postgres_image}
    restart: unless-stopped
    environment:
      POSTGRES_USER: {postgres_user}
      POSTGRES_PASSWORD: {postgres_password}
      POSTGRES_DB: {postgres_db}
    volumes:
      - {volume_path.as_posix()}:/var/lib/postgresql
    ports:
      - \"127.0.0.1:{host_port}:5432\"
    networks:
      - {DB_NET_NAME}

networks:
  {DB_NET_NAME}:
    external: true
"""


def render_gateway_compose() -> str:
    return f"""services:
  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./conf.d:/etc/nginx/conf.d:rw
      - ./certbot/www:/var/www/certbot:rw
      - ./certbot/conf:/etc/letsencrypt:rw
    networks:
      - {WEB_NET_NAME}

  certbot:
    image: certbot/certbot:latest
    restart: unless-stopped
    entrypoint: /bin/sh -c "trap exit TERM; while :; do certbot renew --webroot -w /var/www/certbot --quiet; sleep 12h; done"
    volumes:
      - ./certbot/www:/var/www/certbot:rw
      - ./certbot/conf:/etc/letsencrypt:rw
    networks:
      - {WEB_NET_NAME}

networks:
  {WEB_NET_NAME}:
    external: true
"""


def ensure_gateway_stack() -> Path:
    ensure_external_network(WEB_NET_NAME)

    gateway_root = NGINX_GATEWAY_ROOT
    conf_dir = gateway_root / "conf.d"
    certbot_www = gateway_root / "certbot" / "www"
    certbot_conf = gateway_root / "certbot" / "conf"
    gateway_root.mkdir(parents=True, exist_ok=True)
    conf_dir.mkdir(parents=True, exist_ok=True)
    certbot_www.mkdir(parents=True, exist_ok=True)
    certbot_conf.mkdir(parents=True, exist_ok=True)

    compose_path = gateway_root / "docker-compose.yml"
    compose_path.write_text(render_gateway_compose(), encoding="utf-8")
    return compose_path


def docker_compose_apply(
    compose_path: Path,
    timeout_seconds: int = 180,
    docker_config_dir: Path | None = None,
) -> None:
    ensure_external_network(DB_NET_NAME)
    ensure_external_network(WEB_NET_NAME)
    env = _build_run_env(docker_config_dir)
    pull = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "pull"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    if pull.returncode != 0:
        raise RuntimeError(f"docker compose pull failed: {pull.stderr or pull.stdout}")

    up = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d", "--remove-orphans"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    if up.returncode != 0:
        raise RuntimeError(f"docker compose up failed: {up.stderr or up.stdout}")


def docker_compose_down(
    compose_path: Path,
    remove_volumes: bool = True,
    timeout_seconds: int = 180,
    docker_config_dir: Path | None = None,
) -> None:
    command = ["docker", "compose", "-f", str(compose_path), "down", "--remove-orphans"]
    if remove_volumes:
        command.append("-v")

    down = subprocess.run(
        command,
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=_build_run_env(docker_config_dir),
    )
    if down.returncode != 0:
        raise RuntimeError(f"docker compose down failed: {down.stderr or down.stdout}")


def docker_compose_up_no_pull(compose_path: Path, timeout_seconds: int = 180) -> None:
    up = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d", "--remove-orphans"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if up.returncode != 0:
        raise RuntimeError(f"docker compose up failed: {up.stderr or up.stdout}")


def docker_compose_run_certbot(
    compose_path: Path,
    domain: str,
    email: str,
    staging: bool = False,
    timeout_seconds: int = 300,
) -> None:
    command = [
        "docker",
        "compose",
        "-f",
        str(compose_path),
        "run",
        "--rm",
        "certbot",
        "certonly",
        "--webroot",
        "-w",
        "/var/www/certbot",
        "-d",
        domain,
        "--email",
        email,
        "--agree-tos",
        "--no-eff-email",
        "--non-interactive",
    ]
    if staging:
        command.append("--staging")

    run = subprocess.run(
        command,
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if run.returncode != 0:
        raise RuntimeError(f"certbot failed: {run.stderr or run.stdout}")


def validate_nginx_config(gateway_root: Path, timeout_seconds: int = 120) -> None:
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{(gateway_root / 'conf.d').as_posix()}:/etc/nginx/conf.d:ro",
        "-v",
        f"{(gateway_root / 'certbot' / 'conf').as_posix()}:/etc/letsencrypt:ro",
        "-v",
        f"{(gateway_root / 'certbot' / 'www').as_posix()}:/var/www/certbot:ro",
        "nginx:alpine",
        "nginx",
        "-t",
    ]
    check = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if check.returncode != 0:
        raise RuntimeError(f"nginx -t failed: {check.stderr or check.stdout}")


def docker_login_ghcr(
    docker_config_dir: Path,
    github_username: str,
    github_token: str,
    timeout_seconds: int = 120,
) -> None:
    env = _build_run_env(docker_config_dir)
    login = subprocess.run(
        ["docker", "login", "ghcr.io", "-u", github_username, "--password-stdin"],
        input=github_token,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    if login.returncode != 0:
        raise RuntimeError(f"docker login ghcr.io failed: {login.stderr or login.stdout}")


def docker_logout_ghcr(docker_config_dir: Path, timeout_seconds: int = 60) -> None:
    env = _build_run_env(docker_config_dir)
    logout = subprocess.run(
        ["docker", "logout", "ghcr.io"],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=env,
    )
    if logout.returncode != 0:
        raise RuntimeError(f"docker logout ghcr.io failed: {logout.stderr or logout.stdout}")
