# AI Form Filler — Final Implementation Plan

## 1. Objective and confirmed decisions

Build a local application that turns source documents into reviewed, evidence-backed PDF and XLSX outputs. Each processing stage must expose its inputs, results, and provenance in the application.

**Implementation proceeds one phase at a time. After each phase, run automated tests and evaluations, provide manual testing instructions, and stop for your approval.**

Confirmed choices:

- React/TypeScript frontend and Python/FastAPI backend.
- Modular monolith with background workers, PostgreSQL, and private object storage.
- Reducto as the primary parser; native PDF/XLSX inspection supplements it.
- OpenAI behind a configurable Model Gateway.
- Single-reviewer local pilot through Phase 6.
- Required-field blanks or N/A may receive an explicit, audited reviewer exception.
- Repeating-record overflow blocks finalization; no silent truncation or automatic continuation pages.
- Prefilled values retain provenance and require review before conflicting replacements.
- No signatures, autonomous submission, unsupported inference, or derivations deeper than two.

### Primary acceptance cases

| Case | Inputs | Reference outputs |
|---|---|---|
| **ZIP 1: Elite Chauffer** | Email PDF, scanned driver’s license, existing application workbook | Updated application workbook |
| **ZIP 2: Rivington** | Mohammad’s source PDF, original Rivington PDF and driver/vehicle workbook | Filled PDF and XLSX |

Keep these cases isolated. Filled reference outputs belong exclusively to evaluation and must never enter extraction or mapping evidence.

Validate reference values against their inputs before labeling them correct. Existing output defects become regression cases: lost dropdowns, exposed/deleted hidden sheets, removed tables/names, and changed formulas.

## 2. Application architecture and contracts

### Technology and structure

- **Web:** React, Vite, TypeScript, React Router, TanStack Query, Tailwind, and accessible UI components.
- **API:** FastAPI, Pydantic, SQLAlchemy, and Alembic.
- **Workers:** Celery and Redis; PostgreSQL holds authoritative run status and results.
- **Storage:** S3-compatible storage with MinIO locally. Originals are immutable and content-hashed.
- **Development:** pnpm, uv, and Docker Compose. Verify/install the local container runtime during setup.
- **Tests:** pytest, Vitest, and Playwright.

Organize the backend into Case, Artifact, Template, Evidence, Mapping, Verification, Review, and Finalization modules. API and worker processes use the same domain services.

Introduce database tables incrementally. Phase 1 needs artifacts, template drafts/versions, and processing runs; later phases add their own records.

### Stable interfaces

| Interface or entity | Responsibility |
|---|---|
| `ParserAdapter` | Convert provider output into canonical content blocks and source locations. |
| `TemplateVersion` | Describe fields, writable locations, repeating groups, constraints, and approved defaults. |
| `EvidenceSnapshot` | Freeze facts, entity associations, provenance, and extraction versions. |
| `FillPlanRevision` | Store candidates, selections, derivations, states, issues, and review history. |
| `VerificationReport` | Record findings against an exact revision without changing values. |
| `ReviewDecision` | Record human actions, previous/new values, actor, reason, and revision. |
| `Renderer` | Write an approved revision into a clone of the original artifact. |
| `CalculationAdapter` | Recalculate supported workbook formulas and report results or limitations. |

Canonical provenance supports PDF page/rectangle, spreadsheet sheet/cell-range, and text character spans. Retain original coordinate systems and page rotation transforms.

Expose `/api/v1` REST resources for uploads, templates, cases, evidence, processing runs, Fill Plans, review, approval, and outputs. Generate TypeScript contracts from OpenAPI. Background operations return run IDs; the UI polls for progress.

Edits require the expected revision. Stale edits and late worker results cannot overwrite newer decisions.

### Model Gateway and execution trace

Use the OpenAI Python SDK and Responses API with Pydantic structured outputs. Configure models separately for template analysis, evidence extraction, mapping, and verification. Use `gpt-6-astra` as the initial evaluation baseline where available; keep model identifiers outside business logic.

Record input references, model/provider, prompt/schema version, duration, usage, errors, and result references. Handle refusals and incomplete responses explicitly.

Treat document text as untrusted content. Instructions inside emails may be extracted as proposed case directions, but require reviewer confirmation before becoming application instructions.

## 3. Document processing and authority rules

### Parsing and PDF handling

Reducto handles primary parsing through an asynchronous adapter. Workers persist provider job IDs, poll status, and normalize both inline and URL-backed results. Retain raw responses for diagnosis and pin parser configuration.

