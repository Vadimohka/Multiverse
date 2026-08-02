# Архитектура и решения

- Raw-first: HTTP node вычисляет SHA-256 и регистрирует raw artifact; модель `raw_documents` готова к S3/MinIO persistence.
- Deterministic-first: HTTP, HTML, CSS, repeating list, JSONPath, transform, validate и deduplicate выполняются до LLM.
- Versioning: source/schema/workflow/prompt version, immutable `workflow_versions`, record versions.
- Human-in-the-loop: review tasks не перезаписывают подтверждённые записи до решения оператора.
- Security: PBKDF2 password hashes, JWT, encrypted secrets, RBAC, no eval and no SQL concatenation.
- Execution: isolated node registry, DAG validation, topological order, node timeout, artifacts and node-run callbacks.
