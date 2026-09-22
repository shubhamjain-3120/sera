from types import SimpleNamespace

from app.mapping import ModelFillPlanOutput, ModelMappingProposal, build_model_fill_plan
from app.model_gateway import ModelResult


def field(field_id, label, semantic, kind="text", **extra):
    return {
        "id": field_id, "label": label, "semantic_type": semantic,
        "field_type": kind, "required": False, "writable": True,
        "options": [], "choice_options": [], "constraints": {},
        "native_full_name": field_id, "location": {"kind": "pdf_rect", "page": 1, "rect": [1, 2, 3, 4]},
        "current_value": None, **extra,
    }


def fact(fact_id, key, value, *, label=None, kind="text", role="business", confidence=0.99, **extra):
    return {
        "id": fact_id, "key": key, "label": label or key.split(".")[-1].replace("_", " "),
        "value": value, "raw_value": str(value), "value_type": kind,
        "entity_id": f"{role}-1", "entity_role": role, "accepted": True,
        "confidence": confidence, "uncertainty": [], "contradicts": [],
        "provenance": [{"artifact_id": "source-1", "kind": "pdf_rect", "page": 2, "rect": [0.1, 0.2, 0.3, 0.4]}],
        **extra,
    }


class FakeGateway:
    settings = SimpleNamespace(openai_reasoning_effort="high")

    def __init__(self, output):
        self.output = output
        self.input = None
        self.calls = 0

    def model_for(self, stage):
        assert stage == "mapping"
        return "gpt-6-luna"

    def run(self, stage, input_data, _output_model, **kwargs):
        assert stage == "mapping"
        self.calls += 1
        self.input = input_data
        kwargs["validate_output"](self.output)
        return ModelResult(self.output, "mapping-trace", "gpt-6-luna", "shortlist-links-v2", "target-fact-links-v2")


def proposal(target_id, *fact_ids, transformation=None):
    return ModelMappingProposal(
        target_field_id=target_id, evidence_fact_ids=list(fact_ids),
        transformation=transformation, rationale="Supported by the located source fact.",
    )


def run(fields, facts, proposals):
    gateway = FakeGateway(ModelFillPlanOutput(proposals=proposals))
    result = build_model_fill_plan(
        {"fields": fields, "repeating_groups": [], "inspection": {"format": "pdf"}},
        [("snapshot-1", {"facts": facts})], session=None, run_id="run-1", gateway=gateway,
    )
    return result, gateway


def by_id(result):
    return {target["field"]["id"]: target for target in result["targets"]}


def test_ein_fein_business_name_and_revenue_aliases_enter_shortlist_and_write_server_values():
    ein = field("ein", "FEIN", "business.tax_id")
    name = field("business_name", "Name of Business / Named Insured", "business.legal_name")
    revenue = field("revenue", "Gross Annual Revenue", "business.revenue", "number")
    f_ein = fact("f-ein", "business.ein", "12-3456789", label="Employer Identification Number")
    f_name = fact("f-name", "business.company_name", "Rivington Transport LLC", label="Company Name")
    f_revenue = fact("f-revenue", "business.gross_sales", "$1,250,000", kind="currency")

    result, gateway = run([ein, name, revenue], [f_ein, f_name, f_revenue], [
        proposal("ein", "f-ein"), proposal("business_name", "f-name"), proposal("revenue", "f-revenue"),
    ])

    targets = by_id(result)
    assert targets["ein"]["candidates"][0]["value"] == "12-3456789"
    assert targets["business_name"]["candidates"][0]["value"] == "Rivington Transport LLC"
    assert targets["revenue"]["candidates"][0]["value"] == 1250000
    assert "f-ein" in {fact["id"] for fact in gateway.input["facts"]}
    assert len(gateway.input) == 2 and "published_template_schema" not in gateway.input
    assert gateway.calls == 1


def test_revenue_without_reporting_period_is_a_review_suggestion():
    revenue = field("revenue", "Revenue Current Year Estimated", "template.revenue_current_year_estimated", "text")
    sales = fact("f-sales", "business.gross_sale", "60000", kind="currency")
    result, _gateway = run([revenue], [sales], [proposal("revenue", "f-sales")])
    candidate = by_id(result)["revenue"]["candidates"][0]
    assert candidate["value"] == "60000"
    assert candidate["review_required"] is True
    assert any("reporting period" in reason for reason in candidate["auto_approval_reasons"])


