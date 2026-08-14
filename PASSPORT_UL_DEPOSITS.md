# Паспорт данных: депозиты юридических лиц (ЮЛ)

**Проект:** Multiverse — Belarus Market Data Pack
**Dataset:** `deposit-offers-legal`
**Назначение:** стабильная выдача внешнему ИИ-агенту актуальных публичных условий размещения денежных средств юридических лиц/ИП в банках Республики Беларусь.
**Версия паспорта:** 1.0
**Исследование источников:** 13.08.2026
**Исходный бизнес-паспорт:** `Паспорт ИИ агента_ Парсинг данных о ставках депозитов для ЮЛ и ФЛ(1).xlsx`

> Этот документ заменяет необходимость передавать coding-агенту исходный Excel. Он содержит бизнес-требования, целевую schema, список источников, уточнённые URL и технические подсказки по каждому источнику.

---

## 1. Роль Multiverse

Multiverse **не** должен быть набором банковских парсеров. Для каждого банка создаётся декларативный `SourcePresetRevision` и отдельный workflow на общем каркасе:

```text
Start → Acquire → Traverse → Extract → Process → Assure → Output
```

Запрещено создавать `ParseBelarusbankNode`, `TCBankParser`, `if hostname == ...` в generic engine и другие site-specific ноды.

Специфика банка хранится только в preset/workflow configuration:

- URL и allowed domains;
- HTTP/API/browser strategy order;
- selectors/JSONPath/XPath;
- tabs/filter states;
- pagination/detail fan-out;
- document links и правила выбора действующей редакции;
- mapping/normalization;
- schema, natural key, completeness assertions;
- fallback и budgets.

Browser допустим только для публично доступного анонимному посетителю содержимого и только после неуспеха более простого публичного способа.

---

## 2. Что должен получать внешний ИИ-агент

Бизнес-паспорт требует для ЮЛ:

1. валюта: `BYN`, `USD`, `EUR`, `RUB` (и иные валюты, если банк публично предлагает их — не отбрасывать);
2. наименование банка;
3. срок в днях/месяцах;
4. годовая процентная ставка с точностью до двух знаков для consumer-представления;
5. минимальная сумма;
6. фиксированная/индивидуальная ставка;
7. ссылка на первоисточник.

Для корректного API и истории этого недостаточно. Dataset должен хранить более богатый record.

### 2.1. Целевая business schema `BankDepositOffer`

```json
{
  "segment": "LEGAL_ENTITY",
  "institution_id": "tcbank",
  "institution_name": "ТК Банк",
  "product_id": "stable-source-id-or-derived",
  "product_name": "...",
  "product_variant": null,

  "currency": "BYN",

  "term_raw": "32–61 день",
  "term_min_days": 32,
  "term_max_days": 61,
  "term_min_months": null,
  "term_max_months": null,

  "rate_type": "FIXED",
  "rate_raw": "6,0%",
  "rate_pct": 6.00,
  "rate_min_pct": null,
  "rate_max_pct": null,
  "rate_formula_raw": null,
  "benchmark_code": null,
  "spread_pp": null,
  "rate_tier": null,

  "min_amount": 50000.00,
  "max_amount": null,
  "amount_currency": "BYN",

  "replenishment_allowed": null,
  "partial_withdrawal_allowed": null,
  "early_termination_terms_raw": null,

  "effective_from": null,
  "effective_to": null,
  "offer_status": "ACTIVE",

  "source_role": "LEGAL_ENTITY",
  "source_authority": "PRIMARY",
  "source_url": "https://...",
  "canonical_url": "https://...",
  "source_published_at": null,
  "source_modified_at": null
}
```

### 2.2. `rate_type`

Поддерживать как минимум:

```text
FIXED
VARIABLE
BENCHMARK_SPREAD
TERM_TIERED
AMOUNT_TIERED
FORMULA
INDIVIDUAL
NOT_PUBLISHED
```

Правила:

- `ставка не опубликована` ≠ `0`;
- `по договорённости`, `индивидуально` → `INDIVIDUAL`;
- формула типа `ставка НБРБ + X п.п.` хранится как формула, benchmark и spread, а не только как вычисленное число;
- tiered table раскрывается в отдельные business records либо в явно версионированную tier structure;
- `rate_pct` не включать в natural key.

