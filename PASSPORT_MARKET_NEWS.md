# Паспорт данных: ежедневный дайджест новостей белорусского рынка

**Проект:** Multiverse — Belarus Market Data Pack
**Dataset:** `market-news`
**Назначение Multiverse:** собрать, доказать, нормализовать и отдать по API полный поток релевантных публикаций.
**Назначение внешнего ИИ-агента:** после API — semantic deduplication, краткая factual выжимка, персонализация и доставка пользователю.
**Версия паспорта:** 1.0
**Исследование источников:** 13.08.2026
**Исходный бизнес-паспорт:** `_Паспорт_ Дайджест новостей белорусского рынка ежедневный(1).xlsx`

> Multiverse **не должен** сам создавать финальный Telegram-дайджест. Его контракт — проверенные исходные news records + coverage/assessment + provenance.

---

## 1. Бизнес-режим из исходного паспорта

- запуск в рабочие дни ориентировочно к **09:00**;
- обычный день: собрать публикации предыдущего календарного дня;
- понедельник: охватить пятницу + выходные до начала текущего рабочего окна;
- не отправлять повторно уже обработанные/отправленные публикации;
- не затягивать в текущий дайджест старые новости без явной причины;
- итог внешнего ИИ: кратко, фактологично, без прогнозов/советов/оценок;
- в пилоте — human review;
- неоднозначно релевантные записи не терять: `AMBIGUOUS` и отдельная очередь/еженедельный отчёт;
- отсутствие новостей в корректно проверенном периоде — **валидный пустой результат**, не ошибка.

### Временная модель

Парсер не должен понимать слова «вчера»/«понедельник» сам. Оркестратор/внешний ИИ вычисляет точный half-open interval:

```text
[from, to)
```

Для белорусских источников публикационные timestamps интерпретировать в `Europe/Minsk`, если сам источник не задаёт offset.

---

## 2. Целевая schema `MarketNewsRecord`

```json
{
  "source_id": "primepress-finance",
  "source_name": "PrimePress",
  "source_section": "Финансы",
  "source_authority": "PRIMARY_OR_MEDIA",

  "external_id": null,
  "canonical_url": "https://...",

  "title": "...",
  "summary_raw": "...",
  "body_text": "...",
  "body_html": null,

  "source_published_at": "2026-08-13T13:01:00+03:00",
  "source_modified_at": null,
  "fetched_at": "...",
  "observed_at": "...",

  "language": "ru",
  "category": "finance",
  "tags": [],

  "candidate_status": "INCLUDE",
  "selection_rule_id": "...",
  "selection_reason": "...",

  "access_status": "PUBLIC",
  "attachments": [],

  "content_hash": "..."
}
```

### `candidate_status`

```text
INCLUDE
EXCLUDE
AMBIGUOUS
```

Не удалять `EXCLUDE`/`AMBIGUOUS` без следа: decision должен быть аудируемым. Dataset/API может по умолчанию отдавать только `INCLUDE`, но история отбора сохраняется.

### `access_status`

```text
PUBLIC
PAYWALLED
ACCESS_LIMITED
```

Для paywalled материала сохраняются только данные, которые сайт публично отдал без обхода ограничений: title/date/url/preview/metadata.

---

## 3. Natural key и дедупликация

Предпочтение:

```text
source_id + stable external_id
```

Fallback:

```text
source_id + canonical_url
```

`content_hash` используется для version/change detection, но не заменяет stable identity.

### Межисточниковый semantic duplicate

Одна и та же новость может появиться на НБРБ, PrimePress, MyFin и Telegram. Multiverse может сохранять все source observations. **Семантическое объединение разных источников — задача внешнего ИИ**, если только в Multiverse не появится отдельная универсальная entity-resolution capability.

Не выбрасывать официальный первоисточник из-за того, что раньше был найден media repost.

---

## 4. Общий news workflow

```text
Start(interval)
→ Acquire listing/API/feed
→ Traverse pagination + date boundary + list→detail
→ Extract full public article/metadata/files
→ Process normalize dates/URLs/text + deterministic selection rules
→ Assure interval/completeness/detail reconciliation
→ Output market-news
```

