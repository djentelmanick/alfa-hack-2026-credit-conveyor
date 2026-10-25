# Бэкенд кредитного конвейера

FastAPI-бэкенд «Умного кредитного кабинета предпринимателя». Архитектура описана в `docs/BACKEND_SPEC.md`,
контракт с фронтендом в `docs/API.md`, этапы работы в `docs/PHASES.md`.

## Запуск

```bash
docker compose up --build -d                             # api, worker, rabbitmq, postgres, minio
docker compose exec api python -m app.infra.mocks.seed   # тестовые учётки
curl http://localhost:8000/api/v1/health                 # {"status":"ok","db":"ok","s3":"ok"}
docker compose logs -f worker                            # конвейер, reconciler, истечение сроков
```

`api` принимает запросы и держит WebSocket, проверку по источникам и решение выполняет отдельный процесс
`worker`. Задачи и события идут через RabbitMQ.

Миграции контейнер `api` применяет сам при каждом старте (`alembic upgrade head` перед uvicorn), поэтому
пустая база поднимается одной командой. Начать демо с нуля: `docker compose down -v`, затем три команды выше.

| Адрес | Что там |
|---|---|
| http://localhost:8000/docs | Swagger |
| http://localhost:9001 | консоль MinIO (`minioadmin` / `minioadmin`) |
| http://localhost:15672 | консоль RabbitMQ (`app` / `app`), очереди `applications.process` и `applications.process.dlq` |
| localhost:5432 | PostgreSQL (`app` / `app`, БД `credit`) |

Compose берёт переменные из `.env.example`. Чтобы поменять значения, создайте рядом `.env`:
он переопределит `.env.example` и в git не попадёт.

MinIO запускается из образов `pgsty/minio` и `pgsty/mc`: официальные образы MinIO больше не публикуются.

## Тестовые учётки

`seed.py` создаёт руководителя каждой компании из SCOPE, раздел 12, и аналитика. Пароль у всех один:
значение `SEED_PASSWORD` (в `.env.example` это `demo-password-2026`). Повторный запуск ничего не дублирует.

| Логин | Роль | Кто | Компания, ИНН | Сценарий SCOPE |
|---|---|---|---|---|
| `kolos` | CLIENT | Петров Алексей Николаевич | ООО «Пекарня Колос», 7714012340 | 1, одобрение |
| `romashka` | CLIENT | Смирнова Ольга Викторовна | ООО «Ромашка», 7703123457 | 2, стоп-фактор |
| `sokolov` | CLIENT | Соколов Дмитрий Андреевич | ИП Соколов, 770498765403 | 3, контрпредложение |
| `ivanova` | CLIENT | Иванова Елена Сергеевна | ИП Иванова, 770512345650 | 4, дозапрос выписки |
| `techprom` | CLIENT | Кузнецов Игорь Валентинович | ООО «Техпром», 7704123450 | 5, ручная проверка |
| `gruz` | CLIENT | Морозов Андрей Викторович | ООО «Быстрый груз», 7717045671 | 6, отказ по классу D |
| `buh` | CLIENT | Васильева Анна Игоревна, бухгалтер по МЧД | ООО «Хлебный двор», 7715012342 | представитель, одобрение |
| `petrova` | CLIENT | собственник 30% по нотариальной доверенности | ООО «Пекарня Петровых», 7716012345 | представитель, одобрение |
| `lebedev` | CLIENT | учредитель 30%, МЧД без права подписи | ООО «Техпром», 7704123450 | представитель, отказ на шаге доверенности |
| `analyst` | ANALYST | Орлова Мария Игоревна | | |

## Как пройти сценарии на демо

