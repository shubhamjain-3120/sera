import copy

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    Artifact,
    ArtifactKind,
    ArtifactPurpose,
    EvidenceBundle,
    EvidenceSnapshot,
    FillPlan,
    FillPlanRevision,
    ProcessingRun,
    TemplateDraft,
    TemplateVersion,
)
from app.services import verify_fill_plan_revision
from app.verification import build_verification_report, deterministic_validate


def evidence_fact(
    fact_id: str,
    key: str,
    value: object,
    *,
    role: str = "applicant",
    period: str | None = None,
) -> dict:
    return {
        "id": fact_id,
        "key": key,
        "label": key.split(".")[-1],
        "value": value,
        "raw_value": str(value),
        "entity_role": role,
        "entity_id": f"{role}-1",
        "accepted": True,
        "confidence": 0.98,
        "period_context": period,
        "provenance": [{"kind": "pdf_rect", "artifact_id": "source-1", "page": 1, "rect": [1, 2, 3, 4]}],
    }


def target(
    field_id: str,
    semantic: str,
    candidate: dict | None,
    *,
    required: bool = True,
    constraints: dict | None = None,
) -> dict:
    selected_id = candidate.get("id") if candidate else None
    return {
        "field": {
            "id": field_id,
            "label": semantic.replace(".", " "),
            "semantic_type": semantic,
            "field_type": "text",
            "required": required,
            "writable": True,
            "constraints": constraints or {},
        },
        "selected_candidate_id": selected_id,
        "candidates": [candidate] if candidate else [],
        "state": "proposed" if candidate else "unresolved",
    }


def candidate(fact: dict, field_id: str, **updates: object) -> dict:
    result = {
        "id": f"candidate-{fact['id']}",
        "target_field_id": field_id,
        "fact_id": fact["id"],
        "value": fact["value"],
        "origin": "evidence",
        "resolution": "direct",
        "entity_role": fact["entity_role"],
        "period_context": fact.get("period_context"),
        "provenance": fact["provenance"],
        "review_required": True,
    }
    result.update(updates)
    return result


def payload(*targets: dict) -> dict:
    return {"targets": list(targets), "repeating_groups": [], "issues": []}


def test_valid_proposal_passes_both_stages_without_mutation():
    fact = evidence_fact("name-1", "applicant.legal_name", "Amina Doe")
    plan = payload(target("name", "applicant.legal_name", candidate(fact, "name")))
    original = copy.deepcopy(plan)

    report = build_verification_report(plan, [{"facts": [fact]}])

    assert report["status"] == "pass"
    assert report["deterministic"]["status"] == "pass"
    assert report["independent"]["status"] == "pass"
    assert report["selection_unchanged"] is True
    assert plan == original


def test_incorrect_entity_is_detected_by_independent_verifier():
    fact = evidence_fact("driver-1", "applicant.legal_name", "Wrong Person", role="driver")
    plan = payload(target("name", "applicant.legal_name", candidate(fact, "name")))

    report = build_verification_report(plan, [{"facts": [fact]}])

    assert report["status"] == "fail"
    assert any(item["code"] == "incorrect_entity" for item in report["independent"]["findings"])


def test_reporting_period_mismatch_is_deterministically_blocked():
    fact = evidence_fact("revenue-1", "business.revenue", "100", role="business", period="2025")
    plan = payload(
        target(
            "revenue",
            "business.revenue",
            candidate(fact, "revenue"),
            constraints={"reporting_period": "2026"},
        )
    )

    result = deterministic_validate(plan)

    assert result["status"] == "fail"
    assert any(item["code"] == "reporting_period_mismatch" for item in result["findings"])


def test_unsupported_derivation_is_deterministically_blocked():
    derived = {
        "id": "d3",
        "target_field_id": "total",
        "fact_id": None,
        "value": 42,
        "origin": "derivation",
        "resolution": "derived",
        "provenance": [],
        "review_required": True,
        "derivation": {"depth": 3, "input_ids": ["d2"], "operation": "sum"},
    }
    plan = payload(target("total", "business.total", derived))
    plan["targets"][0]["field"]["field_type"] = "number"

    result = deterministic_validate(plan)

    assert result["status"] == "fail"
    assert any(item["code"] == "unsupported_derivation" for item in result["findings"])


def test_missed_fact_is_reported_as_additional_evidence_without_selection():
    fact = evidence_fact("phone-1", "applicant.phone", "555-0100")
    plan = payload(target("phone", "applicant.phone", None, required=False))

    report = build_verification_report(plan, [{"facts": [fact]}])

    assert report["status"] == "needs_review"
    assert report["independent"]["additional_evidence"][0]["snapshot_fact_id"] == "phone-1"
    assert plan["targets"][0]["selected_candidate_id"] is None


def test_persisted_report_is_revision_pinned_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    fact = evidence_fact("name-1", "applicant.legal_name", "Amina Doe")
    source = Artifact(filename="source.pdf", media_type="application/pdf", kind=ArtifactKind.PDF, purpose=ArtifactPurpose.SOURCE, case_key="case-1", sha256="a" * 64, size_bytes=1, storage_key="source")
    target_artifact = Artifact(filename="target.pdf", media_type="application/pdf", kind=ArtifactKind.PDF, purpose=ArtifactPurpose.TARGET, sha256="b" * 64, size_bytes=1, storage_key="target")
    session.add_all([source, target_artifact])
    session.flush()
    run = ProcessingRun(artifact_id=source.id)
    draft = TemplateDraft(artifact_id=target_artifact.id, revision=1, name="Target", schema={})
    session.add_all([run, draft])
    session.flush()
    snapshot = EvidenceSnapshot(artifact_id=source.id, run_id=run.id, parser_provider="test", parser_version="v1", extractor_version="v1", snapshot_sha256="c" * 64, snapshot={"facts": [fact]})
    version = TemplateVersion(draft_id=draft.id, version=1, name="Target", schema={}, source_revision=1, schema_sha256="d" * 64)
    session.add_all([snapshot, version])
    session.flush()
    bundle = EvidenceBundle(case_key="case-1", snapshot_ids=[snapshot.id], bundle_sha256="e" * 64)
    session.add(bundle)
    session.flush()
    plan = FillPlan(case_key="case-1", template_version_id=version.id, evidence_bundle_id=bundle.id, current_revision=1)
    session.add(plan)
    session.flush()
    plan_payload = payload(target("name", "applicant.legal_name", candidate(fact, "name")))
    revision = FillPlanRevision(fill_plan_id=plan.id, revision=1, mapper_version="test", payload_sha256="f" * 64, payload=plan_payload)
    session.add(revision)
    session.commit()

    first = verify_fill_plan_revision(session, plan)
    second = verify_fill_plan_revision(session, plan)

    assert first.id == second.id
    assert first.fill_plan_revision_id == revision.id
    assert first.status == "pass"
    assert first.report["trace"]["fill_plan_revision_sha256"] == revision.payload_sha256
