# Belarus Market Data baseline

Date: 2026-08-13

- `origin/main`: `03ba4646ee056f370918552b05761aa30cf44376`
- Working branch: `codex/universal-parser-regressions` (identical to `origin/main` before this EPIC).
- Test baseline: `175 passed` with four non-failing dependency/parser warnings.
- Migration chain: revisions `0001`–`0015` are present. Live `alembic current` cannot run in this local session because the configured PostgreSQL host `postgres` is unavailable.

## Reused capabilities

- Contract v2 and its fixed seven public phases.
- Immutable `WorkflowBlueprintRevision` / `SourcePresetRevision` compiler and the existing requirement that `VERIFIED` presets have fixture references.
- Public egress guard, HTTP/API/browser strategies, typed artifact handling, source profiling and pagination/list-detail traversal.
- Data API paging, version/observation history and compact provenance.

## Gaps found

- Process was row-to-row only; it had no collection transforms.
- Traverse had no declared half-open date boundary or boundary diagnostics.
- No generic current/effective-revision selection.
- Assure lacked field coverage, source-role and valid-empty-window assertions.
- `deadlineSeconds` was declared in contracts but not counted in strategy budgets.
- Data API had no dataset-wide expected-source coverage or opt-in field-level evidence contract.
- No reviewable Belarus preset pack/importer or source fixture matrix existed.

## Baseline regression repaired

With `from __future__ import annotations`, FastAPI interpreted the `-> None` return annotation on the workflow-template DELETE endpoint as a response body model for HTTP 204 and prevented API import. The route now explicitly uses `response_model=None`; the full test suite imports and passes.