---

## 3. Нормализация

### 3.1. Срок

Сохранять и raw, и normalized.

Примеры:

```text
"до 30 дней"        → max_days=30
"32–61 день"        → min_days=32, max_days=61
"3 месяца"          → normalized days только по явно принятой общей политике;
                       raw обязательно сохраняется
"от 6 до 12 месяцев"→ min_months=6, max_months=12
```

Исторический Excel содержит buckets `до 30`, `30–60`, `60–90`, `90–180`, `180–365`, `свыше 365`; использовать их только как regression/QA reference, **не** как источник текущих ставок.

### 3.2. Сумма

- decimal, без пробелов-разделителей;
- `min_amount` + `amount_currency`;
- amount tiers раскрывать отдельно;
- текстовые ограничения сохранять в raw/evidence.

### 3.3. Ставка

- запятая/точка → decimal;
- символ `%` удалить только при normalization;
- API consumer display может округлять до 2 знаков, raw не терять;
- отрицательные/аномальные значения должны падать на Assure range check.

### 3.4. Валюта

Нормализовать ISO-коды. Не выбрасывать `CNY` и другие валюты, если официальный источник их реально предлагает: исходный паспорт перечисляет четыре основные валюты, но parser не должен терять легитимные публичные данные.

---

## 4. Natural key, версии и observations

Рекомендуемый natural key:

```text
institution_id
+ segment
+ stable product identity
+ product_variant/channel
+ currency
+ term tier
+ amount tier
```

**Не включать ставку.** Тогда изменение `9.5% → 10.0%` создаёт новую `RecordVersion`, а не новый продукт.

Повторный неизменившийся запуск создаёт observation/seen-at, но не duplicate business version.

Для каждого поля сохранять provenance/evidence:

```text
normalized value
← raw value
← selector/JSONPath/table cell/document location
← raw artifact
← final URL
← run_id
← SourcePresetRevision
```

---

## 5. Общий workflow банков

Рекомендуемая стратегия discovery:

```text
HTTP/API
  ↓ postconditions failed
public XHR capture
  ↓ failed/incomplete
browser render/actions
  ↓
documents/files
```

Это не означает одинаковый fallback для всех банков. Если server HTML полный — browser запрещено использовать без причины.

### Универсальные capabilities, которые особенно нужны ЮЛ

- scoped repeating containers;
- list → detail fan-out;
- HTML table extraction;
- `matrix_to_records` / `unpivot`;
- `explode` / `expand_tiers`;
- tabs/filter state matrix;
- MIME branching для PDF/DOCX/XLSX;
- `select_effective_revision` для current vs archive documents;
- source-role assertion;
- detail/document reconciliation;
- unexpected-empty protection.

---

## 6. Статусы исследований

- **CONFIRMED_HTTP** — на 13.08.2026 подтверждено, что публичная страница/деталь отдаёт полезное содержимое через обычный HTTP или поисковый индекс явно видит структурированные условия.
- **CONFIRMED_ROUTE** — актуальный корпоративный маршрут найден/уточнён; перед `VERIFIED` всё равно нужен live fixture.
- **REPROFILE** — публичный URL известен, но representation нестабилен/JS-heavy/таймаутился; workflow должен live-профилировать HTTP→XHR→browser.
- **PRIMARY** — официальный источник банка.

Ни один статус этого документа не заменяет fixture + live smoke перед `SourcePreset=VERIFIED`.

---

# 7. Источники ЮЛ: source-by-source playbook

## UL-01 — Банк Дабрабыт

- **Официальный URL:** https://bankdabrabyt.by/money/depozity_biznes/
- **Authority:** PRIMARY
- **Research status:** REPROFILE
- **Acquire:** HTTP first; если HTML не проходит postconditions — public XHR, затем anonymous browser render.
- **Traverse:** product cards/sections → detail/terms/documents; не обходить весь сайт.
- **Extract:** product name, currency, term, min amount, rate/individual terms, linked current documents.
- **Process:** rate type, term normalization, amount, document effective dates.
- **Assure:** `sourceRole=LEGAL_ENTITY`, `minRecords>0` для доступной продуктовой линейки, detail/doc reconciliation.
- **Особая подсказка:** direct fetch во время исследования был нестабилен/таймаутился; это не повод делать browser-only preset. Сначала доказывать неполноту HTTP representation.