### Критически нужный generic traversal

```yaml
dateBoundary:
  field: source_published_at
  lowerBound: "{{run.from}}"
  upperBound: "{{run.to}}"
  order: DESC
  stopWhenOlder: true
```

Early stop разрешён только если preset доказал монотонный порядок списка. Иначе фильтровать без premature stop.

---

# 5. Source-by-source playbook

## NEWS-01 — БВФБ / пресс-релизы

- **URL:** https://www.bcse.by/press-center/releases
- **Темы из паспорта:**
  - депозитарные облигации;
  - биржевые облигации;
  - облигации;
  - изменения информации проспекта/условий выпуска.
- **Repo intelligence:** существующий Multiverse уже знает detail endpoint `/solo/calendar`; аудит проекта также выявлял публичный listing/calendar API family.
- **Recommended Acquire:** public JSON/API first **после live-проверки endpoint**; HTML/browser только fallback.
- **Known detail pattern from current repo seed:**

```text
https://www.bcse.by/solo/calendar
query: sType=6, sDay=<publication_time>, link=<record_id>
```

- **Traverse:** category/listing IDs → detail; date boundary.
- **Extract:** title/date/body/attachments/external id.
- **Process:** deterministic topic inclusion по full detail, не только title.
- **Assure:** discovered IDs = fetched + explicit failed/skipped; required topic decision reason.
- **Legal note:** публичная доступность не равна свободной лицензии на перераспространение. Deployment owner должен отдельно проверить условия использования/перепечатки сайта; Multiverse не должен обходить ограничения.

## NEWS-02 — БВФБ / новости

- **Base:** https://www.bcse.by/press-center/releases и соответствующая категория news в публичной calendar family.
- **Правило паспорта:** брать все экономические/рыночные новости, исключать явно неэкономические (праздничные поздравления, шахматный турнир и подобное).
- **Acquire/Traverse:** тот же API-first pattern, отдельный `source_section/category` preset.
- **Selection:** deterministic deny rules для очевидного non-economic + `AMBIGUOUS` для спорного; не строить opaque LLM-only filtering.
- **Output:** `candidate_status` + `selection_reason`.

## NEWS-03 — БВФБ / валютные и REPO показатели

- **Homepage:** https://www.bcse.by
- **Из паспорта:** курсы валют + REPO rates.
- **Архитектура:** **не писать эти значения в `market-news` как статьи**. Создать/использовать отдельный dataset `market-indicators`.
- **Repo audit intelligence:** ранее выявлялся public route вида `/Home/Repo?currency=BYN`; live-профилировать перед preset verification.
- **Acquire:** API/JSON first if public; HTML market block second.
- **Natural key:** indicator code + effective timestamp/session.

## NEWS-04 — НБРБ / пресс-релизы

- **URL:** https://www.nbrb.by/news/press
- **Rule:** все пресс-релизы.
- **Current verification:** публичная страница индексируется с датированным списком релизов.
- **Acquire:** HTTP/RSS/feed if profiler discovers an official feed; no browser by default.
- **Traverse:** listing → detail + date boundary.
- **Extract:** scoped article body, publication date, attachments.
- **Assure:** all list items in interval have detail result or reason.

## NEWS-05 — НБРБ / статистика

- **URL:** https://www.nbrb.by/news/statistics
- **Включать только точные тематические серии из паспорта:**
  1. `Сведения о средних процентных ставках кредитно-депозитного рынка`
  2. `Показатели рынка корпоративных ценных бумаг`
- **Acquire:** HTTP/official structured source.
- **Selection:** normalized exact-title/series-id allowlist, не fuzzy LLM.
- **Traverse:** matching entry → detail/file/table.
- **Extract:** publication metadata + public detail/file; если данные сами представляют time series, при необходимости дополнительно публиковать структурированные значения в `market-indicators`, оставляя announcement в `market-news`.

## NEWS-06 — Министерство экономики

