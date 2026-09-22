# Развёртывание на VPS

Инструкция разворачивает весь стек (SPA + FastAPI + Postgres + Keycloak + Redis + MinIO
за краевым nginx) на одной машине через `docker compose`. Рассчитана на **пилот на один
отдел**: один VPS, без HA, без Kubernetes.

Перед включением реальных пользователей прочитайте
[`backend/docs/prod-readiness-2026-09-20.md`](backend/docs/prod-readiness-2026-09-20.md) —
там перечислено то, чего в системе ещё нет (CI, мониторинг, разделение зон данных между
отделами) и что требует управленческого решения (передача ПДн внешним AI-провайдерам).

---

## 0. Что получится

| Компонент | Где живёт | Доступ снаружи |
|---|---|---|
| `nginx` (краевой) | контейнер | **443/80 — единственный публичный вход** |
| `frontend` (SPA) | контейнер | только через nginx, `https://APP_HOST/` |
| `app` (FastAPI, gunicorn ×4) | контейнер | только через nginx, `https://APP_HOST/api/v1/…` |
| `keycloak` | контейнер | только через nginx, `https://KC_HOSTNAME/` |
| `postgres`, `keycloak-db`, `redis`, `minio` | контейнеры | **нет**, только внутри docker-сети |

Публичные имена: `APP_HOST` (приложение), `KC_HOSTNAME` (Keycloak) и `www.APP_HOST`
(краевой nginx редиректит его на `APP_HOST`). Все — A-записи на один и тот же IP,
один сертификат с тремя SAN.

MinIO краевой nginx отдаёт по TLS на порту **9443, который не публикуется на хост** —
это нужно только для того, чтобы бэкенд ходил в объектное хранилище по `https://`
(в прод-режиме `validate_for_production()` отвергает `http://`-эндпоинт S3).

---

## 1. Требования к VPS

- Ubuntu 22.04/24.04, **4 vCPU / 8 GB RAM / 60 GB SSD** (минимум 2 vCPU / 4 GB + 4 GB swap —
  сборка фронта `npm ci && vite build` прожорлива по памяти).
- Docker Engine 24+ и плагин `docker compose` v2, `git`, `make`, `python3`, `openssl`, `certbot`.
- Открыты входящие **22, 80, 443**; исходящий доступ к hub.docker.com и npm/PyPI (иначе
  образы надо собирать заранее и привозить).
- Данные (заявки, документы) — в docker volume'ах на этом же диске; см. §9 «Бэкапы».

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git make python3 certbot
sudo usermod -aG docker "$USER" && newgrp docker
sudo ufw allow 22,80,443/tcp && sudo ufw enable
```

## 2. DNS

Три A-записи на IP сервера, например:

```
korxona.com        A   203.0.113.10     # APP_HOST
www.korxona.com    A   203.0.113.10     # 301 -> korxona.com
auth.korxona.com   A   203.0.113.10     # KC_HOSTNAME
```

Дождитесь, пока все три резолвятся (`dig +short korxona.com`) — без этого не выпустится
сертификат.

## 3. Код и конфигурация

```bash
git clone <URL репозитория> /opt/bap && cd /opt/bap
make env-prod                 # создаст .env.prod из .env.prod.example
chmod 600 .env.prod
$EDITOR .env.prod
```

Заполните **каждый** `CHANGE_ME`. Обязательный минимум:

| Переменная | Значение |
|---|---|
| `APP_HOST`, `KC_HOSTNAME` | публичные имена из §2 |
| `PUBLIC_BASE_URL`, `FRONTEND_BASE_URL` | `https://<APP_HOST>` |
| `KEYCLOAK_PUBLIC_ISSUER` | `https://<KC_HOSTNAME>/realms/bap` |
| `TRUSTED_HOSTS`, `CORS_ORIGINS` | `<APP_HOST>` и `https://<APP_HOST>` |
| `POSTGRES_PASSWORD`, `KC_DB_PASSWORD`, `REDIS_PASSWORD` | `openssl rand -base64 24` каждый |
| `KEYCLOAK_ADMIN_PASSWORD` | ≥12 символов (политика реалма) |
| `OIDC_CLIENT_SECRET`, `KEYCLOAK_ADMIN_CLIENT_SECRET` | `openssl rand -base64 32` каждый (в §5 положим те же значения в реалм) |
| `SESSION_ENCRYPTION_KEYS` | `openssl rand -base64 32` — AES-256 ключ шифрования токенов в Redis |
| `S3_ENDPOINT_URL` | `https://<APP_HOST>:9443` (встроенный MinIO) либо внешний https-S3 |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | `openssl rand -base64 24` каждый |

`make gen-key` на VPS не работает (нужен `backend/.venv`) — используйте `openssl rand -base64 32`,
формат тот же.

**В значениях не должно быть `$`.** Docker Compose подставляет `$VAR` прямо в `.env.prod`,
и пароль молча приезжает в контейнер обрезанным (в логе это `WARN The "xxxx" variable is
not set`). Проверьте `grep '\$' .env.prod` — если что-то нашлось, перегенерируйте секрет
(`openssl rand -base64 32` символ `$` не выдаёт) или удвойте: `$$`.