## UL-02 — Белагропромбанк

- **URL:** https://www.belapb.by/krupnomu-biznesu/razmeshchenie-sredstv/depozity/
- **Status:** CONFIRMED_HTTP
- **Observed:** публичный листинг содержит категории/фильтры по типу вкладчика, типу депозита и валютам, включая `BYN/RUB/CNY/USD/EUR`, а также ссылки на конкретные продукты.
- **Acquire:** HTTP first; при discovery проверить backing API/XHR, но не переходить в browser без необходимости.
- **Traverse:** scoped cards → product detail; declarative state coverage, если фильтры действительно меняют набор данных.
- **Extract:** DOM fields/details + documents, если условия вынесены в файлы.
- **Assure:** expected currencies/states; не считать скрытый filter state покрытым только потому, что кнопка найдена.

## UL-03 — Беларусбанк

- **URL:** https://belarusbank.by/business/deposits/
- **Status:** CONFIRMED_HTTP
- **Observed:** server HTML богатый; есть корпоративные продукты, включая отдельные product links, но рядом много глобального/retail-контента.
- **Acquire:** HTTP.
- **Traverse:** только business-deposits container → детали → scoped documents.
- **Extract:** таблицы и detail blocks; документы использовать как отдельный candidate source.
- **Process:** `matrix_to_records`/tier expansion, если ставки представлены матрицами; effective revision selection.
- **Assure:** `sourceRole=LEGAL_ENTITY`; selectors должны быть scoped, чтобы не захватить retail/global nav.
- **Риск:** архивные/новые редакции условий. Не выбирать PDF по позиции ссылки.

## UL-04 — Белгазпромбанк

- **Seed из паспорта:** https://belgazprombank.by/korporativnim_klientam/razmeschenie_svobodnih_denezhnih_sredst/
- **Более точный deposits route:** https://belgazprombank.by/korporativnim_klientam/razmeschenie_svobodnih_denezhnih_sredst/bankovskie_depoziti/
- **Пример официального документа:** https://belgazprombank.by/upload/userfiles/files/bgpb_vkladi_usloviya_ul-ip.pdf
- **Status:** CONFIRMED_ROUTE
- **Acquire:** specific deposits route HTTP first.
- **Traverse:** tabs/product sections/details → scoped document links.
- **Extract:** HTML terms плюс PDF conditions, если он является действующим authority source.
- **Assure:** expected tabs/states; current-document decision evidence.

## UL-05 — Белинвестбанк

- **URL:** https://www.belinvestbank.by/business/deposits
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** cards → details → product documents.
- **Extract:** DOM + typed documents.
- **Ключевой риск:** current vs historical terms.
- **Assure:** document scope только в product/detail context; archive links не должны случайно стать current.

## UL-06 — Банк ВТБ (Беларусь)

- **Seed паспорта (малый бизнес):** https://www.vtb.by/malomu-biznesu/depozity
- **Корпоративный раздел:** https://www.vtb.by/korporativnym-klientam/privlechenie-svobodnyh-denezhnyh-sredstv
- **Пример detail:** https://www.vtb.by/malomu-biznesu/depozity/depozit-bezotzyvnyy
- **Status:** CONFIRMED_ROUTE
- **Acquire:** HTML/detail first; profile public XHR/API backing calculator/filter before browser actions.
- **Traverse:** exact target segment → product details; filter/calculator states only when they expose otherwise unavailable business combinations.
- **Extract:** terms, amount, currencies, rate, revocability/type.
- **Assure:** explicitly record whether preset targets `SMALL_BUSINESS` as accepted subtype of `LEGAL_ENTITY` or full corporate; не смешивать разные линейки без product/source-role metadata.

## UL-07 — БНБ-Банк

- **URL:** https://bnb.by/k-delu/razmeshchenie/depozitnye-operatsii/
- **Status:** CONFIRMED_HTTP
- **Observed:** большой server-rendered HTML.
- **Acquire:** HTTP first.
- **Traverse:** scoped product sections/details.
- **Hint:** expandables могут уже присутствовать в исходном DOM — не имитировать click, если данные доступны без него.
- **Assure:** no global footer/recommendation leakage; product count drift.