- **URL:** https://economy.gov.by/ru/aktualnaya-informatsiya-ru/
- **Rule:** все новые публикации/файлы из указанного раздела.
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** listing → article **или** direct file.
- **Extract:** MIME branching: HTML/PDF/XLSX/DOCX по фактическому content type.
- **Provenance:** связь `listing publication → attachment` обязательна.
- **Assure:** file parse failure не должен молча превращаться в пустой body.

## NEWS-07 — Министерство финансов

- **URL:** https://www.minfin.gov.by/public/ru/news/
- **Rule:** все новые статьи.
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** dated listing → article/file; date boundary.
- **Extract:** title/date/full public body; typed files, если публикация ведёт непосредственно к данным/документу.

## NEWS-08 — Центральный депозитарий

- **URL:** https://www.centraldepo.by/news/
- **Rule:** все новые публикации, брать **не только заголовок, а полный доступный detail**, который внешний ИИ потом сократит.
- **Status:** CONFIRMED_HTTP
- **Current observation:** listing имеет фильтры `за 24ч / 3 дня / 7 дней`, date range и нумерованную пагинацию; публичный URL pagination подтверждается `?PAGEN_1=N`.
- **Acquire:** HTTP.
- **Traverse:** list→detail + `PAGEN_1` pagination + date boundary.
- **Optimization:** не использовать сайт-фильтр как единственный источник истины; parser interval должен быть собственным воспроизводимым параметром.
- **Assure:** page order/date monotonicity fixture; detail reconciliation.

## NEWS-09 — PrimePress / аналитика

- **URL:** https://primepress.by/analitika/
- **Rule паспорта:** все статьи/обзоры раздела.
- **Status:** CONFIRMED_HTTP
- **Current observation:** страница явно различает открытые и коммерческие обзоры; встречается цена `240 BYN`; есть нумерованная пагинация (`1…5`, hundreds of materials).
- **Previously observed URL pagination:** Bitrix pattern `?PAGEN_1=N` — live-проверить и pin в preset.
- **Acquire:** HTTP.
- **Traverse:** URL pagination/date boundary → detail.
- **Paywall:** если материал платный, `access_status=PAYWALLED`; сохранить только публичный title/date/preview/url. **Никакого обхода.**
- **Assure:** public vs paid classification.

## NEWS-10 — PrimePress / финансы

- **URL:** https://primepress.by/news/finansi/
- **Rule паспорта:** все **кроме банковской тематики**.
- **Status:** CONFIRMED_HTTP
- **Current observation 13.08.2026:** карточки содержат title, summary, `Платный контент: Y/N`, точный datetime и detail link; список смешивает валютный рынок, гособлигации и банковские новости.
- **Acquire:** HTTP.
- **Traverse:** `?PAGEN_1=N` (после live confirmation) + date boundary → detail.
- **Selection order:**
  1. получить full public detail/preview;
  2. deterministic bank-topic classifier;
  3. `EXCLUDE` очевидные bank-only materials;
  4. `AMBIGUOUS` спорные случаи.
- **Не фильтровать только по слову `банк`:** статья о НБРБ/рынке/БВФБ может быть релевантной, хотя содержит банковскую лексику. Правило должно иметь configurable allow/deny/context patterns.
- **Evidence:** `selection_rule_id`, matched terms/category and reason.

## NEWS-11 — MyFin / ценные бумаги

- **URL:** https://myfin.by/article/rynki/cennye-bumagi
- **Rule:** все новости конкретного раздела.
- **Acquire:** HTTP.
- **Critical scope:** MyFin page содержит глобальные блоки `Популярное`/recommendations и множество unrelated navigation links. Listing selector должен быть scoped именно на category feed.
- **Traverse:** category cards → detail + URL pagination/date boundary.
- **Extract detail:** JSON-LD metadata preferred for title/date/canonical, then scoped DOM article body.
- **Assure:** category-source evidence; no global recommendation leakage.

## NEWS-12 — MyFin / драгметаллы

- **URL:** https://myfin.by/article/rynki/dragmetally
- **Rule:** все новости раздела.
- **Status:** CONFIRMED_HTTP
- **Current observation:** большой HTML, cookie/UI/global navigation; category feed must be isolated. Пагинация присутствует.
- **Pattern:** scoped category list → detail → JSON-LD + article DOM.
- **Assure:** no duplicate global cards; interval coverage.