AI-функции (`OPENAI_API_KEY`, `TRANSCRIPTION_URL`) оставьте **пустыми**, пока передача ПДн
внешним провайдерам не согласована письменно: пустой ключ = функция выключена (503),
остальная система работает.

Приложение стартует с `APP_ENV=prod`, и `validate_for_production()` не даст ему подняться
с http-URL, слабым секретом, Redis без пароля или без ключа шифрования сессий — ошибка
будет в логах `app` с точным списком нарушений.

## 4. TLS-сертификат

Один сертификат на все три имени (порт 80 в этот момент должен быть свободен):

```bash
sudo certbot certonly --standalone -d korxona.com -d www.korxona.com -d auth.korxona.com \
     --agree-tos -m NShilov@beeline.uz --non-interactive
sudo mkdir -p /opt/bap/nginx/certs
sudo cp /etc/letsencrypt/live/korxona.com/{fullchain.pem,privkey.pem} /opt/bap/nginx/certs/
```

Автопродление (certbot ставит таймер сам; добавьте хук, который копирует и перечитывает nginx):

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/bap.sh >/dev/null <<'SH'
#!/bin/sh
cp /etc/letsencrypt/live/korxona.com/fullchain.pem /opt/bap/nginx/certs/
cp /etc/letsencrypt/live/korxona.com/privkey.pem   /opt/bap/nginx/certs/
cd /opt/bap && docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T nginx nginx -s reload
SH
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/bap.sh
```

## 5. Подготовка реалма Keycloak — **до первого запуска**

Экспорт реалма (`backend/keycloak/realm-export.json`) импортируется **только один раз**,
при создании реалма. Всё, что не поправлено сейчас, потом правится руками в админке.

```bash
cd /opt/bap

# 5.1 Удалить демо-учётки (demo/requester/ba/business_owner/executor/approver
#     с общим паролем demo-password-123).
python3 backend/scripts/remove_demo_user.py

# 5.2 Прописать прод-URL редиректа и реальные секреты клиентов из .env.prod.
python3 - <<'PY'
import json, pathlib
env = {}
for line in pathlib.Path(".env.prod").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
p = pathlib.Path("backend/keycloak/realm-export.json")
realm = json.loads(p.read_text(encoding="utf-8"))
app = f"https://{env['APP_HOST']}"
for c in realm["clients"]:
    if c["clientId"] == env.get("OIDC_CLIENT_ID", "bap-backend"):
        c["redirectUris"] = [f"{app}/api/v1/auth/callback"]
        c["webOrigins"] = [app]
        c["secret"] = env["OIDC_CLIENT_SECRET"]
    if c["clientId"] == env.get("KEYCLOAK_ADMIN_CLIENT_ID", "bap-backend-admin"):
        c["secret"] = env["KEYCLOAK_ADMIN_CLIENT_SECRET"]