## UL-08 — Банк БелВЭБ

- **Неверный URL из исходного паспорта:** `https://www.belveb.by/deposits/` — retail.
- **Исправленный corporate/small-business URL:** https://www.belveb.by/small-business/deposits/deposits-small-business/
- **Пример detail:** https://www.belveb.by/small-business/deposits/deposits-small-business/bezotzyvny-s-popolneniem/
- **Status:** CONFIRMED_ROUTE
- **Observed:** filters/sections `Безотзывные`, `Отзывные`, валюты и product detail с блоками `Описание / Условия, ставки, лимиты / Документы`.
- **Acquire:** HTTP list→detail.
- **Traverse:** product cards and currency/type filters only if needed for completeness.
- **Assure:** **обязательный `sourceRole=LEGAL_ENTITY`**; regression test должен ловить случайную подстановку retail URL.

## UL-09 — Р-Банк

- **URL:** https://rbank.by/business/deposit/
- **Примеры details:** `https://rbank.by/business/deposit/pravilnoe-reshenie/` и другие product routes.
- **Status:** CONFIRMED_ROUTE
- **Acquire:** HTTP first.
- **Traverse:** list→details; attachments scoped to detail.
- **Assure:** все обнаруженные product URLs либо успешно обработаны, либо имеют явный failure/skip reason.

## UL-10 — МТБанк

- **URL:** https://www.mtbank.by/business/deposits/
- **Status:** CONFIRMED_HTTP
- **Observed:** текущая corporate page отдаёт полезный HTML: виды размещения, минимальные суммы, валюты, сроки и документы.
- **Acquire:** HTTP first; прежние оценки как «empty shell» не считать актуальной истиной.
- **Extract:** page fields/details/document.
- **Semantics:** если numeric rate публично не указана — `INDIVIDUAL`/`NOT_PUBLISHED`, а не ошибка и не `0`.
- **Assure:** empty shell postcondition всё равно оставить как regression protection.

## UL-11 — Neo Bank

- **Неверный URL из паспорта для ЮЛ:** `https://neobank.by/deposits/` — retail.
- **Исправленный business URL:** https://neobank.by/business/razmeshchenie-sredstv/depozity/
- **Status:** CONFIRMED_ROUTE
- **Acquire:** HTTP first; при JS shell — public XHR → anonymous render.
- **Assure:** `sourceRole=LEGAL_ENTITY`; separate preset от retail.

## UL-12 — Паритетбанк

- **URL:** https://www.paritetbank.by/business/deposit/
- **Status:** CONFIRMED_HTTP
- **Observed:** product cards и документы; встречаются явно датированные редакции условий (`ДО ...`, `С ...`).
- **Acquire:** HTTP.
- **Traverse:** product details/docs.
- **Critical fixture:** хранить одновременно old/current document и проверять generic `select_effective_revision` относительно immutable run clock.
- **Assure:** ambiguity → review, не молчаливый выбор.

## UL-13 — Приорбанк

- **URL:** https://www.priorbank.by/business/services/investments/vklady-business
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** только business investments/deposits section → product details.
- **Risk:** очень большой общий HTML; обязательный scoped container, иначе легко собрать навигацию/похожие предложения.
- **Semantics:** индивидуальные условия → `INDIVIDUAL`, не synthetic fixed rate.

## UL-14 — Банк РРБ

- **Исходный паспорт:** только `https://www.rrb.by` с пометкой о проблеме входа.
- **Исправленный corporate route:** https://www.rrb.by/korporativnim-klientam/depoziti
- **Status:** CONFIRMED_ROUTE
- **Acquire:** direct corporate route HTTP first, не homepage crawling.
- **Traverse:** deposits → product details, если доступны.
- **Assure:** source role и segment; negotiated terms сохранять явно.

## UL-15 — Сбер Банк

- **URL:** https://www.sber-bank.by/vklady-biznes/depozity-i-investicii/dlya-yuridicheskih-lic
- **Status:** CONFIRMED_HTTP
- **Pattern:** document-heavy.
- **Acquire:** HTTP list/detail.
- **Traverse:** relevant product blocks → document inventory.
- **Extract:** HTML + PDF/DOCX/XLSX by MIME.
- **Process:** current-document resolver.
- **Assure:** document authority/effective date доказаны; не выбирать архивный файл по DOM order.

