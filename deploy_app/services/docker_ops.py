import subprocess
from pathlib import Path

from deploy_app.config import DB_NET_NAME


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
    volumes:
      - ./data:/data

networks:
  {DB_NET_NAME}:
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


def docker_compose_apply(compose_path: Path, timeout_seconds: int = 180) -> None:
    ensure_external_network(DB_NET_NAME)

    pull = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "pull"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if pull.returncode != 0:
        raise RuntimeError(f"docker compose pull failed: {pull.stderr or pull.stdout}")

    up = subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "up", "-d", "--remove-orphans"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    if up.returncode != 0:
        raise RuntimeError(f"docker compose up failed: {up.stderr or up.stdout}")


def docker_compose_down(
    compose_path: Path,
    remove_volumes: bool = True,
    timeout_seconds: int = 180,
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
    )
    if down.returncode != 0:
        raise RuntimeError(f"docker compose down failed: {down.stderr or down.stdout}")
