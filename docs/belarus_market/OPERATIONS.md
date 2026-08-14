# Настройка публичного сайта без кода

## Что является настройкой, а что — возможностью платформы

URL, allowed domains, транспорт, selectors, JSONPath, pagination, list→detail,
tabs, документы, mapping, rules отбора, expected states, coverage, fixtures и
cron — это данные SourcePresetRevision и Workflow. Пользователь меняет их через
UI/API, создавая новую revision; для этого не нужно изменять Python/TypeScript.

Движок даёт только универсальные операции: HTTP/public API/browser after
postcondition, table/matrix extraction, pagination, document MIME routing,
normalisation, field evidence, validation и output.

## Пользовательский путь

1. Создайте проект и источник в разделе **Источники**. Профилировщик помогает
   обнаружить публичный HTML/API, selectors и доступные документы. Не добавляйте
   логин, cookies, private API или действия обхода CAPTCHA.
2. Откройте **Пресеты источников** → **Создать preset**. Укажите URL, transport,
   dataset/schema, сегмент и статус `DRAFT`. Политика `public-anonymous-only`
   применяется всегда.
3. Нажмите **Создать workflow** и откройте его в **Workflows**. В визуальном
   редакторе настройте scoped containers, selectors/JSONPath, list→detail,
   pagination, tabs/state matrix, document selection, mapping/normalisation и
   expected coverage. `Advanced JSON` доступен только для расширенных
   декларативных параметров — пользовательский код не выполняется.
4. Сохраните публичную fixture (body/JSON/document без cookies, headers и
   секретов), затем добавьте её ссылку в новую revision preset. Выполните
   fixture regression и ручной test run.
5. Запустите anonymous-public live smoke. Он должен доказать transport,
   source-role и useful data либо допустимое пустое окно; отсутствие доступа
   фиксируйте как `BLOCKED` с причиной.
6. Только после fixture и passing live smoke создайте новую revision со статусом
   `VERIFIED`. Откройте **Расписания**, проверьте cron/timezone и включите
   schedule. DRAFT schedules, созданные Belarus pack, выключены по умолчанию.
7. Внешний ИИ сначала вызывает coverage, затем records; evidence подключается
   явно через `include=evidence`.

## Примеры API

```http
GET /api/v1/datasets/deposit-offers-legal/coverage
GET /api/v1/datasets/deposit-offers-retail/records?view=current&include=evidence
GET /api/v1/datasets/market-news/coverage?from=2026-08-13T21:00:00Z&to=2026-08-14T21:00:00Z
GET /api/v1/datasets/market-indicators/records?view=current
```

## Passport pack

`scripts/import_belarus_market_pack.py` создаёт 60 sources/preset revisions/
workflows/schedules: 21 ЮЛ, 20 ФЛ, 15 websites news и 4 indicators. Это
стартовая библиотека конфигураций, а не набор встроенных банковских парсеров.
Telegram не импортируется. Все rows начинают с `DRAFT`; пользователь вправе
изменить и подтвердить каждую revision в UI.
