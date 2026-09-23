from io import BytesIO

from PIL import Image

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base, get_db
from app.models import (
    Artifact,
    ArtifactKind,
    ArtifactPurpose,
    EvidenceSnapshot,
    FormFill,
    ProcessingRun,
    TemplateDraft,
    TemplateVersion,
)
from app.storage import FilesystemStorage


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    storage = FilesystemStorage(tmp_path / "objects")
    monkeypatch.setattr(main, "get_storage", lambda: storage)

    def database():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    main.app.dependency_overrides[get_db] = database
    with TestClient(main.app) as test_client:
        yield test_client, engine, storage
    main.app.dependency_overrides.clear()
    engine.dispose()


def _pdf():
    stream = BytesIO()
    drawing = canvas.Canvas(stream, pagesize=(300, 300))
    drawing.drawString(20, 260, "Applicant")
    drawing.save()
    return stream.getvalue()


def _workbook():
    book = Workbook()
    sheet = book.active
    sheet.title = "Application"
    sheet["C3"] = "=1+1"
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def _fill(engine, storage, *, workbook=False):
    original = _workbook() if workbook else _pdf()
    suffix = ".xlsx" if workbook else ".pdf"
    key, digest, size = storage.put_immutable(BytesIO(original), suffix)
    source_key, source_digest, source_size = storage.put_immutable(BytesIO(b"Alice, Bob"), ".txt")
    field = {"id": "applicant", "label": "Applicant", "field_type": "text", "writable": True,
             "location": {"kind": "xlsx_range", "sheet": "Application", "cell_range": "C3"} if workbook else
                         {"kind": "pdf_rect", "page": 1, "rect": [85, 245, 230, 270]},
             "current_value": None}
    with Session(engine, expire_on_commit=False) as session:
        target = Artifact(filename="target" + suffix, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if workbook else "application/pdf", kind=ArtifactKind.XLSX if workbook else ArtifactKind.PDF, purpose=ArtifactPurpose.TARGET, sha256=digest, size_bytes=size, storage_key=key)
        source = Artifact(filename="intake.txt", media_type="text/plain", kind=ArtifactKind.TEXT, purpose=ArtifactPurpose.SOURCE, case_key="case-1", sha256=source_digest, size_bytes=source_size, storage_key=source_key)
        session.add_all([target, source])
        session.flush()
        draft = TemplateDraft(artifact_id=target.id, name="Test form", schema={"fields": [field], "inspection": {}})
        run = ProcessingRun(artifact_id=source.id)
        session.add_all([draft, run])
        session.flush()
        version = TemplateVersion(draft_id=draft.id, version=1, name="Test form", schema=draft.schema, source_revision=1, schema_sha256="a" * 64)
        snapshot = EvidenceSnapshot(artifact_id=source.id, run_id=run.id, parser_provider="test", parser_version="1", extractor_version="1", snapshot_sha256="b" * 64,
            snapshot={"facts": [
                {"id": "fact-a", "label": "Name", "value": "Alice", "entity_role": "applicant", "provenance": [{"excerpt": "Applicant: Alice", "artifact_id": source.id}]},
                {"id": "fact-b", "label": "Other", "value": "Bob", "entity_role": "driver", "provenance": [{"excerpt": "Driver: Bob", "artifact_id": source.id}]},
            ], "parse_blocks": [{"id": "block-a", "text": "Applicant: Alice"}]})
        session.add_all([version, snapshot])
        session.flush()
        fill = FormFill(case_key="case-1", template_version_id=version.id, evidence_snapshot_ids=[snapshot.id], answers=[{"field_id": "applicant", "write_value": "Alice", "origin": "model", "evidence_fact_ids": ["fact-a"], "source_block_ids": []}], state="mapped")
        session.add(fill)
        session.commit()
        return fill.id


def test_review_citations_usage_manual_edit_and_pdf_download(client):
    http, engine, storage = client
    fill_id = _fill(engine, storage)
    review = http.get(f"/api/v1/form-fills/{fill_id}").json()
    assert len(review["fields"]) == 1
    assert review["fields"][0]["snippets"][0]["text"] == "Applicant: Alice"
    assert [item["used"] for item in review["evidence"]] == [True, False]
    linked = http.patch(f"/api/v1/form-fills/{fill_id}/fields/applicant", json={"write_value": "Bob", "evidence_fact_ids": ["fact-b"]}).json()
    assert [item["used"] for item in linked["evidence"]] == [False, True]
    assert linked["fields"][0]["snippets"][0]["text"] == "Driver: Bob"
    manual = http.patch(f"/api/v1/form-fills/{fill_id}/fields/applicant", json={"write_value": "Reviewer name"}).json()
    assert manual["fields"][0]["origin"] == "human"
    assert manual["fields"][0]["snippets"] == []
    assert [item["used"] for item in manual["evidence"]] == [False, False]
    exported = http.post(f"/api/v1/form-fills/{fill_id}/approve-and-export")
    assert exported.status_code == 200
    output = http.get(f"/api/v1/form-fills/{fill_id}/output")
    assert output.status_code == 200
    assert "Reviewer name" in PdfReader(BytesIO(output.content)).pages[0].extract_text()
    assert http.get(f"/api/v1/form-fills/{fill_id}/output?preview=1").headers["content-disposition"].startswith("inline")
    http.patch(f"/api/v1/form-fills/{fill_id}/fields/applicant", json={"write_value": "Changed"})
    assert http.get(f"/api/v1/form-fills/{fill_id}/output").status_code == 404


