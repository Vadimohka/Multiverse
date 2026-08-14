# Паспорт данных: депозиты физических лиц (ФЛ)

**Проект:** Multiverse — Belarus Market Data Pack
**Dataset:** `deposit-offers-retail`
**Назначение:** стабильная выдача внешнему ИИ-агенту актуальных публичных условий банковских вкладов/депозитов для физических лиц в Республике Беларусь.
**Версия паспорта:** 1.0
**Исследование источников:** 13.08.2026
**Исходный бизнес-паспорт:** `Паспорт ИИ агента_ Парсинг данных о ставках депозитов для ЮЛ и ФЛ(1).xlsx`

> Документ самодостаточен для coding-агента и заменяет необходимость передавать исходный Excel.

---

## 1. Архитектурная роль

Каждый банк — отдельный декларативный `SourcePresetRevision` + workflow на общем каркасе:

```text
Start → Acquire → Traverse → Extract → Process → Assure → Output
```

Нельзя создавать ноды под конкретный банк. URL, selectors, tabs, filters, documents, normalization и quality rules живут в preset.

Парсер работает только с публичными данными, доступными анонимному посетителю. Не обходить login, CAPTCHA, paywall, robots restrictions и приватные API/session boundaries.

---

## 2. Бизнес-поля из исходного паспорта

Для ФЛ обязательный минимум:

1. банк;
2. валюта (`BYN`, `USD`, `EUR`, `RUB`; другие реальные публичные валюты не терять);
3. срок;
4. годовая ставка, consumer precision до 2 знаков;
5. минимальная сумма;
6. отзывный/безотзывный;
7. возможность пополнения;
8. первоисточник.

Для корректной работы ИИ и сохранения семантики dataset расширяется дополнительными полями.

### 2.1. Целевая schema

```json
{
  "segment": "INDIVIDUAL",
  "institution_id": "rrb",
  "institution_name": "Банк РРБ",
  "product_id": "stable-id",
  "product_name": "...",
  "product_variant": null,

  "currency": "BYN",

  "term_raw": "13 месяцев",
  "term_min_days": null,
  "term_max_days": null,
  "term_min_months": 13,
  "term_max_months": 13,

  "rate_type": "FIXED",
  "rate_raw": "...",
  "rate_pct": 10.50,
  "rate_min_pct": null,
  "rate_max_pct": null,
  "rate_formula_raw": null,
  "benchmark_code": null,
  "spread_pp": null,

  "min_amount": 100.00,
  "amount_currency": "BYN",

  "revocability": "IRREVOCABLE",
  "replenishment_allowed": true,
  "partial_withdrawal_allowed": false,
  "capitalization": "MONTHLY",
  "interest_payment_frequency": "MONTHLY",
  "early_termination_terms_raw": "...",
  "auto_prolongation": null,
  "online_opening": null,

  "effective_from": null,
  "effective_to": null,
  "offer_status": "ACTIVE",

  "source_role": "INDIVIDUAL",
  "source_authority": "PRIMARY",
  "source_url": "https://...",
  "canonical_url": "https://..."
}
```

### 2.2. Enum semantics

`revocability`:

```text
REVOCABLE
IRREVOCABLE
MIXED
NOT_PUBLISHED
```

`rate_type`:

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

Не додумывать missing fields. Если банк не публикует возможность пополнения/частичного снятия — `null`, а не `false`.

---

## 3. Нормализация и versioning

### Срок

Сохранять raw + normalized days/months. Не терять единицу измерения источника.

### Ставка

- raw value сохраняется;
- decimal normalization отдельно;
- `до X%` не превращать безусловно в fixed `X` — использовать min/max/type;
- индивидуальная ставка — отдельный тип;
- ставка не входит в natural key.

### Natural key

```text
institution_id
+ segment
+ stable product identity
+ product_variant/channel
+ currency
+ term tier
+ amount tier
```

При изменении ставки создаётся новая версия той же business entity.

### Evidence

Каждый важный field должен быть доказуем:

```text
business field → raw value → DOM/table/JSON/document pointer → artifact → URL → run → preset revision
```

---

## 4. Общие parsing patterns для ФЛ

Большинство retail-источников укладываются в комбинации:

```text
HTTP cards → details
HTTP cards → details → documents
filters/tabs → bounded states
calculator/filter → public backing API/XHR
JS shell → anonymous browser fallback
```

Browser не использовать, если исходный HTML уже содержит скрытые/раскрываемые данные.

Особенно важны universal capabilities:

- scoped cards;
- list→detail;
- field candidates;
- tabs/state matrix;
- HTML table/matrix extraction;
- document MIME routing;
- current revision resolver;
- source-role assertion;
- required-field coverage;
- count drift;
- unexpected-empty protection.

---

# 5. Источники ФЛ: source-by-source playbook

## FL-01 — Беларусбанк

- **URL:** https://belarusbank.by/fizicheskim_licam/vklady
- **Authority:** PRIMARY
- **Status:** CONFIRMED_HTTP, dynamic filters need profiling
- **Observed:** server HTML содержит разделы/ссылки продуктов, но интерфейс фильтра может показывать служебное `Найдено 0` независимо от наличия product links.
- **Acquire:** HTTP first.
- **Traverse:** вклад section → product detail; filters only if server/list representation не покрывает все продукты.
- **Important:** не считать одну надпись `Найдено 0` доказательством пустого результата. Assure должен смотреть на scoped product records/postconditions.
- **Extract:** terms/rates/min amount/revocability/replenishment/details/documents.

## FL-02 — Белагропромбанк

- **URL:** https://www.belapb.by/chastnomu-klientu/sberezheniya/vklady-i-scheta/
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** scoped product cards → detail.
- **Extract:** rich product parameters; documents if referenced.
- **Hint:** profile filters, но browser actions не нужны, если cards/details уже server-rendered.

## FL-03 — Белгазпромбанк

- **URL:** https://belgazprombank.by/personal_banking/vkladi_depoziti/depoziti/
- **Status:** REPROFILE
- **Acquire:** HTTP first; при неполной representation — public XHR → render.
- **Traverse:** product list/details, tabs/documents.
- **Assure:** `sourceRole=INDIVIDUAL`; empty/shell response не считать valid dataset.

## FL-04 — Белинвестбанк

- **URL:** https://www.belinvestbank.by/individual/deposits
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** cards→details→documents.
- **Risk:** действующие и архивные редакции условий; generic current revision resolver.
- **Assure:** coverage по продуктам и документам.

## FL-05 — Банк Дабрабыт

- **URL:** https://bankdabrabyt.by/personal/deposite/
- **Status:** REPROFILE
- **Acquire:** HTTP postconditions → XHR → anonymous browser.
- **Traverse:** retail product cards/details/files.
- **Assure:** non-empty fixture обязателен перед `VERIFIED`.

## FL-06 — МТБанк

- **URL:** https://www.mtbank.by/deposits/
- **Status:** CONFIRMED_HTTP
- **Observed:** богатый server-rendered retail HTML.
- **Acquire:** HTTP.
- **Traverse:** product cards → details/documents.
- **Extract:** all core fields + replenishment/withdrawal/payment semantics where published.

## FL-07 — БНБ-Банк

- **URL:** https://bnb.by/o-lichnom/sberezhenie/
- **Status:** CONFIRMED_HTTP
- **Observed:** большой server HTML.
- **Acquire:** HTTP.
- **Hint:** accordion content может уже присутствовать в DOM; не использовать clicks, если данные доступны.
- **Traverse:** savings/deposits scoped section → detail.

## FL-08 — Приорбанк

- **URL:** https://www.priorbank.by/offers/savings/deposits
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** savings/deposits cards → details.
- **Risk:** большая страница с другими предложениями; strict scoped container.
- **Assure:** expected product list/count drift.

## FL-09 — Паритетбанк

- **URL:** https://www.paritetbank.by/private/deposit/
- **Status:** CONFIRMED_HTTP
- **Acquire:** HTTP.
- **Traverse:** product cards/details/docs.
- **Process:** current revision selection, rate/term tier normalization.

## FL-10 — Банк БелВЭБ

- **URL:** https://www.belveb.by/deposits
- **Status:** CONFIRMED_HTTP
- **Важно:** это **правильный retail URL**, но его нельзя использовать для ЮЛ.
- **Acquire:** HTTP list→detail.
- **Traverse:** filters/type/currency only if needed.
- **Assure:** `sourceRole=INDIVIDUAL`; regression pair with corporate BelVEB preset, чтобы сегменты не перепутались.

