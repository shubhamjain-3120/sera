from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    Artifact,
    ArtifactKind,
    ArtifactPurpose,
    EvidenceBundle,
    FillPlan,
    FillPlanRevision,
    ReviewDecision,
    TemplateDraft,
    TemplateVersion,
)
from app.review import apply_review_decision
from app.services import RevisionConflict, review_fill_plan, revise_fill_plan
from app.verification import EvidenceAwareVerifier, deterministic_validate


def candidate(candidate_id: str, value: object, field_id: str = "name") -> dict:
    return {
        "id": candidate_id,
        "target_field_id": field_id,
        "value": value,
        "origin": "evidence",
        "resolution": "direct",
        "selectable": True,
        "provenance": [{"kind": "text_span", "artifact_id": "source"}],
        "evidence_confidence": 0.9,
        "match_score": 0.9,
        "review_required": True,
        "uncertainty": [],
        "contradicts": [],
    }


def target(field_id: str = "name", value: object = "Amina", **field_updates: object) -> dict:
    item = candidate(f"candidate-{field_id}", value, field_id)
    field = {
        "id": field_id,
        "label": field_id,
        "field_type": "text",
        "required": True,
        "writable": True,
        "current_value": None,
    }
    field.update(field_updates)
    return {
        "field": field,
        "selected_candidate_id": item["id"],
        "candidates": [item],
        "issues": [],
        "state": "proposed",
    }


def payload(*targets: dict) -> dict:
    return {
        "targets": list(targets),
        "issues": [],
        "repeating_groups": [],
        "template_schema": {"inspection": {"format": "xlsx"}},
        "summary": {},
    }


def test_required_exception_needs_reason_and_records_human_authority():
    plan = payload(target())
    try:
        apply_review_decision(
            plan,
            decision_id="decision-1",
            target_field_id="name",
            action="intentional_blank",
            actor="reviewer@example.test",
            reason=None,
            candidate_id=None,
            value=None,
        )
    except ValueError as exc:
        assert "reason is required" in str(exc)
    else:
        raise AssertionError("required exception was accepted without a reason")

    revised, audit = apply_review_decision(
        plan,
        decision_id="decision-1",
        target_field_id="name",
        action="intentional_blank",
        actor="reviewer@example.test",
        reason="Source explicitly omits this required value",
        candidate_id=None,
        value=None,
    )
    reviewed = revised["targets"][0]
    assert reviewed["review"]["origin"] == "human"
    assert reviewed["review"]["required_exception"] is True
    assert reviewed["state"] == "reviewed"
    assert audit["new_value"] == ""
    assert deterministic_validate(revised)["status"] == "pass"


def test_prefilled_disposition_is_explicit():
    prefilled = target(current_value="Old Applicant")
    revised, audit = apply_review_decision(
        payload(prefilled),
        decision_id="decision-2",
        target_field_id="name",
        action="retain_prefilled",
        actor="reviewer",
        reason=None,
        candidate_id=None,
        value=None,
    )
    assert revised["targets"][0]["review"]["prefilled_disposition"] == "retain"
    assert audit["new_value"] == "Old Applicant"


def test_upstream_change_invalidates_nonhuman_dependent_and_notifies_human_dependent():
    upstream = target("base", 2, field_type="number")
    derived = candidate("derived", 4, "total")
    derived.update(
        origin="derivation",
        resolution="derived",
        derivation={"operation": "multiply", "input_ids": ["candidate-base"], "depth": 1},
    )
    dependent = target("total", 4, field_type="number")
    dependent["candidates"] = [derived]
    dependent["selected_candidate_id"] = "derived"
    plan = payload(upstream, dependent)
    revised, _ = apply_review_decision(
        plan,
        decision_id="decision-3",
        target_field_id="base",
        action="edit",
        actor="reviewer",
        reason=None,
        candidate_id=None,
        value=3,
    )
    assert revised["targets"][1]["selected_candidate_id"] is None
    assert revised["targets"][1]["state"] == "unresolved"
    assert any(item["code"] == "dependency_changed" for item in revised["targets"][1]["issues"])

    human_dependent, _ = apply_review_decision(
        plan,
        decision_id="decision-4",
        target_field_id="total",
        action="approve",
        actor="reviewer",
        reason=None,
        candidate_id=None,
        value=None,
    )
    revised, _ = apply_review_decision(
        human_dependent,
        decision_id="decision-5",
        target_field_id="base",
        action="edit",
        actor="reviewer",
        reason=None,
        candidate_id=None,
        value=3,
    )
    notice = revised["targets"][1]["review"]["dependency_notice"]
    assert notice["acknowledged"] is False
    assert deterministic_validate(revised)["status"] == "fail"


def make_persisted_plan(session: Session) -> FillPlan:
    artifact = Artifact(
        filename="target.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        kind=ArtifactKind.XLSX,
        purpose=ArtifactPurpose.TARGET,
        sha256="a" * 64,
        size_bytes=1,
        storage_key="target",
    )
    session.add(artifact)
    session.flush()
    schema = {"fields": [], "inspection": {"format": "xlsx"}}
    draft = TemplateDraft(artifact_id=artifact.id, revision=1, name="Target", schema=schema)
    session.add(draft)
    session.flush()
    version = TemplateVersion(
        draft_id=draft.id,
        version=1,
        name="Target",
        schema=schema,
        source_revision=1,
        schema_sha256="b" * 64,
    )
    bundle = EvidenceBundle(case_key="case", snapshot_ids=[], bundle_sha256="c" * 64)
    session.add_all([version, bundle])
    session.flush()
    plan = FillPlan(
        case_key="case", template_version_id=version.id, evidence_bundle_id=bundle.id
    )
    session.add(plan)
    session.flush()
    revision = FillPlanRevision(
        fill_plan_id=plan.id,
        revision=1,
        mapper_version="test",
        payload_sha256="d" * 64,
        payload=payload(target()),
    )
    session.add(revision)
    session.commit()
    return plan


def test_decisions_persist_exact_revisions_and_stale_edits_fail():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    plan = make_persisted_plan(session)
    decision = review_fill_plan(
        session,
        plan,
        expected_revision=1,
        target_field_id="name",
        action="approve",
        actor="reviewer",
        reason=None,
        candidate_id=None,
        value=None,
    )
    assert plan.current_revision == 2
    assert decision.source_revision_id != decision.resulting_revision_id
    assert session.scalar(select(ReviewDecision)).id == decision.id
    try:
        review_fill_plan(
            session,
            plan,
            expected_revision=1,
            target_field_id="name",
            action="approve",
            actor="late reviewer",
            reason=None,
            candidate_id=None,
            value=None,
        )
    except RevisionConflict:
        pass
    else:
        raise AssertionError("stale review was accepted")

    try:
        revise_fill_plan(session, plan, 2, {"name": None}, [])
    except ValueError as exc:
        assert "terminal authority" in str(exc)
    else:
        raise AssertionError("mapper overwrote terminal human authority")


def test_independent_verifier_skips_human_decisions():
    reviewed, _ = apply_review_decision(
        payload(target()),
        decision_id="decision-6",
        target_field_id="name",
        action="edit",
        actor="reviewer",
        reason=None,
        candidate_id=None,
        value="Reviewer Value",
    )
    result = EvidenceAwareVerifier().verify(
        reviewed,
        [{"facts": [{"id": "fact", "key": "name", "value": "Different", "accepted": True}]}],
    )
    assert result["findings"] == []