## NEWS-13 — MyFin / аналитика

- **URL:** https://myfin.by/article/rynki/analitika
- **Rule:** все новости раздела.
- **Pattern:** аналогично MyFin securities/metals, но отдельный SourcePreset/source_section.
- **Reason for separate presets:** независимая coverage, category-specific filters, возможность отключения/изменения одной категории без изменения остальных.

## NEWS-14 — Phoenix Refining

- **URL:** https://www.phoenixrefining.com/blog
- **Rule:** только материалы про **gold, silver, platinum**.
- **Status:** CONFIRMED_HTTP
- **Repo/audit observation:** pagination ранее подтверждалась через `?page=N`; live-проверить.
- **Acquire:** HTTP.
- **Traverse:** `?page=N` → detail + date boundary.
- **Selection:** category/tags first, затем deterministic full-text topic rule; другие металлы/общие новости → EXCLUDE/AMBIGUOUS.
- **Language:** обычно English; сохранять original text, translation делает внешний ИИ при необходимости.

## NEWS-15 — The Business Times / precious metals

- **URL:** https://www.businesstimes.com.sg/keywords/precious-metals?ref=article-bottom-keyword
- **Rule исходного паспорта:** брать материалы по тегу/теме **gold**.
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP keyword listing.
- **Traverse:** list→article detail where anonymously public.
- **Selection:** несмотря на keyword `precious-metals`, дополнительно фильтровать именно gold relevance, если бизнес-правило остаётся таким.
- **Paywall:** metadata/preview only + `PAYWALLED`; никакого обхода подписки.
- **Extract:** JSON-LD preferred for metadata, public DOM for body.

## NEWS-16 — TexMetals

- **URL:** https://texmetals.com/news?page=1
- **Rule:** все новости.
- **Status:** REPROFILE
- **Audit observation:** paginator ведёт на `?page=N`; в некоторых fetch representation была JS-dependent.
- **Acquire:** HTTP direct pages first; если cards не присутствуют — anonymous browser render.
- **Traverse:** `?page=N`, page hash/repeated-page detection, date boundary.
- **Assure:** page 2 must differ from page 1; repeated content stops traversal and reports reason.

---

# 6. Telegram sources из паспорта

Telegram **не должен быть primary P0**, если официальный сайт уже содержит ту же публикацию. Использовать как secondary/reconciliation/test source.

Запрещён `web.telegram.org` с login/session.

Допустим публичный анонимный mirror:

```text
https://t.me/s/<channel>
```

если он реально отдаёт сообщения без авторизации.

## NEWS-TG-01 — usefulfigures

- **Исходный URL:** https://web.telegram.org/k/#@usefulfigures
- **Статус:** **DEFERRED/BLOCKED AS PROVIDED** — требует Telegram Web/session.
- **Action:** найти официальный публичный `https://t.me/s/<channel>` только если он существует и доступен анонимно. Иначе не парсить.

## NEWS-TG-02 — БВФБ

- **Исходный:** https://t.me/jsc_bcse
- **Рекомендуемый public mirror:** https://t.me/s/jsc_bcse
- **Current verification:** public mirror доступен анонимно.
- **Role:** SECONDARY/TEST; official BCSE site/API primary.

## NEWS-TG-03 — НБРБ

- **Исходный:** https://t.me/pressnbrb
- **Рекомендуемый:** https://t.me/s/pressnbrb
- **Current verification:** public mirror доступен.
- **Role:** SECONDARY/TEST; official NBRB site primary.

## NEWS-TG-04 — Минфин

- **URL:** https://t.me/s/minfinrb
- **Role:** SECONDARY/TEST.
- **Acquire:** public mirror HTML only; no Telegram login/session.

## NEWS-TG-05 — Минэкономики

- **URL:** https://t.me/s/econ_gov_by
- **Role:** SECONDARY/TEST.

### Telegram natural key

Prefer stable post ID from canonical message URL. Store channel + message ID + public URL. Не считать Telegram copy самостоятельной заменой official article.

---

# 7. Selection rules: deterministic first

Selection rules должны быть versioned data, а не site-specific Python.

