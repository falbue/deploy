# Deploy

Сервис деплоя Docker по webhook

Этот проект запускает небольшой Flask-сервис, который принимает подписанные
webhook-запросы, обновляет `docker-compose.yml` для конкретного репозитория и
деплоит контейнеры из GitHub Container Registry (GHCR).

## Что делает проект

- Принимает `POST /webhook` с данными о репозитории и теге.
- Проверяет HMAC-подпись `X-Hub-Signature-256`.
- Создает/обновляет директорию деплоя: `/apps/<owner>/<repo>`.
- Генерирует `docker-compose.yml` с нужным тегом образа.
- Проверяет наличие внешней сети `db-net` и создает её при отсутствии.
- Выполняет:
	- `docker compose pull`
	- `docker compose up -d --remove-orphans`
- Возвращает статус деплоя и назначенный внешний порт.

## Архитектура

Контейнер сервиса содержит Docker CLI и Compose plugin и использует:

- `/var/run/docker.sock` (монтируется только для чтения) для управления Docker-демоном хоста.
- `/opt/deploy`, смонтированную в `/apps`, как корень деплоев.

Основной поток:

1. CI собирает образ и пушит его в GHCR при пуше тега.
2. Внешняя система отправляет подписанный webhook с `repo` и `tag`.
3. Сервис валидирует подпись и payload.
4. Сервис записывает compose-файл и пере-деплоивает целевое приложение.

## docker-compose.yml

```yaml
services:
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
    external: true
```

Перед деплоем сервис автоматически выполняет проверку сети `db-net`
и создаёт её, если сеть отсутствует.

## Контракт для деплоимых проектов

Все приложения, которые деплоятся через этот сервис, должны слушать
внутренний порт `5000` внутри контейнера.

**Причина:** compose-файл генерируется в формате:

```yaml
ports:
  - "<external_port>:5000"
```

Если приложение внутри контейнера слушает другой порт, оно поднимется,
но будет недоступно снаружи.

Рекомендуется в Dockerfile проекта:

```dockerfile
EXPOSE 5000
```

И запускать сервер внутри контейнера на `0.0.0.0:5000`.

## Переменные окружения

Из `.env-template`:

- `WEBHOOK_SECRET` (обязательно): общий секрет для HMAC-валидации.

Для рантайма сервиса:

- `DEPLOY_ROOT` (необязательно, по умолчанию `/apps`): корневая папка для деплоев.

## Локальный запуск (Docker Compose)

1. Создайте env-файл:

```bash
cp .env-template .env
# укажите WEBHOOK_SECRET в .env
```

2. Запустите сервис:

```bash
docker compose up -d
```

3. Endpoint сервиса:

- `http://127.0.0.1:1500/webhook`

Отдельно создавать сеть `db-net` вручную не требуется: сервис сделает это сам при первом деплое.

## Контракт webhook

### Запрос

- Метод: `POST`
- Путь: `/webhook`
- Заголовок: `X-Hub-Signature-256: sha256=<hex_digest>`
- Тело (JSON):

```json
{
	"repo": "owner/repository",
	"tag": "1.2.3"
}
```

### Подпись

Подпись считается по точному сырому телу запроса:

`sha256=` + `HMAC_SHA256(WEBHOOK_SECRET, raw_body)`

### Успешный ответ

```json
{
	"status": "success",
	"repo": "owner/repository",
	"tag": "1.2.3",
	"port": 2001,
	"message": "Deployed successfully"
}
```

## Назначение портов

Внешние порты назначаются детерминированно на основе отсортированных
директорий owner/repository внутри `DEPLOY_ROOT`:

- База для owner: `2000 + owner_index * 1000`
- Для repository: `base + repo_index + 1`

Это сохраняет стабильность порта при неизменном порядке директорий.



