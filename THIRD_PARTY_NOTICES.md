# Сторонние компоненты

Все компоненты бесплатные и с открытым исходным кодом. Точные версии зафиксированы: Python-пакеты, включая транзитивные, в `backend/uv.lock`, npm-пакеты в `frontend/package-lock.json`, образы в `docker-compose.yml`, `backend/docker-compose.yml` и Dockerfile с точными тегами.

## Python-пакеты бэкенда

| Пакет | Лицензия | Назначение |
|---|---|---|
| fastapi | MIT | веб-фреймворк |
| python-multipart | Apache-2.0 | разбор multipart/form-data (загрузка выписки) |
| uvicorn | BSD-3-Clause | ASGI-сервер |
| pydantic | MIT | DTO и валидация |
| pydantic-settings | MIT | настройки из окружения |
| sqlalchemy | MIT | ORM, async |
| asyncpg | Apache-2.0 | драйвер PostgreSQL |
| alembic | MIT | миграции |
| aioboto3 | Apache-2.0 | клиент S3 |
| aio-pika | Apache-2.0 | клиент RabbitMQ |
| pyjwt | MIT | JWT |
| argon2-cffi | MIT | хеширование паролей |
| pyyaml | MIT | чтение каталога продуктов `products.yaml` |
| reportlab | BSD-3-Clause | PDF договора |
| pillow | MIT-CMU | зависимость reportlab (изображения) |
| charset-normalizer | MIT | зависимость reportlab |
| pytest | MIT | тесты (dev) |
| pytest-asyncio | Apache-2.0 | async-тесты (dev) |
| httpx | BSD-3-Clause | HTTP-клиент для тестов (dev) |
| ruff | MIT | линтер и форматтер (dev) |
| mypy | MIT | проверка типов (dev) |

## npm-пакеты фронтенда

| Пакет | Лицензия | Назначение |
|---|---|---|
| react, react-dom | MIT | UI |
| react-router-dom, react-router | MIT | маршрутизация SPA |
| scheduler, loose-envify, js-tokens, cookie, set-cookie-parser | MIT | транзитивные зависимости react и react-router |
| vite | MIT | сборка и dev-сервер (dev) |
| @vitejs/plugin-react | MIT | JSX и fast refresh для vite (dev) |

UI-библиотек нет: компоненты и дизайн-токены написаны в проекте по макетам дизайн-системы «Кредитный кабинет», которые команда сделала в Claude Design (Anthropic). Сам сервис в работе решения не участвует.

## Шрифты

| Компонент | Версия | Лицензия | Назначение |
|---|---|---|---|
| DejaVu Sans (обычный и жирный) | 2.37 | Bitstream Vera, изменения DejaVu в public domain | кириллица в PDF договора, файлы и текст лицензии в `backend/app/infra/documents/fonts/` |

## Образы и инструменты

| Компонент | Версия | Лицензия | Назначение |
|---|---|---|---|
| python (Docker official image) | `3.15.1-slim` | PSF-2.0 | базовый образ `api`, `worker`, `seed` |
| uv (`ghcr.io/astral-sh/uv`) | `0.13.5` | MIT / Apache-2.0 | установка Python-зависимостей по `uv.lock` |
| node (Docker official image) | `26.12.1-alpine3.24` | MIT | сборка фронтенда |
| nginx (Docker official image) | `1.30.6-alpine` | BSD-2-Clause | раздача SPA и прокси `/api` |
| postgres (Docker official image) | `16.15-alpine3.24` | PostgreSQL License | БД |
| rabbitmq (Docker official image) | `4.3.7-management-alpine` | MPL-2.0 | очередь задач и события |
| MinIO, сборка `pgsty/minio` | `RELEASE.2026-08-04T00-00-00Z` | AGPL-3.0 | S3 локально, официальные образы MinIO больше не публикуются |
| MinIO Client, сборка `pgsty/mc` | `RELEASE.2026-10-18T00-00-00Z` | AGPL-3.0 | создание бакетов в `minio-init` |

MinIO используется как отдельный сервис без изменений исходного кода, по сети через S3 API.

