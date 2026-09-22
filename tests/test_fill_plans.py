from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.mapping import apply_revision, build_fill_plan, evaluate_derivations
from app.models import (
    Artifact,
    ArtifactKind,
    ArtifactPurpose,
    EvidenceSnapshot,
    FillPlanRevision,
    ProcessingRun,
    TemplateDraft,
    TemplateVersion,
)
from app.services import RevisionConflict, create_fill_plan, revise_fill_plan


def fact(
    fact_id: str,
    key: str,
    value: object,
    *,
    entity_id: str = "applicant-1",
    entity_role: str = "applicant",
    accepted: bool = True,
    confidence: float = 0.96,
    contradicts: list[str] | None = None,
) -> dict:
    return {
        "id": fact_id,
        "key": key,
        "label": key.split(".")[-1].replace("_", " "),
        "value": value,
        "raw_value": str(value),
        "value_type": "text",
        "entity_id": entity_id,
        "entity_role": entity_role,
        "accepted": accepted,
        "confidence": confidence,
        "uncertainty": [],
        "contradicts": contradicts or [],
        "provenance": [
            {
                "kind": "pdf_rect",
                "artifact_id": "source-1",
                "page": 1,
                "rect": [10, 20, 30, 40],
                "coordinate_system": "reducto-normalized-top-left",
            }
        ],
    }


def field(field_id: str, semantic: str, **updates: object) -> dict:
    result = {
        "id": field_id,
        "label": semantic.split(".")[-1].replace("_", " "),
        "semantic_type": semantic,
        "field_type": "text",
        "required": True,
        "writable": True,
        "options": [],
        "current_value": None,
        "location": {"kind": "xlsx_range", "sheet": "Application", "cell_range": "B2"},
    }
    result.update(updates)
    return result


def test_direct_and_normalized_candidates_retain_exact_provenance():
    schema = {
        "fields": [
            field("name", "person.given_name"),
            field("active", "applicant.active", field_type="boolean", required=False),
        ],
        "repeating_groups": [],
        "inspection": {"format": "xlsx"},
    }
    snapshot = {
        "facts": [
            fact("f-name", "person.given_name", "Amina"),
            fact("f-active", "applicant.active", "Yes"),
        ]
    }
    payload = build_fill_plan(schema, [("snapshot-1", snapshot)])

    name = payload["targets"][0]
    active = payload["targets"][1]
    assert name["selected_candidate_id"] == name["candidates"][0]["id"]
    assert name["candidates"][0]["provenance"][0]["rect"] == [10, 20, 30, 40]
    assert name["candidates"][0]["evidence_confidence"] == 0.96
    assert active["candidates"][0]["value"] is True
    assert active["candidates"][0]["resolution"] == "normalized"


def test_conflicting_values_are_alternatives_and_never_silently_selected():
    schema = {"fields": [field("name", "person.given_name")], "repeating_groups": []}
    snapshot = {
        "facts": [
            fact("f-1", "person.given_name", "Amina", contradicts=["f-2"]),
            fact("f-2", "person.given_name", "Amira", contradicts=["f-1"]),
        ]
    }
    payload = build_fill_plan(schema, [("snapshot-1", snapshot)])

    target = payload["targets"][0]
    assert target["selected_candidate_id"] is None
    assert all(not candidate["selectable"] for candidate in target["candidates"])
    assert target["state"] == "unresolved"


def test_number_normalization_does_not_correct_embedded_text():
    schema = {
        "fields": [field("revenue", "business.revenue", field_type="number")],
        "repeating_groups": [],
    }
    snapshot = {"facts": [fact("f-1", "business.revenue", "about 123 dollars")]}
    payload = build_fill_plan(schema, [("snapshot-1", snapshot)])

    assert payload["targets"][0]["candidates"] == []
    assert payload["targets"][0]["selected_candidate_id"] is None


def test_repeating_overflow_retains_entities_and_blocks():
    repeated = field("driver-name", "driver.given_name", required=False)
    schema = {
        "fields": [repeated],
        "repeating_groups": [
            {"id": "drivers", "label": "Drivers", "field_ids": ["driver-name"], "capacity": 1}
        ],
    }
    snapshot = {
        "facts": [
            fact("d-1", "driver.given_name", "One", entity_id="driver-1", entity_role="driver"),
            fact("d-2", "driver.given_name", "Two", entity_id="driver-2", entity_role="driver"),
        ]
    }
    payload = build_fill_plan(schema, [("snapshot-1", snapshot)])

    group = payload["repeating_groups"][0]
    assert group["entity_ids"] == ["driver-1", "driver-2"]
    assert group["overflow_entity_ids"] == ["driver-2"]
    assert payload["summary"]["blocker_count"] == 1


