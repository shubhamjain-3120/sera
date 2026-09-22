# Formwork — AI Form Filler

The canonical phased roadmap and acceptance gates are documented in
[`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md). Consult it before starting each phase.

Phase 1 implements target ingestion and the Template Inspector. Phase 2 adds isolated source ingestion and an Evidence Inspector. Phase 3 adds grounded mapping and the Fill Plan Inspector. Originals remain immutable and content-addressed. Independent verification, human approval, and final output generation intentionally remain for later phases.

## What is implemented

- React/TypeScript inspector with PDF field overlays, bounded workbook grid navigation, field add/edit/delete, writable/required/semantic metadata, draft save, and version publishing.
- FastAPI `/api/v1` API, SQLAlchemy models, Alembic migration, revision conflict protection, processing-run progress, immutable SHA-256 object keys, and a Celery worker entry point.
- Native PDF inspection with field-tree/widget reconciliation, exact rectangles, page rotation, signature/action exclusion, rendered previews, and geometry-based visible-label proposals.
- Workbook inspection with hidden/protected sheets, formulas, merged ranges, tables, names, standard and x14 extended validations, broken-reference warnings, and relevant formatting in the grid.
- A constrained OOXML preservation spike that modifies only the selected worksheet part and rejects formula replacement.
- Reducto parsing when `REDUCTO_API_KEY` is configured, including URL-backed results, OCR word geometry, and provider trace capture. Native `pdfplumber` layout extraction remains the offline fallback and `pypdf` widgets remain authoritative for form targets.

PDF fields retain their native identity separately from their proposed human-readable label. Every layout proposal records confidence and the exact nearby text rectangles that produced it. Proposals remain in `needs_review` state; editing a label records it as a human-confirmed value. The system intentionally does not invent semantic ontology identifiers when only a nearby visual label is known.

Inherited page widgets are reconciled through their terminal AcroForm parent. Widgets that carry their own `/T` or `/FT` remain distinct terminal fields even when they share a structural parent. A Yes/No control with two inherited widgets is therefore one logical field, while sibling text cells in a table remain independent.

- A Reducto asynchronous adapter boundary. Live evidence parsing requires a valid `REDUCTO_API_KEY`; native target inspection remains usable without it.
- Separate source parsing and semantic extraction stages. Production evidence ingestion requires Reducto and never silently substitutes local OCR. Native parsing remains available only for deterministic tests, source-only evaluation, and structural inspection.
- Immutable, content-hashed evidence snapshots with facts, entity roles, exact provenance, parser/extractor versions, date/unit context, confidence, uncertainty, duplicates, contradictions, and unreadable regions.
- Canonical navigation for PDF and image rectangles, PDF rotation transforms, workbook sheet/cell ranges, and text character spans. Accepted facts always retain at least one exact source locator.
- Untrusted-content controls: quoted email, signatures, and document-authored directions are not accepted as applicant facts or application instructions. Filled/reference outputs are rejected by the source endpoint.
- Frozen, case-scoped evidence bundles shared across target outputs, with one current immutable snapshot per source and explicit snapshot selection support.
- Immutable Fill Plan revisions with optimistic concurrency, published template-version pinning, native target locations, exact evidence provenance/confidence, selected proposals, alternatives, and unresolved issues.
- Conservative semantic/type/entity mapping with PostgreSQL full-text candidate retrieval, deterministic SQLite test behavior, exact choice matching, and auditable boolean/date/number normalization.
- Grounded restricted derivations with exposed inputs and as-of dates, maximum depth two, cycle rejection, and mandatory later human review. Model-generated code is never executed.
- Repeating-record accounting that retains every entity and creates a blocker when target capacity is exceeded. Prefilled target values require later disposition and are never treated as current-case evidence.

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

Open `/evidence` for Phase 2 source ingestion. Choose the representative case before uploading so evidence stays isolated.

Open `/fill-plans` for Phase 3 mapping. Publish a target template first, choose the matching case, and create a Fill Plan against the latest published version and frozen case evidence.

Evidence ingestion requires `REDUCTO_API_KEY`. The API returns a clear `503` instead of producing lower-quality native OCR evidence when credentials are missing.

For the intended service stack, install Docker Desktop or another Compose-compatible runtime and run `docker compose up --build`. This uses PostgreSQL, Redis, MinIO, the API, and a Celery worker.

## Automated verification

```bash
uv run ruff check app tests migrations
uv run pytest -q
uv run python -m scripts.evaluate_phase2 "/path/to/input and filled form 1.zip" "/path/to/input and filled form 2.zip"
uv run python -m scripts.evaluate_phase3
cd web
pnpm build
pnpm test
pnpm test:e2e
```

Regenerate TypeScript OpenAPI contracts after an API change with `cd web && pnpm contracts`.

## Manual Phase 3 acceptance walkthrough

1. Complete the Phase 1 and 2 walkthroughs for a case and publish each target template version. Never upload a filled reference output as evidence.
2. Open `/fill-plans`, select the case, and create a Fill Plan for a published target. Confirm the header shows a frozen evidence hash and the center panel shows the unchanged native PDF widgets or XLSX cells.
3. Select directly matched and normalized proposals. Confirm the right panel shows the selected value, unchanged evidence confidence, match score, resolution, and exact source location. Open the immutable source link and compare it with the proposal.
4. Inspect an ambiguous or contradictory field. Confirm different values remain alternatives, the target stays unresolved, and the application does not silently select one.
5. Inspect a required field with no grounded fact. Confirm it is unresolved with a blocker. Inspect signatures, actions, and non-writable targets and confirm they are explicitly excluded.
6. Use synthetic tests to exercise depth-one/two derivations, cycles, depth-three derivations, and repeating-record overflow. Confirm invalid derivations and overflow are blockers and all overflow entity IDs remain recorded.
7. Create Fill Plans for both Rivington outputs without changing case evidence. Confirm both plans have the same evidence-bundle hash.
8. Run the automated commands above, then run `uv run python -m scripts.evaluate_phase3 --require-plans` against the populated local database. Stop for Phase 3 approval; there is no independent verifier, human approval workflow, target write, or regenerated output in this phase.

## Manual Phase 2 acceptance walkthrough

1. Open `/evidence`, select representative case 1, and upload only `Jeev Mail - Limousine_ Elite Chauffer LLC.pdf` and `California's.pdf`. Do not upload the original application workbook or filled workbook as evidence.
2. Open the scanned-license snapshot. Select the given-name fact and confirm it says explicitly absent rather than an empty or invented name. Confirm the source viewer highlights the original image region.
3. Review low-confidence and unreadable license regions. Confirm the inspector does not invent a wrapped/obscured identifier and shows the uncertainty beside its provenance.
4. Open the email snapshot. Confirm applicant/vehicle facts are separate from broker signatures, quoted content, and proposed case directions. Proposed directions must remain unaccepted.
5. Select representative case 2 and upload only `Mohammad-Khalifeh-08-19-2026 (2).pdf`. Do not upload the original Rivington targets or either FILLED output as evidence.
6. Click several accepted facts in each snapshot. Confirm each selection navigates to the correct page/rectangle. For a synthetic XLSX source, confirm facts navigate across multiple sheets and exact cells. For text, confirm exact character spans.
7. Re-ingest an unchanged source. Confirm the original SHA-256 and stored bytes are unchanged and the identical immutable snapshot is reused.
8. Run the automated commands above. Stop here for Phase 2 approval; there is no target-mapping UI or API in this phase.

## Phase 2 source evaluation (2026-09-22)

The evaluator read only the authorized original source members directly from the two supplied archives. It did not extract or inspect target templates or FILLED outputs.

| Case/source | Parse blocks | Facts | Accepted | Review | Unreadable regions | Result |
|---|---:|---:|---:|---:|---:|---|
| Case 1 scanned license (`California's.pdf`) | 37 | 7 | 4 | 3 | 12 | Explicit no-given-name preserved with exact region; local OCR did not recover the license number and did not guess it |
| Case 1 email PDF | 172 | 36 | 19 | 17 | Applicant/vehicle facts extracted; email routing and contradictory values remain reviewable |
| Case 2 Mohammad PDF | 77 | 9 | 9 | 0 | 0 | Source facts extracted with word-derived page rectangles |

Individual observed failures are retained rather than hidden: local OCR did not recover the scanned license number, three scanned-license candidates require review, 12 scan regions were unreadable/low-confidence, and 17 email facts require review due to routing metadata, contradictions, or untrusted direction-like content. Live Reducto output can improve scan coverage while the recorded response fixture keeps CI deterministic.

## Manual Phase 1 acceptance walkthrough

1. Start the API and web application using the local commands above.
2. From `input and filled form 1.zip`, extract and upload only `black elite limo inc (1).xlsx`. Do not upload the filled reference workbook.
3. Confirm five sheets appear, including hidden `Data` and `Broker List`; select standard and extended dropdown ranges and confirm the location card and grid navigation agree.
4. From `input and filled form 2.zip`, upload only `RivingtonDriverVehicleTemplate_12-10-2024 (3).xlsx`. Confirm `Drivers`, `Vehicles`, and hidden `Data`, two tables, named ranges, 12 x14 validations, and the existing broken `#REF!` validation warning.
5. Upload only `Rivington Supplemental_11-15-2025_FINAL (3).pdf`. Confirm two pages, 486 field-tree entries, 407 page widgets, and non-writable signature/action fields. Select fields on both pages and confirm the highlight and location coordinates agree. Review proposed labels such as the driver hiring practices and safety-device options; confirm that the native PDF name and exact supporting text remain visible separately.
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
| Rivington supplemental PDF | 2 pages, 486 field-tree entries, 407 widgets reconciled into 380 widget-backed logical fields, 3 excluded non-business controls |

These are structural measurements, not claims about independent business-field accuracy.