p.write_text(json.dumps(realm, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("realm-export.json patched for", app)
PY

chmod 600 backend/keycloak/realm-export.json
```

> После этих правок рабочее дерево содержит секреты — **не коммитьте** `realm-export.json`
> с этой машины (`git checkout backend/keycloak/realm-export.json` перед любым коммитом).

## 6. Первый запуск

```bash
make prod-up          # сборка образов + старт всего стека
make prod-ps
make prod-logs svc=app
make prod-migrate     # миграции public-схемы (реестр тенантов, аудит)
```

Проверка:

```bash
curl -s https://korxona.com/api/v1/healthz          # {"status":"ok"}
curl -s https://korxona.com/api/v1/readyz           # db + jwks = ok
curl -sI https://auth.korxona.com/realms/bap        # 200 от Keycloak
```

`make seed-demo` в проде **не запускать** — это демо-тенант из шаблона.

## 7. Тенант и пользователи

```bash
DCP="docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml"

# 7.1 Тенант: строка в реестре + группа в Keycloak + схема tenant_<slug> + бакет MinIO
$DCP exec -T app python scripts/provision_tenant.py --slug beeline --name "Beeline UZ"

# 7.2 Узнать tenant_id (понадобится для пользователей)
$DCP exec -T postgres psql -U app -d app -c "SELECT id, slug FROM public.tenants;"
```

Справочник отделов заполняется под конкретный отдел пилота:

```bash
$DCP exec -T postgres psql -U app -d app -c \
  "INSERT INTO tenant_beeline.departments (id, name, description, is_active, created_at, updated_at)
   VALUES (gen_random_uuid(), 'Разработка ПО', 'Продуктовая разработка', true, now(), now());"
```

Пользователи заводятся в админке Keycloak (`https://auth.korxona.com/admin`, логин из
`KEYCLOAK_ADMIN`/`KEYCLOAK_ADMIN_PASSWORD`), для каждого:

1. **Users → Add user**: username, email, Email verified = On.
2. Вкладка **Attributes**: `tenant_id` = UUID из шага 7.2. **Без этого атрибута вход не
   работает** — тенант берётся из claim в токене, больше ниоткуда.
3. Вкладка **Role mapping**: всем `tenant_user` (это и есть «Заявитель»), плюс по роли —
   `ba`, `business_owner`, `executor`, `approver`, `tenant_admin`.
4. Вкладка **Credentials**: пароль ≥12 символов, не совпадающий с username/email
   (политика реалма), Temporary по вкусу.

Проверка сквозного пути: `https://korxona.com` → редирект на Keycloak → вход →
создание заявки.

## 8. Обновление версии и откат

```bash
cd /opt/bap
git fetch && git checkout <tag|commit>
make prod-up            # пересборка изменившихся образов + рестарт
make prod-migrate       # миграции public-схемы
# миграции схем тенантов (для каждого tenant_<slug>):
$DCP exec -T app alembic -x scope=tenant -x schema=tenant_beeline upgrade tenant@head
```

Откат: `git checkout <предыдущий tag> && make prod-up`. **Миграции назад не откатываются
автоматически** — если релиз содержал несовместимую миграцию, восстанавливайте БД из
бэкапа (§9).

Только фронт (без перезапуска бэкенда): `make fe-rebuild ENV=prod`.

## 9. Бэкапы

Бэкапить нужно **оба** хранилища: Postgres (заявки, требования, аудит) и том MinIO
(приложенные документы). Потеря любого = потеря части системы записи.

```bash
sudo tee /usr/local/bin/bap-backup.sh >/dev/null <<'SH'
#!/bin/bash
set -euo pipefail
cd /opt/bap
DCP="docker compose --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml"
DEST=/var/backups/bap/$(date +%F)
mkdir -p "$DEST"
$DCP exec -T postgres pg_dump -U app -d app --format=custom > "$DEST/app.dump"
$DCP exec -T keycloak-db pg_dump -U keycloak -d keycloak --format=custom > "$DEST/keycloak.dump"
docker run --rm -v bap_minio-data:/data:ro -v "$DEST":/backup alpine \
  tar czf /backup/minio-data.tar.gz -C /data .
find /var/backups/bap -maxdepth 1 -type d -mtime +14 -exec rm -rf {} +
SH
sudo chmod +x /usr/local/bin/bap-backup.sh
echo "30 2 * * * root /usr/local/bin/bap-backup.sh" | sudo tee /etc/cron.d/bap-backup
```

Имя тома (`bap_minio-data`) зависит от `COMPOSE_PROJECT_NAME` — проверьте `docker volume ls`.

Восстановление (проверьте процедуру **до** запуска пилота, а не после инцидента):

```bash
$DCP stop app
$DCP exec -T postgres psql -U app -d postgres -c "DROP DATABASE app; CREATE DATABASE app OWNER app;"
cat /var/backups/bap/<дата>/app.dump | $DCP exec -T postgres pg_restore -U app -d app
docker run --rm -v bap_minio-data:/data -v /var/backups/bap/<дата>:/backup alpine \
  sh -c "rm -rf /data/* && tar xzf /backup/minio-data.tar.gz -C /data"
$DCP start app
```

## 10. Эксплуатация

```bash
make prod-ps                       # состояние сервисов
make prod-logs svc=app             # логи (structlog, JSON в stdout)
$DCP restart app                   # перезапуск одного сервиса
$DCP exec postgres psql -U app -d app
```

Минимальный мониторинг, пока нет нормального: cron, дергающий `readyz`.

```bash
echo '*/5 * * * * root curl -fsS https://korxona.com/api/v1/readyz >/dev/null || logger -t bap "readyz FAILED"' \
  | sudo tee /etc/cron.d/bap-health
```

### Грабли, на которые наступают

- **`--import-realm` не переимпортирует уже созданный реалм.** Правка
  `realm-export.json` после первого запуска ни на что не влияет: меняйте через админку
  Keycloak либо (в крайнем случае, с потерей всех учёток) `docker volume rm bap_keycloak-db-data`.
- **Провижининг тенанта неатомарен.** Сбой посередине оставляет строку в `public.tenants`
  без схемы или без группы в Keycloak — чистить вручную (`DROP SCHEMA tenant_<slug>`,
  `DELETE FROM public.tenants WHERE slug=…`, удалить группу в админке) и повторить.
- **Нет `tenant_id` у пользователя → 401/403 сразу после успешного входа в Keycloak.**
  Первое, что нужно проверять при жалобе «вошёл, но ничего не открывается».
- **Схемы тенантов мигрируют по одной** — после релиза с миграцией `tenant@head`
  прогоните её для каждого `tenant_<slug>` (§8), иначе тенант упадёт на первом запросе.
- **Сертификат один на все имена.** Если добавляете ещё одно имя — перевыпускайте
  сертификат целиком (`certbot certonly -d … -d … -d …`), частично он не дополняется.

### Что в этой сборке осознанно не сделано

- Нет CI и сканирования зависимостей — гейты (`make check`) гоняются руками.
- Нет метрик/трейсинга/алертов — только логи в stdout и cron из §10.
- Разделение зон данных между отделами не реализовано: чтение заявок общее в пределах
  тенанта. Для пилота на один отдел приемлемо, для второго отдела — нет.
- AI-интейк и распознавание речи выключены по умолчанию (передача ПДн за пределы РУз).