## UL-16 — СтатусБанк

- **URL:** https://stbank.by/business-customers/deposits/
- **Status:** CONFIRMED_ROUTE
- **Observed:** публичный business deposits section существует, но часть условий может сводиться к индивидуальному контакту/согласованию.
- **Acquire:** HTTP.
- **Extract:** всё публично опубликованное; если numeric rate нет — сохранить product + `INDIVIDUAL`/`NOT_PUBLISHED`.
- **Assure:** отсутствие ставки не должно превращать весь source run в FAIL, если сам продукт и факт индивидуальных условий доказаны.

## UL-17 — Технобанк

- **URL:** https://tb.by/business/investments/deposits/
- **Status:** REPROFILE
- **Observed:** корректный business route существует, но server representation при одном из fetch был крайне мал/JS-oriented.
- **Acquire:** HTTP postconditions → public XHR discovery → browser render/actions.
- **Traverse:** product/currency subpages/states.
- **Assure:** shell/placeholder ≠ success; browser fallback должен быть полностью отражён в attempts/provenance.

## UL-18 — Цептер Банк

- **URL:** https://zepterbank.by/business/deposit/
- **Status:** CONFIRMED_ROUTE
- **Observed:** существуют отдельные business deposit product pages; часть ставок может быть индивидуальной.
- **Acquire:** HTTP list→detail.
- **Traverse:** product routes + scoped docs.
- **Process:** individual/fixed semantics, effective revision where documents exist.

## UL-19 — Альфа-Банк

- **URL:** https://www.alfabank.by/business/deposits/
- **Status:** CONFIRMED_ROUTE
- **Примеры product routes:**
  - https://www.alfabank.by/business/deposits/solution/
  - https://www.alfabank.by/business/deposits/profit/
  - https://www.alfabank.by/business/deposits/srochniy/
  - https://www.alfabank.by/business/deposits/touring/
- **Acquire:** list/detail HTTP first.
- **Traverse:** scoped products only.
- **Semantics:** различать fixed public conditions и negotiated product.
- **Documents:** не собирать generic footer/policy files как evidence продукта.

## UL-20 — ТК Банк

- **URL:** https://www.tcbank.by/business/deposits/
- **Status:** CONFIRMED_HTTP
- **Особая ценность:** один из лучших regression fixtures для универсального HTML-table pipeline.
- **Observed pattern:** таблицы/матрицы с currency sections, видом депозита, минимальной суммой и колонками диапазонов сроков (`32–61`, `62–91`, `92–185`, `186–366` и т.п.) со ставками.
- **Acquire:** HTTP. Browser не нужен при прохождении postconditions.
- **Extract:** semantic HTML table.
- **Process:** generic `matrix_to_records`/`unpivot` → отдельный offer на каждый term/rate cell; amount/rate tier semantics.
- **Assure:** число созданных records должно согласовываться с непустыми rate cells матрицы; evidence хранит row+column header/cell.

## UL-21 — БСБ Банк

- **URL:** https://www.bsb.by/depozity-dlya-biznesa
- **Примеры details:**
  - https://bsb.by/srochnyj-bezotzyvnyj
  - https://bsb.by/srochnyj-otzyvnyj
- **Status:** CONFIRMED_ROUTE / REPROFILE REPRESENTATION
- **Observed:** публичные business deposit products и detail routes существуют; indexed content содержит сроки/ставки.
- **Acquire:** HTTP list→detail; если текущая representation неполна — public XHR → browser render.
- **Assure:** не помечать источник permanently browser-only; решение принимается по текущим postconditions/fixtures.

---

# 8. Отдельный контур НБРБ — НЕ смешивать с депозитами банков

Исходный Excel содержит также регуляторные показатели:

- https://www.nbrb.by/statistics/MonetaryPolicyInstruments/RefinancingRate
- https://www.nbrb.by/statistics/rates/ratesDaily
- https://www.nbrb.by/statistics/valuables/bankingots

Бизнесу нужны, в частности:

- ключевая/регуляторная ставка;
- ставка рефинансирования;
- РВСР/связанные индикаторы, если официально представлены;
- средние ставки новых депозитов;
- статистика по депозитам ФЛ/ЮЛ.

