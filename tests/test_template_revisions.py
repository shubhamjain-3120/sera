from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Artifact, ArtifactKind, TemplateDraft
from app.schemas import TemplateSchema
from app.services import (
    RevisionConflict,
    _reconcile_inspection_annotations,
    publish_draft,
    update_draft,
)


def session_with_draft() -> tuple[Session, TemplateDraft]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    artifact = Artifact(filename="target.pdf", media_type="application/pdf", kind=ArtifactKind.PDF, sha256="a" * 64, size_bytes=10, storage_key="original")
    session.add(artifact)
    session.flush()
    draft = TemplateDraft(artifact_id=artifact.id, name="Target", schema={"fields": [], "repeating_groups": [], "inspection": {}})
    session.add(draft)
    session.commit()
    return session, draft


def test_update_requires_expected_revision_and_publish_is_immutable():
    session, draft = session_with_draft()
    updated = update_draft(session, draft, 1, TemplateSchema(), "Renamed")
    assert updated.revision == 2
    version = publish_draft(session, updated, 2)
    assert version.version == 1
    assert version.source_revision == 2
    assert len(version.schema_sha256) == 64
    try:
        update_draft(session, draft, 1, TemplateSchema(), None)
    except RevisionConflict:
        pass
    else:
        raise AssertionError("Expected stale update to fail")


def test_reinspection_reconciles_only_human_overrides_by_legacy_field_id():
    old = {
        "fields": [
            {
                "id": "Col4.16.1",
                "label": "My custom label",
                "label_origin": "human",
                "semantic_type": "agency.custom_percentage",
                "semantic_type_origin": "human",
                "notes": "Reviewer note",
            },
            {
                "id": "Coverage_GL_YesNo",
                "label": "Refrigeration Breakdown",
                "label_origin": "layout",
                "semantic_type": "coverage.refrigeration_breakdown",
                "semantic_type_origin": "rule",
            },
        ]
    }
    inspected = {
        "fields": [
            {
                "id": "Col4.16.1",
                "label": "Towing Services %",
                "label_origin": "layout",
                "semantic_type": "template.towing_services_percentage",
                "semantic_type_origin": "rule",
            },
            {
                "id": "Coverage_GL_YesNo",
                "label": "General Liability",
                "label_origin": "layout",
                "semantic_type": "coverage.general_liability",
                "semantic_type_origin": "rule",
            },
        ]
    }
    result = _reconcile_inspection_annotations(old, inspected)
    fields = {field["id"]: field for field in result["fields"]}
    assert fields["Col4.16.1"]["label"] == "My custom label"
    assert fields["Col4.16.1"]["semantic_type"] == "agency.custom_percentage"
    assert fields["Col4.16.1"]["notes"] == "Reviewer note"
    assert fields["Coverage_GL_YesNo"]["label"] == "General Liability"
    assert fields["Coverage_GL_YesNo"]["semantic_type"] == "coverage.general_liability"