def test_native_write_failure_names_field_and_has_no_download(client):
    http, engine, storage = client
    fill_id = _fill(engine, storage, workbook=True)
    response = http.post(f"/api/v1/form-fills/{fill_id}/approve-and-export")
    assert response.status_code == 422
    assert response.json()["detail"]["field_id"] == "applicant"
    assert http.get(f"/api/v1/form-fills/{fill_id}/output").status_code == 404


def test_geometry_review_preserves_answer_metadata_and_invalidates_output(client):
    http, engine, storage = client
    fill_id = _fill(engine, storage)
    before = http.get(f"/api/v1/form-fills/{fill_id}").json()
    resized = http.patch(f"/api/v1/form-fills/{fill_id}/fields/applicant/geometry", json={
        "geometry": {"page": 1, "rect": [0.1, 0.2, 0.5, 0.08], "coordinate_system": "normalized-top-left"},
    })
    assert resized.status_code == 200
    assert resized.json()["answers"] == before["answers"]
    assert resized.json()["fields"][0]["field"]["location"]["coordinate_system"] == "normalized-top-left"


def test_review_marks_styles_and_original_provenance_survive_export(client):
    http, engine, storage = client
    fill_id = _fill(engine, storage)
    changed = http.patch(f"/api/v1/form-fills/{fill_id}/fields/applicant", json={
        "write_value": "Reviewer name", "review_status": "accepted",
        "style": {"font": "Times-Roman", "size": 12, "bold": True, "italic": False},
    })
    assert changed.status_code == 200
    answer = changed.json()["fields"][0]
    assert answer["model_proposal"]["evidence_fact_ids"] == ["fact-a"]
    assert answer["original_snippets"][0]["text"] == "Applicant: Alice"
    marks = [
        {"id": f"mark-{kind}", "kind": kind, "text": text, "page": 1,
         "rect": [0.1 + index * 0.1, 0.5, 0.06, 0.05],
         "style": {"font": "Helvetica", "size": 11, "bold": False, "italic": False}}
        for index, (kind, text) in enumerate([("check", "✓"), ("Y", "Y"), ("N", "N")])
    ]
    saved = http.put(f"/api/v1/form-fills/{fill_id}/annotations", json={"annotations": marks})
    assert saved.status_code == 200
    assert len(saved.json()["mapping_metadata"]["annotations"]) == 3
    assert http.post(f"/api/v1/form-fills/{fill_id}/approve-and-export").status_code == 200
    pdf = PdfReader(BytesIO(http.get(f"/api/v1/form-fills/{fill_id}/output").content))
    text = pdf.pages[0].extract_text()
    assert "Reviewer name" in text
    assert "Y" in text and "N" in text
    assert "Applicant: Alice" not in text


def test_invalid_mark_does_not_discard_existing_review(client):
    http, engine, storage = client
    fill_id = _fill(engine, storage)
    original = http.get(f"/api/v1/form-fills/{fill_id}").json()["answers"]
    response = http.put(f"/api/v1/form-fills/{fill_id}/annotations", json={"annotations": [
        {"id": "outside", "kind": "Y", "text": "Y", "page": 1, "rect": [0.9, 0.9, 0.2, 0.2],
         "style": {"font": "Helvetica", "size": 11, "bold": False, "italic": False}}
    ]})
    assert response.status_code == 422
    assert http.get(f"/api/v1/form-fills/{fill_id}").json()["answers"] == original


def test_source_crop_returns_the_cited_pdf_region(client):
    http, engine, storage = client
    fill_id = _fill(engine, storage)
    artifact_id = http.get(f"/api/v1/form-fills/{fill_id}").json()["target_artifact_id"]
    full = Image.open(BytesIO(http.get(f"/api/v1/artifacts/{artifact_id}/pages/1.png?scale=2").content))
    crop = http.get(f"/api/v1/artifacts/{artifact_id}/pages/1/crop.png?x=0.1&y=0.1&width=0.2&height=0.1")
    assert crop.status_code == 200
    image = Image.open(BytesIO(crop.content))
    assert image.width < full.width and image.height < full.height
    assert http.get(f"/api/v1/artifacts/{artifact_id}/pages/1/crop.png?x=0.9&y=0.9&width=0.2&height=0.2").status_code == 422