def test_shortlist_is_bounded_and_server_preserves_an_exact_shortlist_match():
    target = field("target", "FEIN", "business.tax_id")
    unrelated = field("unrelated", "Building construction year", "property.year_built", "number")
    f_ein = fact("f-ein", "business.ein", "12-3456789")
    f_year = fact("f-year", "property.year_built", 1984, kind="number")
    result, gateway = run([target, unrelated], [f_ein, f_year], [proposal("target", "f-ein")])
    assert [row["target"]["id"] for row in gateway.input["targets"]] == ["target", "unrelated"]
    assert {row["id"] for row in gateway.input["facts"]} == {"f-ein", "f-year"}
    assert "value" not in ModelMappingProposal.model_fields
    exact = by_id(result)["unrelated"]["candidates"][0]
    assert exact["value"] == 1984
    assert exact["review_required"] is True


def test_split_address_and_record_count_are_recomputed_on_server():
    street = field("street", "Street Address", "business.address.street")
    city = field("city", "City", "business.address.city")
    state = field("state", "State", "business.address.state")
    postal = field("zip", "ZIP Code", "business.address.postal_code")
    driver_count = field("drivers", "Number of Drivers", "driver.count", "number")
    address = fact("f-address", "business.mailing_address", "10 Main St, Boston, MA 02110", label="Business Mailing Address")
    driver1 = fact("d1", "driver.name", "Amina", role="driver", entity_id="driver-1")
    driver2 = fact("d2", "driver.license", "D222", role="driver", entity_id="driver-2")
    result, _gateway = run([street, city, state, postal, driver_count], [address, driver1, driver2], [
        proposal("street", "f-address", transformation={"operation": "address_component", "component": "street"}),
        proposal("city", "f-address", transformation={"operation": "address_component", "component": "city"}),
        proposal("state", "f-address", transformation={"operation": "address_component", "component": "state"}),
        proposal("zip", "f-address", transformation={"operation": "address_component", "component": "postal_code"}),
        proposal("drivers", "d1", "d2", transformation={"operation": "count_records"}),
    ])
    targets = by_id(result)
    assert [targets[key]["candidates"][0]["value"] for key in ("street", "city", "state", "zip")] == ["10 Main St", "Boston", "MA", "02110"]
    assert targets["drivers"]["candidates"][0]["value"] == 2
    assert all(targets[key]["candidates"][0]["write_value"] == targets[key]["candidates"][0]["value"] for key in ("street", "city", "state", "zip", "drivers"))


def test_unproposed_driver_count_is_a_review_suggestion_from_stable_person_facts():
    count = field("drivers", "No. of Drivers", "template.no_drivers", "number", native_name="Rivington_NoDrivers")
    name = fact("f-person", "person.full_name", "Amina", role="applicant")
    license_number = fact("f-license", "driver.license_number", "D123", role="applicant")
    result, gateway = run([count], [name, license_number], [])
    candidate = by_id(result)["drivers"]["candidates"][0]
    assert candidate["value"] == 1
    assert candidate["review_required"] is True
    assert candidate["write_value"] == 1
    assert gateway.calls == 1


def test_coverage_buttons_and_choice_export_are_derived_from_native_options():
    limit = field("limit", "Auto Liability Limit", "coverage.auto_liability_limit", "choice",
        options=["300,000"], choice_options=[{"display_value": "300,000", "export_value": "300000"}],
        constraints={"choice_options": [{"display_value": "300,000", "export_value": "300000"}]})
    cargo = field("cargo", "Cargo Coverage", "coverage.cargo", "boolean", native_name="Coverage_CARGO_YesNo", constraints={"button_states": ["/Off", "/Yes"]})
    gl = field("gl", "General Liability", "coverage.general_liability", "boolean", native_name="Coverage_GL_YesNo", constraints={"button_states": ["/Off", "/Yes"]})
    f_limit = fact("f-limit", "coverage.auto_liability_limit", "300,000")
    f_cargo = fact("f-cargo", "coverage.motor_cargo_limit", "100,000", kind="currency")
    result, gateway = run([limit, cargo, gl], [f_limit, f_cargo], [])
    targets = by_id(result)
    assert targets["limit"]["candidates"][0]["write_value"] == "300000"
    assert targets["cargo"]["candidates"][0]["value"] is True
    assert targets["cargo"]["candidates"][0]["write_value"] == "/Yes"
    assert targets["limit"]["candidates"][0]["write_value"] == "300000"
    assert targets["limit"]["candidates"][0]["review_required"] is True
    assert targets["cargo"]["candidates"][0]["review_required"] is True
    assert gateway.input["targets"]
    assert by_id(result)["gl"]["candidates"] == []
    assert all(target["target"]["id"] != "gl" for target in gateway.input["targets"])