Use:

- **pypdf:** field trees, widgets, native writes, and PDF assembly.
- **PDF.js:** browser previews and interactive overlays.
- **pypdfium2:** server-side rendering with form rendering enabled where applicable.
- **ReportLab:** deterministic overlays for flat/scanned PDFs.

Reconcile logical fields with their widgets. The Rivington sample’s 486 field-tree entries and 407 page widgets are structural fixtures, not expected counts of independent business fields.

Native widget geometry takes priority. Flat/scanned field detection combines parsed layout, visual analysis, and manual corrections. Unknown requiredness remains unknown.

Exclude signatures and action buttons from automatic filling. Verify saved field values, widget appearances, and visual placement.

### Spreadsheet inspection, writing, and calculation

Use openpyxl for convenient inspection and direct OOXML inspection for extended validations, conditional formatting, relationships, and other unsupported features.

Implement a constrained ZIP/XML writer that changes approved cells while preserving unrelated workbook parts. Do not save originals through a general workbook round trip.

The inspector must expose:

- Sheet roles and hidden/protected state.
- Writable, formula, reference, locked, and unknown regions.
- Merged cells, tables, named ranges, dropdowns, and relevant formatting.
- Broken validation references and unsupported calculation dependencies.

Use a virtualized browser grid with cell/range highlighting and relevant formatting. Do not instantiate millions of cells merely because validation spans an entire column.

Effective protection depends on sheet protection and approved template permissions, not a cell’s locked flag alone. Formula replacement requires an explicit template edit.

For recalculation, run LibreOffice on a disposable copy and transfer only validated cached results into the preserved workbook. Unsupported calculations, missing external dependencies, or unverifiable results block affected output.

Template repair is separate from filling: show the defect, require an explicit template edit, and publish a new version.

### Evidence, derivations, and human decisions

Preserve the PRD’s separate resolution, origin, verification, and review dimensions.

- Every nonhuman proposal must reference evidence or an existing workbook formula.
- Preserve contradictory evidence and uncertain entity matches.
- Dynamically proposed derivations must expose inputs, transformation, output, and depth.
- Enforce acyclic derivation graphs with maximum depth two.
- Evaluate numeric expressions using a restricted expression language; never execute model-generated code.
- Require human review of every derived proposal.
- Record the as-of date used for date-dependent derivations.

For prefilled targets, distinguish current-case evidence, reusable defaults, and previous-applicant content. Explicit reviewer decisions must determine whether existing case-specific values are retained, replaced, or cleared. A reviewer may approve a selected group of carry-forward values, with the decision recorded for each field.

Human decisions set origin to `HUMAN` and retain earlier proposals/provenance. They receive no subsequent AI semantic verification. Deterministic checks still apply.

An upstream edit invalidates dependent nonhuman proposals. Human-approved dependent values remain unchanged but receive a dependency-change notice requiring acknowledgement.

Required-field exceptions need a reason. They cannot waive corruption, forbidden writes, capacity overflow, or other technical integrity failures.

## 4. Phases and acceptance gates

### Phase 0 — Planning

Finalize architecture, sample roles, module contracts, and this implementation sequence.

**Exit:** approved implementation plan. Live provider access remains a setup prerequisite; no production features are implemented during planning.

### Phase 1 — Target ingestion and Template Inspector

Build the application shell, upload/storage path, background runs, Reducto/native inspection, and template editing/publishing.

Support PDF highlighting and workbook sheet/grid inspection. Allow editing labels, types, semantic types, requiredness, writable status, locations, and repeating groups; support adding/deleting fields.

Include a narrow preservation feasibility spike: change a cell in a disposable workbook copy and verify that extended validations, hidden sheets, tables, names, and unrelated formulas survive. Full finalization remains in Phase 6.

**Acceptance:**

- Upload and inspect the original targets from both ZIPs.
- Select fields/ranges and see the corresponding location.
- Correct detections, publish a version, and reopen it.
- Distinguish PDF signatures/actions from business inputs.
- Expose extended Excel dropdowns and existing defects.
- Test flat, scanned, rotated, and hybrid PDF fixtures.

**Stop for approval.**

### Phase 2 — Source ingestion and Evidence Inspector

Support PDF, images, XLSX, and raw text. Produce immutable evidence snapshots with exact snippets and source navigation.

Separate parsing from semantic extraction. Preserve entity, date/period, and unit context. Keep duplicate and conflicting facts inspectable.

