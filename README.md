# Formwork — reviewed supplemental forms

Formwork is a local PDF/XLSX form-filling app. It preserves the existing template inspection and client-evidence extraction flows, sends a published template and frozen evidence snapshots to `gpt-6-sol` once per new fill, and lets a reviewer complete the form. Blank fields are normal. The reviewer approves the entire form before a downloadable file is generated from a copy of the original template.

## Run locally

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

In another terminal:

```bash
cd web
pnpm install
pnpm dev
```

Open [http://localhost:5173](http://localhost:5173). API docs are at [http://localhost:8000/docs](http://localhost:8000/docs). Evidence ingestion requires `REDUCTO_API_KEY`; mapping requires `OPENAI_API_KEY`. The local default uses SQLite and filesystem object storage. Docker Compose provides PostgreSQL, Redis, MinIO, API, and a Celery worker.

## Workflow

1. Upload a blank PDF or XLSX target, inspect its fields, and publish a template version.
   For a workbook, add the actual row cells you want filled before publishing; a detected dropdown range alone does not identify a particular record row.
2. Upload client intake sources to a case. The evidence pipeline stores source blocks and facts with their original locations. Do not upload filled reference forms as evidence.
3. Create a form fill using the published template and case evidence. One `gpt-6-sol` mapping call runs at high reasoning with standard processing and returns native field values with fact/block citations. Agency details are available to mapping.
4. Review every field in the PDF or workbook overlay. Hover a mapped field to see its stored source snippet. Edit or clear values, optionally linking an intake fact. Unlinked manual entries display “Entered by reviewer.” Existing template values persist until changed.
5. Inspect the business, people, vehicles, coverage, and other intake facts. Used means a current field cites the fact. Unused facts stay visible, and can be linked to a field; they do not block approval.
6. Approve the whole form, preview the generated PDF when applicable, and download the PDF or XLSX. For PDFs, choose **Edit filled PDF** to fill native form fields, add markup, apply redactions, or move, rotate, and delete pages, then download a separate edited copy. The generated output remains unchanged. A native writer error names the affected field and prevents download. Editing mapped values later removes approval and requires another generation.

The database migration removes legacy Fill Plans, review decisions, and verification reports. It preserves templates and evidence snapshots. The reference filled files are for manual comparison only and are never mapping inputs.

## Checks

```bash
uv run ruff check app tests migrations
uv run pytest -q
cd web && pnpm build && pnpm test
```

The template and evidence behavior is described by their existing tests. The form-fill tests cover one mapping call, blank fields, fact usage, manual citation behavior, native output, and writer errors.