def test_conflicting_located_name_facts_become_reviewable_alternatives():
    insured = field("insured", "Insured Name", "applicant.legal_name")
    company = fact("f-company", "company.name", "Example Towing LLC")
    person = fact("f-person", "applicant.name", "Amina Doe", label="Applicant Name", role="applicant")
    result, _gateway = run([insured], [company, person], [proposal("insured", "f-company", "f-person")])
    target = by_id(result)["insured"]
    assert len(target["candidates"]) == 2
    assert target["selected_candidate_id"] == target["candidates"][0]["id"]
    assert all(candidate["review_required"] for candidate in target["candidates"])
    assert {candidate["value"] for candidate in target["candidates"]} == {"Example Towing LLC", "Amina Doe"}


def test_located_low_confidence_and_conflicting_facts_are_suggestions_requiring_review():
    low = field("low", "Phone", "business.phone")
    conflict = field("conflict", "Annual Revenue", "business.revenue", "number")
    low_fact = fact("f-low", "business.phone", "555-0100", confidence=0.61, uncertainty=["low OCR confidence"])
    conflicted = fact("f-revenue", "business.revenue", "60000", kind="number", contradicts=["f-revenue-other"])
    other = fact("f-revenue-other", "business.revenue", "65000", kind="number", contradicts=["f-revenue"])
    result, _gateway = run([low, conflict], [low_fact, conflicted, other], [proposal("low", "f-low"), proposal("conflict", "f-revenue")])
    targets = by_id(result)
    assert targets["low"]["selected_candidate_id"] is not None
    assert targets["low"]["candidates"][0]["review_required"] is True
    assert targets["conflict"]["candidates"][0]["review_required"] is True
    assert targets["conflict"]["candidates"][0]["selectable"] is True


def test_unsupported_effective_date_stays_blank_and_optional_blank_has_no_issue():
    date = field("effective", "Effective Date", "policy.effective_date", "date")
    other = field("phone", "Phone", "business.phone")
    phone = fact("f-phone", "business.phone", "555-0100")
    result, _gateway = run([date, other], [phone], [proposal("phone", "f-phone")])
    targets = by_id(result)
    assert targets["effective"]["selected_candidate_id"] is None
    assert targets["effective"]["issues"] == []


def test_whitespace_native_blank_is_writable_but_existing_value_is_preserved():
    blank = field("blank", "Auto Liability Limit", "coverage.auto_liability_limit", "choice",
        native_name="Coverage_AL_Limit", current_value=" ",
        choice_options=[{"display_value": "300,000", "export_value": "300000"}],
        constraints={"choice_options": [{"display_value": "300,000", "export_value": "300000"}]})
    current = field("current", "FEIN", "business.fein", current_value="99-9999999")
    liability = fact("f-limit", "auto.liability", "300,000")
    fein = fact("f-fein", "business.ein", "12-3456789")
    result, _gateway = run([blank, current], [liability, fein], [])
    targets = by_id(result)
    assert targets["blank"]["candidates"][0]["write_value"] == "300000"
    assert targets["current"]["candidates"] == []


def test_required_blank_surfaces_only_required_field_and_legacy_mapper_version_is_new():
    required = field("required", "Policy Number", "policy.number", required=True)
    optional = field("optional", "Policy Effective Date", "policy.effective_date")
    result, _gateway = run([required, optional], [], [])
    targets = by_id(result)
    assert [issue["code"] for issue in targets["required"]["issues"]] == ["required_field_blank"]
    assert targets["optional"]["issues"] == []
    assert result["mapper_version"] == "model-mapper-v2"