Пример:

```yaml
rules:
  - id: primepress-finance-bank-exclusion-v1
    source: primepress-finance
    action: EXCLUDE
    when:
      topic: BANK_ONLY
  - id: phoenix-metals-v1
    action: INCLUDE
    when:
      anyTopic: [GOLD, SILVER, PLATINUM]
```

Возможный universal second stage — classifier с прозрачным output:

```json
{
  "label": "AMBIGUOUS",
  "confidence": 0.61,
  "reason": "mentions bank as counterparty but main event is bond placement"
}
```

LLM/classifier никогда не должен скрывать исходный article/evidence или быть единственной причиной считать run complete.

---

# 8. Paid/licensed/restricted content

Multiverse работает только с тем, что публично доставлено обычному анонимному посетителю.

Запрещено:

- обход paywall;
- login/account/session automation;
- CAPTCHA bypass;
- private APIs/internal IP;
- подмена User-Agent/сессии для обхода технических ограничений;
- раскрытие большего текста, чем источник публично отдал.

Отдельно: некоторые сайты имеют условия перепечатки/распространения. Это не техническая задача parser engine. Dataset обязан сохранять `source_url`/authority/provenance; владелец deployment отдельно валидирует права дальнейшего использования контента.

---

# 9. Quality gates новостей

Для news zero records в интервале может быть `PASS`, но только если доказано, что source был проверен.

Пример assessment:

```yaml
expectedScope:
  allowEmpty: true
requiredFieldCoverage:
  source_id: 1.0
  canonical_url: 1.0
  title: 1.0
  source_published_at: 0.98
  access_status: 1.0
detailSuccessRatio:
  min: 0.95
dateWindow:
  prohibitFuture: true
  prohibitOlderThanFromAfterFilter: true
```

Причины:

```text
EMPTY_VALID_WINDOW
SOURCE_ACCESS_LIMITED
INCOMPLETE_TRAVERSAL
DETAIL_FAILURE
DATE_PARSE_FAILURE
PAYWALL_METADATA_ONLY
```

### Coverage обязательно

Следующие состояния различаются:

```text
0 records, source checked successfully → EMPTY_VALID_WINDOW
0 records, source not reached         → NOT_CHECKED / FAIL
3 records, 7 of 20 details failed     → PARTIAL
```

---

# 10. API для внешнего ИИ

Перед получением новостей:

```http
GET /api/v1/datasets/market-news/coverage?from=<ISO>&to=<ISO>
```

Затем:

```http
GET /api/v1/datasets/market-news/records
  ?view=current
  &time_basis=source_published_at
  &from=<ISO>
  &to=<ISO>
```

External AI pipeline:

```text
coverage check
→ fetch INCLUDE + optionally AMBIGUOUS records
→ cross-source semantic dedupe
→ factual summarization
→ personalization
→ human review if pilot/ambiguous
→ Telegram delivery
```

Не использовать `latest_run` как доказательство coverage всего dataset: источники запускаются независимыми workflows.

---

# 11. Definition of Done news preset

`VERIFIED` только если:

- canonical source/section подтверждены;
- exact listing scope изолирован;
- pagination/date order доказаны fixture;
- date-boundary не пропускает записи;
- list→detail reconciliation сходится;
- public/paywalled state определяется без bypass;
- required fields/evidence сохраняются;
- selection rules versioned и explainable;
- `EMPTY_VALID_WINDOW` проходит только при доказанном source check;
- repeated-page protection есть;
- fixture + live smoke проходят;
- no site-specific engine code.

---

# 12. Репозиторий Multiverse и technical baseline

- https://github.com/Vadimohka/Multiverse
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/contracts.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/strategies.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/nodes.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/data.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/presets.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/seed_templates.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/docs/audit/UNIVERSAL_SCRAPER_BLUEPRINT_2026-08-12.md

На 13.08.2026 Contract v2 и seven-phase facade уже существуют. Основные gaps для этого паспорта — date-bounded traversal, aggregate coverage, universal collection/revision processing и массовые source presets. См. `EPIC_MULTIVERSE_BELARUS_MARKET_DATA.md`.
