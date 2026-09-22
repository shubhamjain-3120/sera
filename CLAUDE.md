# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An evidence-backed PDF/XLSX form filler. Source documents (emails, scanned IDs, workbooks) are parsed and
turned into immutable, provenance-tracked "evidence," which is then mapped onto fields inspected from a
target PDF/XLSX template to produce a Fill Plan, which is deterministically validated and independently
verified. The canonical phased roadmap and acceptance gates live in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) — consult it before starting a new phase of work.
The current state of what's implemented per phase (1–4) is summarized in [`README.md`](README.md).

Originals (targets and evidence sources) are immutable and content-addressed by SHA-256. Filled reference
outputs supplied for evaluation are never used as evidence or by the application; anything with `filled`,
`reference output`, or `evaluation-only` in the filename is rejected at the evidence upload endpoint.

## Commands

Backend (Python 3.12–3.14, managed with `uv`):

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run ruff check app tests migrations
uv run pytest -q
uv run pytest tests/test_fill_plans.py -q                # single file
uv run pytest tests/test_fill_plans.py::test_name -q      # single test
```

Frontend (`web/`, pnpm):

```bash
cd web
pnpm install
pnpm dev
pnpm build          # tsc -b && vite build
pnpm test           # vitest run
pnpm test:e2e       # playwright test
pnpm contracts      # regenerate web/src/generated/api.d.ts from the FastAPI OpenAPI schema after any API change
```

Full service stack (Postgres, Redis, MinIO, API, Celery worker) via `docker compose up --build`. Without
Docker, the local default profile uses SQLite (`./data/formfiller.db`) and filesystem object storage
(`./data/objects`) — set in `.env`, see `app/config.py`.

Phase evaluation scripts (compare structural extraction against the two supplied ZIP fixtures; see README for
what each gate checks):

```bash
uv run python -m scripts.evaluate_phase2 "<zip1>" "<zip2>"
uv run python -m scripts.evaluate_phase3 [--require-plans]
uv run python -m scripts.evaluate_phase4
```

Evidence ingestion (`POST /api/v1/evidence/sources`) requires `REDUCTO_API_KEY` and returns `503` without it —
native OCR is never silently substituted for production evidence. Tests instead set
`ProcessingRun.config_snapshot["allow_native_fallback"] = True`, which routes through `native_parse` (see
`app/evidence/parse.py`) so CI stays deterministic without live credentials. Target inspection (PDF/XLSX
structure) works without Reducto either way; Reducto is only used there as optional layout enrichment.

## Architecture

### Backend pipeline (`app/`)

The system is a modular monolith (FastAPI app + Celery worker sharing the same DB/models), organized around a
strict data pipeline rather than by technical layer. Each stage's inputs/outputs are immutable and
hash-addressed, and the object graph in `app/models.py` mirrors the pipeline directly:

1. **Target ingestion** — `Artifact` (content-addressed upload) → `ProcessingRun` → `app/inspectors/pdf.py` /
   `app/inspectors/xlsx.py` produce a structural `schema` (fields, widgets, cells, validations) stored on a
   `TemplateDraft`. Drafts are editable and revision-guarded (optimistic concurrency via `expected_revision`,
   raises `RevisionConflict` → HTTP 409); `publish_draft` freezes a draft into an immutable, hashed
   `TemplateVersion`.
2. **Evidence ingestion** — a separate `Artifact` (`purpose=SOURCE`, scoped by `case_key`) is parsed by
   `app/parsers/reducto.py` (or `app/parsers/recorded.py`/native fallback in tests) into canonical `blocks`,
   then `app/evidence/extract.py` turns blocks into an immutable `EvidenceSnapshot`: typed facts with entity
   roles, exact provenance locators (PDF rect, workbook cell, text span), confidence, and explicit
   absent/unreadable/contradictory markers. Untrusted content (quoted email, signatures, document-authored
   instructions) is filtered out and never accepted as applicant fact or instruction.
3. **Bundling** — `create_fill_plan` (`app/services.py`) groups a case's current snapshots (one per source
   artifact, by default the latest) into a hash-addressed `EvidenceBundle` that is frozen and shared across
   fill plans for that case. `case_key` is the evidence isolation boundary — it is what keeps one client's
   documents out of another's fill plan — and is derived per upload batch by `new_case_key`, not chosen from
   a fixed list.
4. **Mapping** — `app/mapping.py` (`build_model_fill_plan`) sends every frozen evidence text block and the
   complete template field descriptions to one model call, along with all extracted facts and agency facts.
   The model proposes field values and source citations without a server-built shortlist. The server checks
   provenance, type, native write constraints, forbidden fields, and review conditions before placing values
   in the Fill Plan preview. Low-confidence, ambiguous, or period-missing evidence is presented for review.
   Results are stored as immutable `FillPlanRevision` records. Agency details (`app/agencies.py`) remain a
   separate non-document fact source.
5. **Validation** — `app/verification.py` contains deterministic validation only. New Fill Plan runs do not
   invoke a separate AI verifier or create new verification reports. Historical `VerificationReport` rows
   remain in the database. Editing selections (`revise_fill_plan`) creates a new revision rather than
   mutating the old one.

Versioning/immutability pattern used throughout: mutable draft → `expected_revision`-guarded update →
immutable published/frozen entity, each identified by a canonical-JSON SHA-256 hash (`json.dumps(...,
sort_keys=True, separators=(",", ":"))`). Follow this pattern for any new stage rather than mutating history
in place.

`app/storage.py` provides content-addressed, write-once object storage (`FilesystemStorage` locally,
`S3Storage`/MinIO in Docker) keyed by `originals/<sha256[:2]>/<sha256><suffix>`; files are chmod 0o444 after
write. `app/worker.py` exposes the Celery entrypoint, but request-scoped ingestion currently runs via FastAPI
`BackgroundTasks` (see `inspect_artifact_background`/`ingest_evidence_background` in `app/main.py`), each
opening its own `SessionLocal()` since a background task must not reuse the request-scoped DB session.

### Frontend (`web/`)

React 19 + TypeScript + Vite + TanStack Query + React Router. `web/src/api.ts` is the typed API client built
against `web/src/generated/api.d.ts`, which is generated from the live FastAPI OpenAPI schema — regenerate it
with `pnpm contracts` whenever backend request/response shapes change, rather than hand-editing it.

### PDF field identity

Inherited AcroForm page widgets are reconciled through their terminal parent: a widget that carries its own
`/T` or `/FT` stays a distinct terminal field even if it shares a structural parent with others (so sibling
table cells stay independent), while widgets without their own `/T`/`/FT` collapse into one logical field
under their terminal ancestor (e.g., a Yes/No control backed by two inherited widgets). See
`app/inspectors/pdf.py` and `app/inspectors/layout.py` (visible-label proposals from nearby text, always
recorded with confidence + supporting rectangles, always left in `needs_review` until a human edits them —
the system never invents a semantic identifier from a visual label alone).
