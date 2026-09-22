# Formwork — AI Form Filler

Phase 1 implements target ingestion and the Template Inspector. It keeps uploaded originals immutable, exposes native PDF/XLSX structure, allows field correction, and publishes immutable template versions. Evidence extraction, mapping, review, and final output generation are intentionally not present yet.

## What is implemented

- React/TypeScript inspector with PDF field overlays, bounded workbook grid navigation, field add/edit/delete, writable/required/semantic metadata, draft save, and version publishing.
- FastAPI `/api/v1` API, SQLAlchemy models, Alembic migration, revision conflict protection, processing-run progress, immutable SHA-256 object keys, and a Celery worker entry point.
- Native PDF inspection with field-tree/widget reconciliation, exact rectangles, page rotation, signature/action exclusion, and rendered previews.
- Workbook inspection with hidden/protected sheets, formulas, merged ranges, tables, names, standard and x14 extended validations, broken-reference warnings, and relevant formatting in the grid.
- A constrained OOXML preservation spike that modifies only the selected worksheet part and rejects formula replacement.
- A Reducto asynchronous adapter boundary. Live Reducto parsing requires a valid `REDUCTO_API_KEY` and provider endpoint compatibility; native inspection is fully usable without it.

The two supplied ZIPs and any extracted customer documents are ignored by Git. Filled reference outputs are not used by the application or test evidence.

## Run locally now (no container runtime required)

The machine used for this phase did not have Docker installed, so the default local profile uses SQLite and filesystem object storage.

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd web
pnpm install
pnpm dev
```

Open [http://localhost:5173](http://localhost:5173). API documentation is at [http://localhost:8000/docs](http://localhost:8000/docs).

For the intended service stack, install Docker Desktop or another Compose-compatible runtime and run `docker compose up --build`. This uses PostgreSQL, Redis, MinIO, the API, and a Celery worker.

## Automated verification

```bash
uv run ruff check app tests migrations
uv run pytest -q
cd web
pnpm build
pnpm test
pnpm test:e2e
```

Regenerate TypeScript OpenAPI contracts after an API change with `cd web && pnpm contracts`.

## Manual Phase 1 acceptance walkthrough

1. Start the API and web application using the local commands above.
2. From `input and filled form 1.zip`, extract and upload only `black elite limo inc (1).xlsx`. Do not upload the filled reference workbook.
3. Confirm five sheets appear, including hidden `Data` and `Broker List`; select standard and extended dropdown ranges and confirm the location card and grid navigation agree.
4. From `input and filled form 2.zip`, upload only `RivingtonDriverVehicleTemplate_12-10-2024 (3).xlsx`. Confirm `Drivers`, `Vehicles`, and hidden `Data`, two tables, named ranges, 12 x14 validations, and the existing broken `#REF!` validation warning.
5. Upload only `Rivington Supplemental_11-15-2025_FINAL (3).pdf`. Confirm two pages, 486 field-tree entries, 407 page widgets, and non-writable signature/action fields. Select fields on both pages and confirm the highlight and location coordinates agree.
6. Change a label, type, semantic type, requiredness, writable status, and notes; add and delete a field; save the draft.
7. Open the same template again and confirm the draft persists. Publish a version. Make another change and confirm publishing is disabled until saved.
8. In two browser tabs, edit the same saved revision. Save one, then save the other; confirm the second receives a revision conflict rather than overwriting the first.
9. For a flat/scanned PDF, confirm the inspector reports that no native widgets were found, then add a manual field and save it. For a rotated/hybrid form, confirm native widget rotation and coordinates remain visible.

## Measured sample inspection results

The original targets were inspected directly in-memory from the supplied archives:

| Original target | Native result |
|---|---|
| Elite workbook | 5 sheets (2 hidden, 2 protected), 35 defined names, 2 standard + 22 x14 validations |
| Rivington driver/vehicle workbook | 3 sheets (1 hidden), 2 tables, 26 defined names, 1 standard + 12 x14 validations; existing broken validation exposed |
| Rivington supplemental PDF | 2 pages, 486 field-tree entries, 407 widgets, 3 excluded non-business controls |

These are structural measurements, not claims about independent business-field accuracy.
