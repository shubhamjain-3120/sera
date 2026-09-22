from __future__ import annotations

import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import main, services
from app.config import Settings
from app.db import Base
from app.models import (
    Agency,
    Artifact,
    ArtifactKind,
    ArtifactPurpose,
    EvidenceSnapshot,
    FillPlan,
    FillPlanRevision,
    ProcessingRun,
    RunStatus,
    TemplateDraft,
    TemplateVersion,
    VerificationReport,
)
from app.schemas import FillPlanCreate


class FakeGateway:
    settings = Settings(openai_api_key="test")

    def model_for(self, stage: str) -> str:
        return {
            "mapping": self.settings.openai_mapping_model,
        }[stage]


def _database(path) -> tuple[Session, Artifact, TemplateDraft, TemplateVersion, EvidenceSnapshot]:
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    artifact = Artifact(
        filename="target.pdf",
        media_type="application/pdf",
        kind=ArtifactKind.PDF,
        sha256=hashlib.sha256(str(path).encode()).hexdigest(),
        size_bytes=123,
        storage_key="target-object",
    )
    session.add(artifact)
    session.flush()
    draft = TemplateDraft(
        artifact_id=artifact.id,
        name="Target",
        revision=1,
        schema={"fields": [], "repeating_groups": [], "inspection": {}},
    )
    session.add(draft)
    session.flush()
    version = TemplateVersion(
        draft_id=draft.id,
        version=1,
        name=draft.name,
        schema=draft.schema,
        source_revision=1,
        schema_sha256="b" * 64,
    )
    session.add(version)
    agency = Agency(key="test-agency", name="Test Agency", details={}, is_default=True)
    session.add(agency)
    run = ProcessingRun(artifact_id=artifact.id)
    session.add(run)
    session.flush()
    source = Artifact(
        filename="source.pdf",
        media_type="application/pdf",
        kind=ArtifactKind.PDF,
        purpose="source",
        case_key="case-1",
        sha256="c" * 64,
        size_bytes=45,
        storage_key="source-object",
    )
    session.add(source)
    session.flush()
    snapshot = EvidenceSnapshot(
        artifact_id=source.id,
        run_id=run.id,
        parser_provider="test",
        parser_version="test-v1",
        extractor_version="test-v1",
        snapshot_sha256="d" * 64,
        snapshot={"facts": []},
    )
    session.add(snapshot)
    session.commit()
    return session, artifact, draft, version, snapshot


def test_post_fill_plan_guards_stale_draft_and_older_published_version(tmp_path):
    session, _artifact, draft, version, _snapshot = _database(tmp_path / "stale.sqlite")
    draft.revision = 2
    session.commit()
    request = FillPlanCreate(case_key="case-1", template_version_id=version.id)
    with pytest.raises(HTTPException) as stale:
        main.post_fill_plan(BackgroundTasks(), request, session)
    assert stale.value.status_code == 409
    assert "unpublished" in stale.value.detail

    draft.revision = 1
    later = TemplateVersion(
        draft_id=draft.id,
        version=2,
        name=draft.name,
        schema=draft.schema,
        source_revision=1,
        schema_sha256="e" * 64,
    )
    session.add(later)
    session.commit()
    with pytest.raises(HTTPException) as old_version:
        main.post_fill_plan(BackgroundTasks(), request, session)
    assert old_version.value.status_code == 409
    assert "latest published" in old_version.value.detail


def test_async_run_idempotency_profile_change_retry_and_polling(tmp_path):
    session, _artifact, _draft, version, snapshot = _database(tmp_path / "retry.sqlite")
    request = FillPlanCreate(
        case_key="case-1",
        template_version_id=version.id,
        evidence_snapshot_ids=[snapshot.id],
    )
    first = main.post_fill_plan(BackgroundTasks(), request, session)
    assert first.status == "queued"
    assert first.stage == "queued"
    assert main.get_run(first.id, session).id == first.id

    duplicate = main.post_fill_plan(BackgroundTasks(), request, session)
    assert duplicate.id == first.id
    run = session.get(ProcessingRun, first.id)
    run.status = RunStatus.FAILED
    run.error = "provider unavailable"
    session.commit()
    retry = main.post_fill_plan(BackgroundTasks(), request, session)
    assert retry.id != first.id

    agency = session.scalar(select(Agency).where(Agency.key == "test-agency"))
    agency.details = {"legal_name": "New verified agency detail"}
    session.commit()
    changed_profile = main.post_fill_plan(BackgroundTasks(), request, session)
    assert changed_profile.id not in {first.id, retry.id}
    assert session.get(ProcessingRun, changed_profile.id).config_snapshot["mapping_profile_hash"] != session.get(
        ProcessingRun, retry.id
    ).config_snapshot["mapping_profile_hash"]