Все запросы идут в Swagger (http://localhost:8000/docs): `POST /auth/login` с логином из таблицы выше,
затем кнопка Authorize с `access_token`. Статусы заявки приходят по WebSocket `/api/v1/ws/applications`
(JWT в `Sec-WebSocket-Protocol: bearer, <token>`), в Swagger их удобно смотреть через `POST /applications/get`.

Скоринг считает `ScoringEngine` (10 факторов, классы A–D, `docs/BACKEND_SPEC.md` раздел 7), поэтому сценарии идут ровно по таблице SCOPE раздел 12. Те же шаги с готовыми телами запросов собраны в коллекции `docs/credit-conveyor.postman_collection.json`, описание сценариев в `../docs/TEST_SCENARIOS.md`.

Общий шаг клиента: `POST /consents/give` (ООО: `BKI_COMPANY`, `BKI_PERSONAL`, `PERSONAL_DATA`; ИП: без `BKI_COMPANY`),
`POST /applications/create`, `POST /applications/submit`.

| # | Логин | Заявка | Что происходит | Что сделать дальше |
|---|---|---|---|---|
| 1 | `kolos` | `LUMP_SUM`, `UNSECURED`, `2000000.00`, 12 мес, `DEVELOPMENT`, `owner_guarantee_consent: true` | `APPROVED`, класс A, ставка `23.5` | клиент `accept`, `signing/code`, `signing/confirm`: договор |
| 2 | `romashka` | `LUMP_SUM`, `UNSECURED`, `1000000.00`, 12 мес | `CHECKING`, затем `REJECTED`: стоп-фактор `COMPANY_INACTIVE` (ликвидирована) | ничего, это конечный исход |
| 3 | `sokolov` | `LUMP_SUM`, `UNSECURED`, `4500000.00`, 24 мес | `COUNTER_OFFER`, класс B: `3600000.00` без залога и `4500000.00` с залогом, ставка `26.5` | клиент `accept` с `offer_id` варианта без залога, затем подписание |
| 4 | `ivanova` | `LUMP_SUM`, `UNSECURED`, `3000000.00`, 24 мес; выписка `ivanova_4m.txt` | `NEEDS_INFO`: выписка короче 6 месяцев | загрузить `ivanova_12m_signed.txt`, снова `submit`; дальше `APPROVED`, класс B, `26.5` |
| 5 | `techprom` | `REVOLVING_LINE`, `SECURED`, `12000000.00`, 60 мес, `REGULAR_PURCHASES`, залог `REAL_ESTATE` на `9000000.00` | `MANUAL_REVIEW`: залог и сумма больше 5 млн | аналитик `approve` по `23` с комментарием; клиент подписывает: открыт лимит |
| 6 | `gruz` | `LUMP_SUM`, `UNSECURED`, `1500000.00`, 24 мес, `owner_guarantee_consent: true` | `REJECTED`, класс D (−20): в `reasons` пояснения факторов, снизивших балл | ничего, это конечный исход |

Выписки сценария 4 лежат в `app/infra/mocks/data/statements/` и загружаются через
`POST /applications/statements/upload` (multipart: `application_id`, `file`). Свежесть выписки считается от текущей даты,
поэтому для демо в другой день файлы перегенерируют:
`docker compose exec api python -m app.infra.mocks.statement_samples --end <вчера> --out /tmp/statements`.

Аналитик (`analyst`) видит очередь в `POST /analyst/applications/list` с `{"queue": "manual"}` и отчёт по заявке
в `POST /analyst/applications/report`. Комментарий к каждому действию обязателен.

## Договор после выдачи и эмуляция

Страница договора: `POST /contracts/get`, график `POST /contracts/schedule`, журнал `POST /contracts/operations`,
версии `POST /contracts/versions`, PDF версии `POST /contracts/document-url`. Операции проводит клиент:

- кредит одной суммой: `POST /contracts/repay` с `{"kind": "PARTIAL", "amount": "500000.00"}` пересчитывает график
  (платёж меньше, срок прежний) и создаёт версию договора с новым PDF; `{"kind": "FULL"}` закрывает договор;
- ВКЛ: `POST /contracts/tranches/draw` (от `50000.00`, не больше доступного, срок транша 6 мес),
  `POST /contracts/tranches/repay` возвращает погашенное в доступный лимит.

Эмуляция времени работает только при `DEMO_MODE=true` (в `.env.example` включено) и двигает дату одного договора,
сроки заявок она не трогает:

- `POST /contracts/emulation/next-month`: месяц вперёд, плановые платежи, проценты по траншам, гашение истёкших траншей;
- `POST /contracts/emulation/rewind` с `target_date` (от текущей даты договора до его окончания);
- `POST /contracts/emulation/reset`: договор как сразу после выдачи, сценарий можно пройти заново.

Демо по SCOPE: у сценария 1 три раза `next-month`, частичное погашение, `rewind` до даты окончания: договор `CLOSED`.
У сценария 5 два транша, например `1000000.00` и `2000000.00`, погашение первого: доступно снова `10000000.00`.

## Как сломать источник

Для демо недоступности реестра аналитик включает поломку мока по паре «источник, ИНН» (только при `DEMO_MODE=true`):

```bash
POST /api/v1/dev/mocks/failures
{"source": "BKI", "inn": "7714012340", "enabled": true}
```

`source` из `EGRUL`, `BKI`, `TURNOVER`, `FNS`, `GIR_BO`, `FSSP`, `CBR_KYC`, `ROSFIN`, `BANK_SIGNATURE`. После
`SOURCE_RETRY_ATTEMPTS` попыток заявка уходит в `MANUAL_REVIEW` с причиной `SOURCE_UNAVAILABLE`, в отчёте у источника
статус `FAILED`, а `approve` отвечает 409 `SOURCES_NOT_READY`. Аналитик выключает поломку (`"enabled": false`)
и делает `POST /analyst/applications/recheck`. Поломки живут в памяти процесса и сбрасываются при перезапуске `api`.

## Разработка

Нужен [uv](https://docs.astral.sh/uv/) и Python 3.15 (`uv python install 3.15`).

```bash
uv sync                       # зависимости из uv.lock
uv run pytest -q              # нужны PostgreSQL и MinIO, см. ниже
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

Тесты репозиториев (`tests/integration/db`) и e2e (`tests/e2e`) идут на настоящем PostgreSQL: перед прогоном они пересоздают базу
из `TEST_DATABASE_URL` (по умолчанию `postgresql+asyncpg://app:app@localhost:5432/credit_test`, это postgres
из compose) и накатывают миграции. Рабочая база `credit` не затрагивается. Если порт 5432 на машине занят
другим PostgreSQL, поднимите отдельный контейнер на свободном порту и укажите его в `TEST_DATABASE_URL`.

Тесты хранилища (`tests/integration/storage`) и e2e ходят в MinIO из compose (`TEST_S3_ENDPOINT`, по умолчанию
`http://localhost:9000`, ключи `TEST_S3_ACCESS_KEY` и `TEST_S3_SECRET_KEY`, по умолчанию `minioadmin`) и пишут
в отдельные бакеты `credit-files-test` и `credit-contracts-test`, которые создают сами.

Новая зависимость: `uv add <пакет>` (или `uv add --dev`), затем строка в `THIRD_PARTY_NOTICES.md` в корне репозитория.

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DATABASE_URL` | обязательная | подключение к БД, `postgresql+asyncpg://...` |
| `JWT_SECRET` | обязательная, от 32 символов | подпись JWT |
| `JWT_TTL_MINUTES` | `60` | время жизни токена |
| `S3_ENDPOINT` | обязательная | адрес S3 изнутри docker-сети |
| `S3_PUBLIC_ENDPOINT` | обязательная | адрес S3 для presigned-ссылок, видимый из браузера |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | обязательные | доступ к S3, они же root-учётка MinIO в compose |
| `S3_REGION` | `us-east-1` | регион S3 |
| `S3_BUCKET_FILES` | `credit-files` | бакет выписок |
| `S3_BUCKET_CONTRACTS` | `credit-contracts` | бакет договоров |
| `RABBITMQ_URL` | обязательная | подключение к RabbitMQ, `amqp://...` (api и worker) |
| `WORKER_PREFETCH` | `4` | сколько задач один worker обрабатывает одновременно |
| `RECONCILE_INTERVAL_SEC` | `30` | как часто reconciler ищет зависшие заявки |
| `PIPELINE_STALE_AFTER_SEC` | `120` | через сколько секунд без смены статуса заявка в конвейере считается зависшей |
| `PRESIGNED_TTL_SECONDS` | `300` | время жизни ссылки на файл |
| `MAX_UPLOAD_MB` | `10` | лимит выписки |
| `PIPELINE_STEP_DELAY_MS` | `800` | пауза на шаге конвейера |
| `SOURCE_RETRY_ATTEMPTS` | `3` | число попыток обращения к источнику |
| `SOURCE_RETRY_PAUSE_MS` | `500` | пауза между попытками |
| `OFFER_TTL_DAYS` | `7` | срок одобрения и контрпредложения |
| `SIGNING_CODE_TTL_SECONDS` | `300` | время жизни кода подписания |
| `DEMO_MODE` | `false` (в `.env.example` `true`) | включает `/dev/*` и эмуляцию |
| `CORS_ORIGINS` | пусто | адреса фронтенда через запятую |
| `SEED_PASSWORD` | пусто | пароль тестовых учёток, нужен только `seed.py` |
| `TEST_DATABASE_URL` | см. «Разработка» | база для тестов репозиториев и e2e, читают только тесты |
| `TEST_S3_ENDPOINT`, `TEST_S3_ACCESS_KEY`, `TEST_S3_SECRET_KEY` | см. «Разработка» | MinIO для тестов хранилища и e2e, читают только тесты |
