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

## Структура репозитория

- `webhook_deployer.py` - endpoint Flask и логика деплоя.
- `docker-compose.yml` - конфигурация запуска этого webhook-сервиса.
- `Dockerfile` - образ сервиса (Python + Docker CLI + Compose plugin).
- `.github/workflows/CI.yml` - workflow сборки и публикации в GHCR.

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

## Примечания по CI/CD

Workflow запускается при пуше тега и публикует образы:

- `ghcr.io/<owner>/<repo>:<tag>`
- `ghcr.io/<owner>/<repo>:latest`

Если тег содержит не более 3 сегментов, разделенных точкой, дополнительно
создается GitHub Release.

## Troubleshooting

- `403 Invalid signature`
	- Не совпадает секрет или подпись рассчитана по измененному JSON.
- `400 Invalid repo format`
	- `repo` должен быть в формате `owner/name`.
- `500 Pull failed`
	- Образ/тег отсутствует в GHCR или проблема сети/авторизации.
- `500 Deploy failed`
	- Ошибка compose-конфига, конфликт порта или ошибка Docker runtime.

Проверьте логи сервиса:

```bash
docker compose logs -f webhook
```