from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.agencies import DEFAULT_AGENCY_KEY, agency_facts
from app.db import Base
from app.mapping import build_fill_plan
from app.models import Agency, Artifact, ArtifactKind, ArtifactPurpose
from app.services import new_case_key, resolve_agency, seed_agencies
from app.verification import deterministic_validate
from tests.test_fill_plans import fact, field


def agency(**details: str) -> dict:
    return {"key": "js-truck", "name": "JS Truck Insurance", "details": details}


def target(payload: dict, field_id: str) -> dict:
    return next(item for item in payload["targets"] if item["field"]["id"] == field_id)


def selected(payload: dict, field_id: str) -> dict | None:
    item = target(payload, field_id)
    return next(
        (candidate for candidate in item["candidates"] if candidate["id"] == item["selected_candidate_id"]),
        None,
    )


def test_seeding_is_idempotent_and_keeps_edited_details():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    seed_agencies(session)
    stored = session.scalar(select(Agency).where(Agency.key == DEFAULT_AGENCY_KEY))
    stored.details = {**stored.details, "phone": "555-0100"}
    session.commit()

    seed_agencies(session)

    assert len(list(session.scalars(select(Agency)).all())) == 2
    assert resolve_agency(session, None).key == DEFAULT_AGENCY_KEY
    assert resolve_agency(session, None).details["phone"] == "555-0100"
    assert resolve_agency(session, "jssr").name == "JSSR"


def test_derived_case_keys_do_not_collide_with_existing_batches():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)

    first = new_case_key(session)
    session.add(
        Artifact(
            filename="source.pdf",
            media_type="application/pdf",
            kind=ArtifactKind.PDF,
            purpose=ArtifactPurpose.SOURCE,
            case_key=first,
            sha256="a" * 64,
            size_bytes=1,
            storage_key="originals/aa/a.pdf",
        )
    )
    session.commit()

    assert new_case_key(session) != first


def test_blank_details_do_not_become_facts():
    facts = agency_facts(agency(legal_name="JS Truck Insurance", phone="", city="   "))
    assert [item["key"] for item in facts] == ["agency.legal_name"]
    assert facts[0]["entity_role"] == "agency"
    assert facts[0]["provenance"][0]["kind"] == "agency_registry"


def test_agency_details_fill_agency_fields_without_touching_applicant_fields():
    payload = build_fill_plan(
        {
            "fields": [
                field("agency-phone", "agency.phone"),
                field("applicant-phone", "applicant.phone"),
            ]
        },
        [("snap-1", {"facts": [fact("f-1", "applicant.phone", "555-0199")]})],
        None,
        agency_facts(agency(phone="555-0100")),
    )

    agency_pick = selected(payload, "agency-phone")
    applicant_pick = selected(payload, "applicant-phone")
    assert agency_pick["value"] == "555-0100"
    assert agency_pick["origin"] == "agency"
    # The applicant's own number must never be displaced by the filing agency's.
    assert applicant_pick["value"] == "555-0199"
    assert applicant_pick["origin"] == "evidence"
    assert deterministic_validate(payload)["status"] == "pass"


def test_agency_facts_are_not_offered_to_unrelated_targets():
    payload = build_fill_plan(
        {"fields": [field("driver-license", "driver.license_number")]},
        [("snap-1", {"facts": []})],
        None,
        agency_facts(agency(license_number="AG-4471")),
    )
    assert target(payload, "driver-license")["candidates"] == []