**Acceptance:**

- Inspect scanned-license facts against the original image.
- Preserve explicit “no given name” semantics.
- Handle wrapped identifiers and unreadable regions without guessing.
- Distinguish applicant information from broker signatures and quoted email content.
- Navigate every accepted fact to its source.
- Test multiple sheets and immutable same-workbook source snapshots.

No target mapping yet.

**Stop for approval.**

### Phase 3 — Mapping and Fill Plan Inspector

Build candidate retrieval, type/entity-aware mapping, normalization, grounded derivations, repeating records, and target overlays.

Start with semantic/type/entity filtering and PostgreSQL text search. Add vector retrieval only if measured retrieval failures justify it.

Show selected values, alternatives, evidence, derivations, and unresolved issues. Preserve original target files.

**Acceptance:**

- Correct direct and normalized proposals.
- Explicit conflicts, ambiguity, and missing information.
- Valid depth-one/two derivations; rejected cycles and depth-three proposals.
- No unsupported name splitting, value correction, or inherited applicant data.
- Overflow retains all records and creates a blocker.
- Both outputs in ZIP 2 use the same frozen case evidence.

No independent verifier yet.

**Stop for approval.**

### Phase 4 — Deterministic validation and independent verification

Run deterministic field/section checks first, then a separate verifier invocation. Give the verifier access to the evidence snapshot for missed-evidence checks.

It may pass, fail, identify additional evidence, or create issues. It cannot change values or trigger an automatic mapper/verifier debate.

**Acceptance:**

Test incorrect entities, reporting periods, unsupported derivations, missed facts, and valid proposals. Measure error detection and false rejection. Assert that verification never mutates selected values.

**Stop for approval.**

### Phase 5 — Human review

Add review-by-exception, evidence panels, approval/edit/candidate selection, clear/N/A/intentional blank, logical control editing, and decision history.

Update overlays immediately. Recalculate affected workbook previews when supported, identifying stale results clearly. Approvals apply to exact revisions.

**Acceptance:**

Complete both case reviews inside the application. Test persistence, stale edits, terminal human authority, dependency notices, required-field exceptions, and prefilled-value disposition.

No final document regeneration after individual edits.

**Stop for approval.**

### Phase 6 — Finalization and outputs

Finalize an approved revision through deterministic checks, original-artifact cloning, approved writes, calculation, integrity checks, and download publication.

Provide final previews, artifacts, and a readable audit note plus machine-readable audit data. Track each artifact’s status independently; show case completion only when all requested outputs succeed.

**Acceptance:**

- Written values match the approved revision.
- PDF field values and visual appearances agree.
- Workbook formulas, tables, names, hidden states, dropdowns, and unrelated package content remain intact.
- Calculation results match reviewed results; material differences return the output for review.
- Failed artifacts are not offered as successful downloads.
- Reference-output preservation defects are not reproduced.

**Stop for approval.**

### Phase 7 — Integration and production hardening

Add authenticated workspaces, authorization/isolation, retention/deletion, recovery workflows, observability, usage/cost reporting, template-version management, and deployment documentation.

Basic immutable storage, revision checks, deduplication, and safe logging begin earlier when needed; this phase expands operational coverage.

**Acceptance:** full workflows, cross-workspace access tests, worker crashes, duplicate delivery, retries, deletion, restore, and performance checks. Public deployment is a separate action after these checks pass.

## 5. Evaluation and delivery requirements

Maintain distinct expected results for template detection, extraction, mapping, verification, review, and rendering. This keeps failures attributable to their owning stage.

For each labeled value, record expected resolution, origin, value, evidence, derivation, review requirement, and validation outcome. Reference outputs alone do not establish factual correctness.

Use:

- Synthetic fixtures for controlled edge cases.
- The two ZIP cases for representative acceptance.
- Mocked/recorded provider responses for repeatable CI.
- Explicit live runs for parser/model quality measurements.

Report phase metrics and individual failures rather than claiming unmeasured accuracy.

Hard requirements for the acceptance suite:

- Zero unsupported finalized values.
- Complete provenance for accepted nonhuman values.
- No AI alteration or semantic re-verification of human decisions.
- No unintended workbook structural changes.
- Faithful rendering of the approved revision.

Keep original samples private and unchanged. Runtime credentials and real customer documents stay outside version control.

**The first implementation deliverable is Phase 1 only, ending at the Template Inspector approval gate.**
