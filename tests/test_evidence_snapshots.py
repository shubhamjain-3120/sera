import io
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Artifact, ArtifactKind, ArtifactPurpose, EvidenceSnapshot, ProcessingRun
from app.services import ingest_evidence
from app.storage import FilesystemStorage


def test_evidence_ingestion_keeps_original_and_reuses_identical_immutable_snapshot(tmp_path: Path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    storage = FilesystemStorage(tmp_path / "objects")
    original = b"Given name: No given name\nDOB: 2020-01-02\n"
    key, digest, size = storage.put_immutable(io.BytesIO(original), ".txt")
    session = Session(engine)
    artifact = Artifact(
        filename="source.txt",
        media_type="text/plain",
        kind=ArtifactKind.TEXT,
        purpose=ArtifactPurpose.SOURCE,
        case_key="synthetic",
        sha256=digest,
        size_bytes=size,
        storage_key=key,
    )
    session.add(artifact)
    session.flush()
    first_run = ProcessingRun(
        artifact_id=artifact.id,
        stage="evidence-ingestion",
        config_snapshot={"allow_native_fallback": True},
    )
    session.add(first_run)
    session.commit()
    ingest_evidence(session, storage, first_run.id)
    first_snapshot = session.scalar(select(EvidenceSnapshot))
    assert first_snapshot is not None
    before_hash = first_snapshot.snapshot_sha256

    second_run = ProcessingRun(
        artifact_id=artifact.id,
        stage="evidence-ingestion",
        config_snapshot={"allow_native_fallback": True},
    )
    session.add(second_run)
    session.commit()
    ingest_evidence(session, storage, second_run.id)

    assert session.scalars(select(EvidenceSnapshot)).all() == [first_snapshot]
    assert second_run.result["snapshot_id"] == first_snapshot.id
    assert first_snapshot.snapshot_sha256 == before_hash
    with storage.open(key) as source:
        assert source.read() == original


def test_same_workbook_source_creates_snapshot_without_modifying_workbook(tmp_path: Path):
    workbook = Workbook()
    workbook.active.title = "Applicant"
    workbook.active.append(["Given name", "Amina"])
    workbook.create_sheet("Vehicles").append(["VIN", "1ABC234"])
    stream = io.BytesIO()
    workbook.save(stream)
    original = stream.getvalue()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    storage = FilesystemStorage(tmp_path / "objects")
    key, digest, size = storage.put_immutable(io.BytesIO(original), ".xlsx")
    session = Session(engine)
    artifact = Artifact(
        filename="source.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        kind=ArtifactKind.XLSX,
        purpose=ArtifactPurpose.SOURCE,
        case_key="synthetic",
        sha256=digest,
        size_bytes=size,
        storage_key=key,
    )
    session.add(artifact)
    session.flush()
    run = ProcessingRun(
        artifact_id=artifact.id,
        stage="evidence-ingestion",
        config_snapshot={"allow_native_fallback": True},
    )
    session.add(run)
    session.commit()
    ingest_evidence(session, storage, run.id)

    snapshot = session.scalar(select(EvidenceSnapshot))
    assert snapshot is not None
    assert {block["source"]["sheet"] for block in snapshot.snapshot["parse_blocks"]} == {
        "Applicant",
        "Vehicles",
    }
    with storage.open(key) as source:
        assert source.read() == original