def test_derivations_allow_depth_two_and_reject_depth_three_and_cycles():
    base = {
        "a": {"value": "2", "provenance": [{"kind": "text_span"}], "evidence_confidence": 0.9},
        "b": {"value": "3", "provenance": [{"kind": "text_span"}], "evidence_confidence": 0.8},
    }
    valid, issues = evaluate_derivations(
        [
            {"id": "d1", "target_field_id": "total", "operation": "sum", "input_ids": ["a", "b"], "separator": " ", "as_of_date": None},
            {"id": "d2", "target_field_id": "double", "operation": "multiply", "input_ids": ["d1", "a"], "separator": " ", "as_of_date": None},
        ],
        base,
    )
    assert [item["value"] for item in valid] == [5, 10]
    assert not issues

    rejected, issues = evaluate_derivations(
        [
            {"id": "d1", "target_field_id": "one", "operation": "sum", "input_ids": ["a"], "separator": " ", "as_of_date": None},
            {"id": "d2", "target_field_id": "two", "operation": "sum", "input_ids": ["d1"], "separator": " ", "as_of_date": None},
            {"id": "d3", "target_field_id": "three", "operation": "sum", "input_ids": ["d2"], "separator": " ", "as_of_date": None},
        ],
        base,
    )
    assert all(item["id"] != "d3" for item in rejected)
    assert any(issue["code"] == "derivation_depth" for issue in issues)

    rejected, issues = evaluate_derivations(
        [
            {"id": "x", "target_field_id": "one", "operation": "sum", "input_ids": ["y"], "separator": " ", "as_of_date": None},
            {"id": "y", "target_field_id": "two", "operation": "sum", "input_ids": ["x"], "separator": " ", "as_of_date": None},
        ],
        base,
    )
    assert not rejected
    assert all(issue["code"] == "derivation_cycle" for issue in issues)


def test_uncertain_candidate_cannot_be_selected_in_a_new_revision():
    schema = {"fields": [field("name", "person.given_name")], "repeating_groups": []}
    snapshot = {"facts": [fact("f-1", "person.given_name", "Amina", accepted=False)]}
    payload = build_fill_plan(schema, [("snapshot-1", snapshot)])
    candidate_id = payload["targets"][0]["candidates"][0]["id"]

    try:
        apply_revision(payload, {"name": candidate_id}, [])
    except ValueError as exc:
        assert "cannot be selected silently" in str(exc)
    else:
        raise AssertionError("uncertain candidate was selected")


def test_case_outputs_share_frozen_evidence_and_revisions_are_immutable():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = Artifact(
        filename="source.pdf",
        media_type="application/pdf",
        kind=ArtifactKind.PDF,
        purpose=ArtifactPurpose.SOURCE,
        case_key="case-2",
        sha256="a" * 64,
        size_bytes=10,
        storage_key="sha256/aa/source.pdf",
    )
    session.add(source)
    session.flush()
    run = ProcessingRun(artifact_id=source.id)
    session.add(run)
    session.flush()
    snapshot = EvidenceSnapshot(
        artifact_id=source.id,
        run_id=run.id,
        parser_provider="reducto",
        parser_version="reducto-v1",
        extractor_version="rules-evidence-v1",
        snapshot_sha256="b" * 64,
        snapshot={"facts": [fact("f-name", "person.given_name", "Amina")]},
    )
    session.add(snapshot)
    versions = []
    for index, kind in enumerate((ArtifactKind.PDF, ArtifactKind.XLSX), start=1):
        target = Artifact(
            filename=f"target-{index}.{kind.value}",
            media_type="application/octet-stream",
            kind=kind,
            purpose=ArtifactPurpose.TARGET,
            sha256=str(index) * 64,
            size_bytes=20,
            storage_key=f"sha256/{index}/target",
        )
        session.add(target)
        session.flush()
        schema = {"fields": [field("name", "person.given_name")], "repeating_groups": [], "inspection": {"format": kind.value}}
        draft = TemplateDraft(artifact_id=target.id, revision=1, name=f"Target {index}", schema=schema)
        session.add(draft)
        session.flush()
        version = TemplateVersion(
            draft_id=draft.id,
            version=1,
            name=draft.name,
            schema=schema,
            source_revision=1,
            schema_sha256=str(index + 2) * 64,
        )
        session.add(version)
        versions.append(version)
    session.commit()

    first = create_fill_plan(session, "case-2", versions[0])
    second = create_fill_plan(session, "case-2", versions[1])
    assert first.evidence_bundle_id == second.evidence_bundle_id
    original_revision = session.scalar(
        select(FillPlanRevision).where(FillPlanRevision.fill_plan_id == first.id)
    )
    assert original_revision is not None
    selected_id = original_revision.payload["targets"][0]["selected_candidate_id"]
    next_revision = revise_fill_plan(session, first, 1, {"name": selected_id}, [])
    assert next_revision.revision == 2
    assert original_revision.revision == 1
    assert original_revision.payload_sha256 != next_revision.payload_sha256 or original_revision.payload == next_revision.payload
    try:
        revise_fill_plan(session, first, 1, {}, [])
    except RevisionConflict:
        pass
    else:
        raise AssertionError("stale Fill Plan edit overwrote a newer revision")
