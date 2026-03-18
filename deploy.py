import hashlib
import hmac
import os
import subprocess
import logging
from pathlib import Path
from flask import Flask, request, abort, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").encode()
DEPLOY_ROOT = Path(os.environ.get("DEPLOY_ROOT", "/apps"))


def get_port_for_repo(owner: str, repo_name: str) -> int:
    """Рассчитывает внешний порт для репозитория (стабильный через сортировку)."""
    owners = sorted([d.name for d in DEPLOY_ROOT.iterdir() if d.is_dir()])
    owner_index = owners.index(owner) if owner in owners else len(owners)
    base_port = 2000 + owner_index * 1000

    owner_path = DEPLOY_ROOT / owner
    repos = (
        sorted([d.name for d in owner_path.iterdir() if d.is_dir()])
        if owner_path.exists()
        else []
    )
    repo_index = repos.index(repo_name) if repo_name in repos else len(repos)

    return base_port + repo_index + 1  # Порт начинается с 1


def verify_signature(payload: bytes, sig_header: str) -> bool:
    if not WEBHOOK_SECRET or not sig_header:
        return False
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header)


def ensure_external_network(network_name: str = "db-net") -> None:
    """Создаёт внешнюю сеть при отсутствии."""
    inspect = subprocess.run(
        ["docker", "network", "inspect", network_name],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if inspect.returncode == 0:
        logger.info(f"✅ Docker network '{network_name}' уже существует")
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

    logger.info(f"✅ Docker network '{network_name}' создана")


def ensure_compose_file(repo_path: Path, full_repo: str, tag: str) -> Path:
    """Гарантированно создаёт/перезаписывает docker-compose.yml с актуальным тегом."""
    owner, repo_name = full_repo.split("/", 1)
    external_port = get_port_for_repo(owner, repo_name)
    compose_content = f"""services:
  app:
    image: ghcr.io/{full_repo}:{tag}
    env_file:
      - .env
    environment:
      - IN_DOCKER=1
    ports:
      - "{external_port}:5000"
    restart: unless-stopped
    networks:
      - db-net
    volumes:
      - ./data:/data

networks:
  db-net:
    external: true"""
    compose_file = repo_path / "docker-compose.yml"
    compose_file.write_text(compose_content, encoding="utf-8")
    logger.info(
        f"✅ docker-compose.yml обновлён для {full_repo} (тег: {tag}, порт: {external_port})"
    )
    return compose_file


@app.route("/webhook", methods=["POST"])
def webhook():
    # === Валидация подписи ===
    sig = request.headers.get("X-Hub-Signature-256")
    payload = request.get_data()
    if not verify_signature(payload, sig):  # type: ignore
        logger.warning("❌ Неверная подпись вебхука")
        abort(403, description="Invalid signature")

    # === Парсинг и валидация данных ===
    try:
        data = request.get_json()
        if not data:
            abort(400, description="Empty payload")
        full_repo = data.get("repo", "").strip()
        tag = data.get("tag", "").strip()
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга JSON: {e}")
        abort(400, description="Invalid JSON")

    if (
        not full_repo
        or full_repo.count("/") != 1
        or not all(c.isalnum() or c in "-_./" for c in full_repo)
    ):
        logger.error(f"❌ Некорректный формат репозитория: {full_repo}")
        abort(400, description="Invalid repo format")
    if not tag:
        logger.error("❌ Отсутствует тег в запросе")
        abort(400, description="Tag is required")

    owner, repo_name = full_repo.split("/", 1)
    repo_path = DEPLOY_ROOT / owner / repo_name
    repo_path.mkdir(parents=True, exist_ok=True)

    try:
        compose_file = ensure_compose_file(repo_path, full_repo, tag)
    except Exception as e:
        logger.exception(f"Ошибка создания docker-compose.yml: {e}")
        return jsonify(
            {"error": "Ошибка создания docker-compose.yml", "details": str(e)}
        ), 500

    # === Выполнение docker compose команд ===
    try:
        logger.info(f"🔄 Запуск деплоя {full_repo}:{tag} в {repo_path}")

        ensure_external_network("db-net")

        # Pull с явным указанием файла (надёжнее)
        pull = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "pull"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if pull.returncode != 0:
            logger.error(
                f"❌ docker compose pull failed:\nSTDOUT: {pull.stdout}\nSTDERR: {pull.stderr}"
            )
            return jsonify(
                {"error": "Pull failed", "stdout": pull.stdout, "stderr": pull.stderr}
            ), 500

        # Up
        up = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(compose_file),
                "up",
                "-d",
                "--remove-orphans",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if up.returncode != 0:
            logger.error(
                f"❌ docker compose up failed:\nSTDOUT: {up.stdout}\nSTDERR: {up.stderr}"
            )
            return jsonify(
                {"error": "Deploy failed", "stdout": up.stdout, "stderr": up.stderr}
            ), 500

        logger.info(
            f"✅ Успешный деплой {full_repo}:{tag} на порту {get_port_for_repo(owner, repo_name)}"
        )
        return jsonify(
            {
                "status": "success",
                "repo": full_repo,
                "tag": tag,
                "port": get_port_for_repo(owner, repo_name),
                "message": "Deployed successfully",
            }
        ), 200

    except subprocess.TimeoutExpired:
        logger.exception("💥 Таймаут выполнения docker compose")
        return jsonify({"error": "Deployment timeout"}), 500
    except Exception as e:
        logger.exception(f"💥 Критическая ошибка деплоя: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500
