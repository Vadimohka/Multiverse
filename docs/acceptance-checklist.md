# Приёмочный чек-лист MVP

| Сценарий | Статус | Где реализовано |
|---|---|---|
| Вход в систему и RBAC | Готово | `routers/auth.py`, `dependencies.py`, Users UI |
| Создание проекта | Готово | Projects API/UI |
| Добавление URL | Готово | Sources API/UI |
| Source Profiler | Готово | HTTP/HTML/JS/XHR/document/CAPTCHA analysis |
| Выбор HTTP/Playwright/document | Готово | Source wizard и workflow template |
| Создание схемы | Готово для flat/nested JSON Schema | Schema builder UI/API |
| Визуальный workflow | Готово | React Flow canvas, node catalog, inspector |
| Выбор CSS/XPath | Готово: screenshot, интерактивные области и candidate picker | Selector snapshot/modal |
| Карточки и поля продукта | Готово | Repeating List field mapping |
| Нормализация валюты/срока/ставки | Готово | Transform node/normalizers |
| LLM fallback | Готово как LLM node и condition branch | DeepSeek/OpenAI-compatible |
| Редактирование prompt | Готово | Prompt manager и node inspector |
| Тест отдельного узла | Готово | `/workflows/node-test` |
| Результат каждого узла | Готово | Run details UI/API |
| Публикация версии | Готово | WorkflowVersion |
| Cron schedule | Готово | Schedules + Celery Beat matcher |
| Diff с прошлой версией | Готово | hash/natural key/RecordVersion |
| Подтверждение изменения | Готово | Review Queue |
| Internal PostgreSQL dataset | Готово | SQLAlchemy models/migrations |
| External PostgreSQL/MySQL | Готово для insert/upsert | Save External DB node |
| XLSX export | Готово и smoke-tested | Export API/node |
| Raw HTML/screenshot/network | Готово | ArtifactStorage/RawDocument |
| Audit log | Готово | audit service/API/UI |
| PDF/DOCX/XLSX/CSV/JSON → dataset | Готово: upload source, raw artifact, document workflow, parser | documents API/UI, document worker/node |

## Частичные пункты

- Авторизованный browser profile: storage state и настройки хранятся, но интерактивный noVNC login wizard отсутствует.
- Schedule: 5-польный cron и worker dispatch работают; расширенные interval presets UI ограничены.
- Large-scale targets исходного ТЗ требуют отдельного performance qualification.