## FL-11 — Банк ВТБ (Беларусь)

- **URL:** https://www.vtb.by/deposits
- **Пример detail:** https://www.vtb.by/chastnym-licam/vklady/vklad-moi-usloviya
- **Status:** CONFIRMED_ROUTE / REPROFILE listing representation
- **Observed:** официальные detail pages содержат structured conditions; listing иногда fetchится нестабильно.
- **Acquire:** HTTP list/detail; public XHR/API for calculator/filter if discovered; browser fallback only by postcondition.
- **Traverse:** all retail deposit details.
- **Extract:** rate, term, min amount, revocability, replenishment, withdrawal/payment conditions.

## FL-12 — Технобанк

- **URL:** https://tb.by/individuals/deposits/
- **Status:** REPROFILE / likely JS-heavy
- **Acquire:** HTTP → public XHR discovery → browser render.
- **Traverse:** product/currency states/details.
- **Assure:** shell ≠ success; attempts/fallback fully retained.

## FL-13 — СтатусБанк

- **URL:** https://stbank.by/private-client/deposits/
- **Status:** CONFIRMED_HTTP
- **Observed:** публичная страница содержит фильтры по сроку/валюте/отзывности/online и product links.
- **Пример detail:** https://stbank.by/private-client/deposits/irrevocable/status-nadezhnyy/
- **Acquire:** HTTP.
- **Traverse:** product links; state matrix только для доказуемо скрытых вариантов.
- **Особая ценность:** хороший fixture для `revocability`, filters и detail schema.

## FL-14 — Neo Bank

- **URL:** https://neobank.by/deposits/
- **Status:** REPROFILE / JS-light-shell observed
- **Важно:** этот URL retail; corporate использует другой route.
- **Acquire:** HTTP postconditions → public XHR → anonymous render.
- **Assure:** source role и non-empty product fixture.

## FL-15 — Банк РРБ

- **URL:** https://www.rrb.by/vkladi
- **Пример detail:** https://www.rrb.by/fizicheskim-licam/vklad/130
- **Status:** CONFIRMED_HTTP/ROUTE
- **Observed detail semantics:** ставка, срок, минимальная сумма, капитализация/выплата дохода, частичное снятие, пополнение, отзывность.
- **Acquire:** HTTP list→detail.
- **Особая ценность:** один из лучших fixtures для полной retail schema.
- **Assure:** detail completeness coverage для ключевых product fields.

## FL-16 — Альфа-Банк

- **URL:** https://www.alfabank.by/deposits/
- **Status:** REPROFILE
- **Acquire:** HTTP first; затем public XHR/render при неполноте.
- **Traverse:** retail deposit cards/details, scoped attachments.
- **Assure:** не использовать агрегаторы как primary, если official detail временно не извлекается.

## FL-17 — Цептер Банк

- **URL:** https://zepterbank.by/personal/deposits/
- **Status:** REPROFILE / official route
- **Acquire:** HTTP list/detail first; fallback по postconditions.
- **Traverse:** product pages/documents.
- **Process:** current revision and rate tier semantics.

## FL-18 — Р-Банк

- **URL:** https://rbank.by/life/deposits/
- **Status:** REPROFILE / official route
- **Acquire:** HTTP first.
- **Traverse:** list→details; load-more if truly required.
- **Assure:** discovered-vs-fetched reconciliation.

## FL-19 — БСБ Банк

- **URL:** https://www.bsb.by/depozit-v-bsb-banke
- **Status:** REPROFILE / official route
- **Acquire:** HTTP→public XHR→render depending current representation.
- **Traverse:** product/detail pages.
- **Assure:** do not declare VERIFIED from shell/landing-only response.

## FL-20 — MyFin (вторичный источник)

- **URL:** https://myfin.by/vklady
- **Authority:** **SECONDARY**
- **Status:** CONFIRMED_HTTP
- **Purpose:** reconciliation/coverage drift, поиск возможного расхождения или пропущенного официального продукта.
- **Запрещено:** перезаписывать official bank value данными MyFin.
- **Recommended output:** отдельный reconciliation observation/issue:

```json
{
  "institution_id": "...",
  "official_rate": 10.5,
  "secondary_rate": 11.0,
  "status": "MISMATCH_REVIEW",
  "secondary_url": "https://myfin.by/..."
}
```

---

# 6. Общие Quality Gates ФЛ

Пример:

```yaml
sourceRole: INDIVIDUAL
expectedScope:
  allowEmpty: false
  minRecords: 1
requiredFieldCoverage:
  institution_id: 1.0
  product_name: 1.0
  currency: 1.0
  rate_type: 1.0
  source_url: 1.0
  revocability: 0.90
  replenishment_allowed: 0.80
detailSuccessRatio:
  min: 0.95
countDrift:
  warnBelowRatio: 0.70
```

Порог покрытия конкретного optional field задаётся по verified source, а не глобально вслепую.

Причины incomplete/failure должны различаться:

```text
EMPTY_UNEXPECTED
SOURCE_ROLE_MISMATCH
SOURCE_ACCESS_LIMITED
INCOMPLETE_TRAVERSAL
DOCUMENT_AMBIGUOUS
PARSE_SCHEMA_FAILURE
```

---

# 7. Current vs archive

Для вкладов ФЛ часто меняются ставки/условия без изменения product URL.

Нужно:

1. сохранять immutable run clock;
2. находить `effective_from/effective_to` в page/table/document labels;
3. иметь generic ranking current documents;
4. сохранять всех кандидатов и decision evidence;
5. при неоднозначности → `REVIEW`, не молчаливый выбор;
6. сохранять историю всех business versions.

---

# 8. Schedule и API

Исходный процесс предполагает мониторинг еженедельно по понедельникам или on-demand.

API:

```http
GET /api/v1/datasets/deposit-offers-retail/coverage
GET /api/v1/datasets/deposit-offers-retail/records?view=current
```

Optional field evidence:

```http
GET /api/v1/datasets/deposit-offers-retail/records?view=current&include=evidence
```

Внешний ИИ сначала проверяет coverage, потом использует records. Он не должен интерпретировать отсутствие банка в ответе как «у банка нет вкладов», если source workflow не запускался/упал.

---

# 9. Shared regulatory indicators

НБРБ из исходного Excel должен быть отдельным dataset, общим для аналитики ЮЛ/ФЛ, а не «банком» в retail dataset:

- https://www.nbrb.by/statistics/MonetaryPolicyInstruments/RefinancingRate
- https://www.nbrb.by/statistics/rates/ratesDaily
- https://www.nbrb.by/statistics/valuables/bankingots

Рекомендуемое имя: `market-indicators` или `regulatory-indicators`.

---

# 10. Токены — отдельный scope

Исходный Excel перечисляет token marketplaces:

- https://bynex.io/investment/ru/ico
- https://finstore.by/kupit-tokeny/
- https://whitebird.io/ico
- https://fainex.by/#buing

Не смешивать с retail deposits. Если проект решит их включить — отдельная schema/dataset `token-offers`.

В исходном паспорте была идея работать «через личный кабинет». Это **не допускается** философией Multiverse: только anonymous-public content. Login/session/private API не автоматизировать.

---

# 11. Definition of Done одного retail preset

`VERIFIED` только если:

- canonical URL и retail role доказаны;
- listing scope не содержит corporate/global/recommendation noise;
- all product details reconciled;
- filters/tabs либо покрыты, либо доказано, что данные уже присутствуют без действий;
- current revision определена;
- core schema/required coverage проходит;
- raw + normalized values/evidence сохраняются;
- natural key стабилен;
- zero products не считается success;
- fixture + live smoke проходят;
- второй unchanged run не создаёт duplicate version;
- rate/condition change создаёт новую version;
- никаких bank-specific нод/hostname conditions в generic code.

---

# 12. Репозиторий Multiverse

Coding-агенту начинать с актуального `main`:

- https://github.com/Vadimohka/Multiverse
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/contracts.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/strategies.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/nodes.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/presets.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/data.py

На дату исследования Contract v2 и семь фаз уже существуют. Не переписывать их; использовать EPIC `EPIC_MULTIVERSE_BELARUS_MARKET_DATA.md` как план gap closure.
