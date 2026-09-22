from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Artifact, ArtifactKind, TemplateDraft
from app.schemas import TemplateSchema
from app.services import RevisionConflict, publish_draft, update_draft


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