def test_failed_model_run_is_visible_and_leaves_no_partial_plan(tmp_path, monkeypatch):
    session, _artifact, _draft, version, snapshot = _database(tmp_path / "atomic.sqlite")
    run = ProcessingRun(
        artifact_id=version.draft.artifact_id,
        stage="queued",
        config_snapshot={
            "operation": "fill_plan",
            "case_key": "case-1",
            "template_version_id": version.id,
            "evidence_snapshot_ids": [snapshot.id],
            "agency_key": "test-agency",
        },
    )
    session.add(run)
    session.commit()

    def provider_failure(*args, **kwargs):
        raise RuntimeError("model schema rejected")

    monkeypatch.setattr(services, "build_model_fill_plan", provider_failure)
    services.run_fill_plan_job(session, run.id, FakeGateway())
    session.close()

    engine = create_engine(f"sqlite:///{tmp_path / 'atomic.sqlite'}")
    with Session(engine) as verify:
        failed = verify.get(ProcessingRun, run.id)
        assert failed.status == RunStatus.FAILED
        assert "model schema rejected" in failed.error
        assert failed.result is None
        assert verify.scalar(select(func.count()).select_from(FillPlan)) == 0
        assert verify.scalar(select(func.count()).select_from(VerificationReport)) == 0


def test_mapping_commits_plan_without_a_second_model_call(tmp_path, monkeypatch):
    session, _artifact, _draft, version, snapshot = _database(tmp_path / "success.sqlite")
    run = ProcessingRun(
        artifact_id=version.draft.artifact_id,
        stage="queued",
        config_snapshot={
            "operation": "fill_plan", "case_key": "case-1",
            "template_version_id": version.id,
            "evidence_snapshot_ids": [snapshot.id], "agency_key": "test-agency",
        },
    )
    session.add(run)
    session.commit()
    calls = []

    def valid_mock_mapping(*args, session, gateway, **kwargs):
        calls.append("mapping")
        _profile_hash, mapping_hash = services.fill_plan_profile_identity(session, "test-agency", gateway)
        return {
            "targets": [], "issues": [], "summary": {"issue_count": 0},
            "mapper_version": "model-mapper-v2", "mapping_profile_hash": mapping_hash,
        }

    monkeypatch.setattr(services, "build_model_fill_plan", valid_mock_mapping)
    services.run_fill_plan_job(session, run.id, FakeGateway())
    session.refresh(run)
    assert calls == ["mapping"]
    assert run.status == RunStatus.SUCCEEDED
    assert run.result and run.result["fill_plan_id"]
    assert session.scalar(select(func.count()).select_from(FillPlan)) == 1
    assert session.scalar(select(func.count()).select_from(VerificationReport)) == 0
    stored = session.scalar(select(FillPlan))
    revision = session.scalar(select(FillPlanRevision).where(FillPlanRevision.fill_plan_id == stored.id))
    assert revision.mapper_version == "model-mapper-v2"


def test_evidence_ingestion_defaults_to_model_and_marks_provider_failure(tmp_path, monkeypatch):
    session, _target, _draft, _version, _snapshot = _database(tmp_path / "evidence-failure.sqlite")
    source = Artifact(
        filename="evidence.pdf",
        media_type="application/pdf",
        kind=ArtifactKind.PDF,
        purpose=ArtifactPurpose.SOURCE,
        case_key="case-2",
        sha256="f" * 64,
        size_bytes=42,
        storage_key="evidence-object",
    )
    session.add(source)
    session.flush()
    run = ProcessingRun(artifact_id=source.id, config_snapshot={"allow_native_fallback": False})
    session.add(run)
    session.commit()

    class Storage:
        def open(self, _key):
            return BytesIO(b"document")

    class ParserAdapter:
        def __init__(self, *_args):
            pass

        def submit(self, _filename, _content):
            return "provider-job"

        def poll(self, _job_id):
            return SimpleNamespace(blocks=[{"text": "Applicant name: Example"}], parser_version="mock-v1", raw={})

    monkeypatch.setattr(
        services,
        "get_settings",
        lambda: SimpleNamespace(reducto_api_key="test-key", reducto_base_url="https://example.test"),
    )
    monkeypatch.setattr(services, "ReductoParserAdapter", ParserAdapter)
    calls = []

    def fail_model_extraction(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("structured extraction failed")

    monkeypatch.setattr(services, "extract_evidence_model", fail_model_extraction)
    with pytest.raises(RuntimeError, match="structured extraction failed"):
        services.ingest_evidence(session, Storage(), run.id)
    session.refresh(run)
    assert calls
    assert run.status == RunStatus.FAILED
    assert "structured extraction failed" in run.error
    assert session.scalar(select(func.count()).select_from(EvidenceSnapshot)) == 1  # fixture snapshot only