## Модели

ML-моделей и LLM в решении нет. Скоринг — прозрачная балльная модель из 10 факторов с классами риска A–D (`docs/SCOPE.md`, раздел 6; `backend/app/domain/scoring`), правила решения заданы в коде и в `backend/app/infra/config/products.yaml`.

## Датасеты

| Данные | Происхождение | Где лежат |
|---|---|---|
| Компании, кредитные отчёты, обороты, налоговые данные, перечень Росфинмониторинга, доверенности, паспорта | синтетика, придумана командой под сценарии `docs/SCOPE.md`, раздел 12. Совпадения с реальными компаниями и людьми случайны | `backend/app/infra/mocks/data` |
| Выписки 1С сценария 4 | генерируются скриптом `python -m app.infra.mocks.statement_samples` | `backend/app/infra/mocks/data/statements` |
| Образцы документов ЕГРЮЛ, ЕГРИП, «Прозрачного бизнеса», ГИР БО, РМСП | общедоступные выписки с сайтов ФНС (`egrul.nalog.ru`, `pb.nalog.ru`, `bo.nalog.ru`, `rmsp.nalog.ru`), используются только как образец формата при проектировании моков | `docs/data-sources/samples` |
| Коды и названия ОКВЭД в синтетических компаниях | общедоступный классификатор ОК 029-2014 | `backend/app/infra/mocks/data/companies` |

## Внешние сервисы

Решение не обращается ни к одному внешнему сервису: всё работает локально в `docker compose`.

## Смоделированные интеграции

Каждая интеграция стоит за доменным портом (`backend/app/domain/ports.py`) и реализована мок-адаптером на синтетике (`backend/app/infra/mocks/adapters.py`) с повторами при сбое (`backend/app/infra/retrying.py`). Для демо любой источник можно «сломать» по ИНН через `POST /api/v1/dev/mocks/failures`. Форматы реальных источников и контракты моков описаны в `docs/data-sources`.

| Интеграция | Реальный источник | Мок | Что проверяем |
|---|---|---|---|
| ЕГРЮЛ / ЕГРИП | ФНС, `egrul.nalog.ru` | `MockEgrulAdapter` | статус компании, ОКВЭД, руководитель, учредители, возраст бизнеса |
| Кредитные бюро | НБКИ, ОКБ, Скоринг Бюро | `MockBkiAdapter` | просрочки, платежи по кредитам, закрытые кредиты, обращения за 3 месяца |
| Обороты по счёту | АБС банка | `MockTurnoverAdapter` | помесячная выручка и крупнейшие плательщики клиентов банка |
| Проверка ЭП банка на выписке | УКЭП банка-отправителя (КриптоПро) | `MockSignatureAdapter` | подлинность выписки 1С от клиента другого банка |
| «Прозрачный бизнес» | ФНС, `pb.nalog.ru` | `MockFnsAdapter` | блокировки счетов, массовые адрес, директор, учредитель, дисквалификация, налоговый долг |
| ГИР БО | ФНС, `bo.nalog.ru` | `MockFnsAdapter` | выручка ООО по бухотчётности для сверки с выпиской |
| Исполнительные производства | ФССП | `MockFsspAdapter` | открытые производства компании и директора |
| «Знай своего клиента» | платформа ЦБ | `MockCbrAdapter` | уровень риска (зелёный, жёлтый, красный) |
| Перечень экстремистов и террористов | Росфинмониторинг | `MockRosfinAdapter` | компания, директор, учредители и представитель |
| Реестр МЧД | ФНС, `m4d.nalog.gov.ru` | `MockMchdAdapter` | статус, срок и полномочия машиночитаемой доверенности |
| Реестр нотариальных доверенностей | ФНП, `reestr-dover.ru` | `MockNotaryPoaAdapter` | действует ли нотариальная доверенность |
| Проверка паспорта | МВД | `MockPassportAdapter` | действительность паспорта при регистрации |
| Подписание договора | ЭП или SMS-код банка | `SigningService` | 6-значный код, который показывается в интерфейсе вместо SMS |
