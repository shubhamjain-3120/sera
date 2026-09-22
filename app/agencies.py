"""Filing agencies and the trusted facts their stored details contribute."""

from typing import Any

AGENCY_SOURCE_VERSION = "agency-registry-v1"
DEFAULT_AGENCY_KEY = "js-truck"

# Detail keys map to the semantic fact keys used by the mapper, so an agency
# address can never score against an applicant or driver address field.
DETAIL_FACT_KEYS: dict[str, str] = {
    "legal_name": "agency.legal_name",
    "address_line1": "agency.address_line1",
    "address_line2": "agency.address_line2",
    "city": "agency.city",
    "state": "agency.state",
    "postal_code": "agency.postal_code",
    "phone": "agency.phone",
    "fax": "agency.fax",
    "email": "agency.email",
    "website": "agency.website",
    "producer_number": "agency.producer_number",
    "license_number": "agency.license_number",
    "naic_code": "agency.naic_code",
    "contact_name": "agency.contact_name",
}

DETAIL_LABELS: dict[str, str] = {
    "legal_name": "Agency legal name",
    "address_line1": "Agency address",
    "address_line2": "Agency address line 2",
    "city": "Agency city",
    "state": "Agency state",
    "postal_code": "Agency ZIP code",
    "phone": "Agency phone",
    "fax": "Agency fax",
    "email": "Agency email",
    "website": "Agency website",
    "producer_number": "Producer number",
    "license_number": "Agency license number",
    "naic_code": "Agency NAIC code",
    "contact_name": "Agency contact",
}

SEED_AGENCIES: list[dict[str, Any]] = [
    {
        "key": DEFAULT_AGENCY_KEY,
        "name": "JS Truck Insurance",
        "is_default": True,
        "details": {key: "" for key in DETAIL_FACT_KEYS} | {"legal_name": "JS Truck Insurance"},
    },
    {
        "key": "jssr",
        "name": "JSSR",
        "is_default": False,
        "details": {key: "" for key in DETAIL_FACT_KEYS} | {"legal_name": "JSSR"},
    },
]


def agency_facts(agency: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn stored agency details into facts the mapper can score and cite.

    These are trusted registry values rather than document extractions, so they
    carry a registry locator instead of a page rectangle and are never mixed
    into a case's frozen evidence bundle.
    """
    facts: list[dict[str, Any]] = []
    details = agency.get("details") or {}
    for detail_key, fact_key in DETAIL_FACT_KEYS.items():
        value = details.get(detail_key)
        if value is None or str(value).strip() == "":
            continue
        facts.append(
            {
                "id": f"agency:{agency['key']}:{detail_key}",
                "key": fact_key,
                "label": DETAIL_LABELS.get(detail_key, detail_key.replace("_", " ")),
                "value": value,
                "raw_value": str(value),
                "value_type": "text",
                "entity_id": f"agency:{agency['key']}",
                "entity_role": "agency",
                "confidence": 1.0,
                "accepted": True,
                "uncertainty": [],
                "contradicts": [],
                "semantics": ["agency_registry"],
                "provenance": [
                    {
                        "kind": "agency_registry",
                        "agency_key": agency["key"],
                        "agency_name": agency.get("name"),
                        "detail_key": detail_key,
                        "source_version": AGENCY_SOURCE_VERSION,
                    }
                ],
            }
        )
    return facts