**Архитектурно:** хранить в отдельном dataset `market-indicators`/`regulatory-indicators`, а не добавлять искусственные «депозиты НБРБ» в `deposit-offers-legal`.

Предпочитать официальный structured API/table, если доступен, затем HTML/document extraction.

---

# 9. Токены из исходного паспорта — отдельный/deferred scope

В Excel перечислены:

- https://bynex.io/investment/ru/ico
- https://finstore.by/kupit-tokeny/
- https://whitebird.io/ico
- https://fainex.by/#buing

Поля: issuer, currency, term, rate, income payment frequency.

**Не смешивать с банковскими депозитами.** При реализации нужен отдельный dataset, например `token-offers`.

Исходный Excel упоминает личный кабинет как возможный путь, но философия Multiverse разрешает только публичные данные обычного анонимного посетителя. Поэтому:

- login/private API/session automation запрещены;
- если публичной страницы недостаточно — source остаётся `DRAFT/BLOCKED`;
- не обходить CAPTCHA/paywall/access restrictions.

---

# 10. Quality gates ЮЛ

Для каждого bank workflow минимум:

```yaml
sourceRole: LEGAL_ENTITY
expectedScope:
  allowEmpty: false
  minRecords: 1
requiredFieldCoverage:
  institution_id: 1.0
  product_name: 1.0
  currency: 1.0
  source_url: 1.0
  rate_type: 1.0
detailSuccessRatio:
  min: 0.95
documentParseRatio:
  min: 0.95
countDrift:
  warnBelowRatio: 0.70
```

Значения thresholds могут отличаться по verified preset; не применять слепо один порог ко всем.

Различать причины:

```text
EMPTY_UNEXPECTED
SOURCE_ROLE_MISMATCH
SOURCE_ACCESS_LIMITED
INCOMPLETE_TRAVERSAL
DOCUMENT_AMBIGUOUS
PARSE_SCHEMA_FAILURE
```

HTTP 200, пустой shell или страница «мы вам перезвоним» не являются автоматически полноценным SUCCESS.

---

# 11. Schedule и freshness

Исходный бизнес-процесс: еженедельный мониторинг по понедельникам либо on-demand.

Рекомендуемый production режим:

- scheduled weekly Monday;
- on-demand endpoint/run для проверки перед аналитикой;
- `observed_at` каждого запуска;
- source effective/published dates отдельно от fetch time;
- если source недоступен, последняя версия не удаляется, но coverage должен явно показать stale/failed source.

---

# 12. API для внешнего ИИ

Основной запрос:

```http
GET /api/v1/datasets/deposit-offers-legal/records?view=current
```

Перед использованием данных агент должен проверить coverage:

```http
GET /api/v1/datasets/deposit-offers-legal/coverage
```

Optional evidence:

```http
GET /api/v1/datasets/deposit-offers-legal/records?view=current&include=evidence
```

ИИ не должен знать, был ли источник получен через HTTP, table, PDF или browser.

---

# 13. Definition of Done одного банковского preset

Preset можно перевести в `VERIFIED` только если:

- canonical/final URL подтверждён;
- corporate/source role подтверждён;
- strategy/fallback наблюдаемы;
- listing scope не собирает retail/footer/global feed;
- pagination/tabs/filter states ограничены budgets и stop conditions;
- discovered detail links reconciled;
- документы scoped и current revision доказана;
- schema проходит;
- field evidence сохраняется;
- natural key стабилен;
- zero records не проходит как ложный success;
- fixture regression проходит;
- live smoke проходит;
- второй неизменившийся run не создаёт duplicate version;
- изменение ставки создаёт новую record version;
- generic engine не содержит hostname-specific кода.

---

# 14. Репозиторий Multiverse — опорные файлы

Перед доработками coding-агент обязан изучить актуальный `main`:

- https://github.com/Vadimohka/Multiverse
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/contracts.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/strategies.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/nodes.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/presets.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/data.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/docs/audit/UNIVERSAL_SCRAPER_BLUEPRINT_2026-08-12.md

На 13.08.2026 в `main` уже есть Contract v2, семь публичных фаз, adaptive strategy framework и typed envelopes; их не надо создавать заново. Полный план изменений вынесен в `EPIC_MULTIVERSE_BELARUS_MARKET_DATA.md`.
