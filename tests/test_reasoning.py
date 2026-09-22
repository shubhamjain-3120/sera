import threading
import time
from types import SimpleNamespace

from app.config import Settings
from app.gateway import ModelGateway
from app.mapping import build_fill_plan
from app.reasoning import (
    FactClassification,
    FactClassifications,
    MappingChoice,
    MappingChoices,
    ModelEvidenceVerifier,
    ModelFinding,
    ModelFindings,
    SemanticProposal,
    SemanticProposals,
    adjudicate_mapping,
    classify_evidence_facts,
    propose_semantic_types,
)
from app.verification import build_verification_report
from tests.test_fill_plans import fact, field


def gateway_returning(parsed: object, *, key: str | None = "test-key") -> ModelGateway:
    instance = ModelGateway(Settings(openai_api_key=key))
    response = SimpleNamespace(
        id="resp_1", status="completed", output=[], output_parsed=parsed,
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
    )
    instance._client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **_: response))
    return instance


def ambiguous_plan() -> dict:
    """Two same-scoring facts with different values leave the target unresolved."""
    return build_fill_plan(
        {"fields": [field("driver-name", "driver.full_name")]},
        [(
            "snap-1",
            {"facts": [
                fact("f-1", "driver.full_name", "Mohammad Khalifeh", entity_role="driver"),
                fact("f-2", "driver.full_name", "M. Khalifeh", entity_role="driver"),
            ]},
        )],
    )


def target_of(payload: dict, field_id: str) -> dict:
    return next(item for item in payload["targets"] if item["field"]["id"] == field_id)


def test_deterministic_mapper_leaves_the_ambiguous_target_for_the_model():
    payload = ambiguous_plan()
    assert target_of(payload, "driver-name")["selected_candidate_id"] is None


def test_model_may_settle_an_unresolved_target_by_citing_a_real_candidate():
    payload = ambiguous_plan()
    candidate_id = target_of(payload, "driver-name")["candidates"][0]["id"]
    gateway = gateway_returning(
        MappingChoices(choices=[MappingChoice(target_field_id="driver-name", candidate_id=candidate_id, confidence=0.9, reasoning="Full legal name.")])
    )

    result, trace = adjudicate_mapping(payload, gateway, input_reference="ref")

    target = target_of(result, "driver-name")
    assert target["selected_candidate_id"] == candidate_id
    assert target["model_decision"]["reasoning"] == "Full legal name."
    selected = next(item for item in target["candidates"] if item["id"] == candidate_id)
    assert selected["resolution"] == "model_adjudicated"
    # The value still comes from the frozen fact, never from the model.
    assert selected["value"] == "Mohammad Khalifeh"
    assert selected["provenance"]
    assert selected["review_required"] is True
    assert trace["applied_count"] == 1


def test_a_candidate_id_the_model_invented_is_rejected():
    payload = ambiguous_plan()
    gateway = gateway_returning(
        MappingChoices(choices=[MappingChoice(target_field_id="driver-name", candidate_id="not-a-real-candidate", confidence=1.0, reasoning="Made up.")])
    )

    result, trace = adjudicate_mapping(payload, gateway, input_reference="ref")

    assert target_of(result, "driver-name")["selected_candidate_id"] is None
    assert trace["rejected_target_ids"] == ["driver-name"]
    assert trace["applied_count"] == 0


def test_a_null_choice_leaves_the_target_unresolved():
    payload = ambiguous_plan()
    gateway = gateway_returning(
        MappingChoices(choices=[MappingChoice(target_field_id="driver-name", candidate_id=None, confidence=0.2, reasoning="Cannot distinguish.")])
    )

    result, trace = adjudicate_mapping(payload, gateway, input_reference="ref")

    assert target_of(result, "driver-name")["selected_candidate_id"] is None
    assert trace["applied_count"] == 0


def test_adjudication_is_skipped_without_credentials():
    payload = ambiguous_plan()
    result, trace = adjudicate_mapping(payload, ModelGateway(Settings(openai_api_key=None)), input_reference="ref")
    assert trace["outcome"] == "disabled"
    assert target_of(result, "driver-name")["selected_candidate_id"] is None


def sequenced_gateway(responses: list[object], *, batch_size: int) -> ModelGateway:
    """A gateway whose successive calls return the given results in order."""
    instance = ModelGateway(Settings(openai_api_key="k", model_batch_size=batch_size))
    queue = list(responses)

    def parse(**_):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            id="r", status="completed", output=[], output_parsed=item,
            usage=SimpleNamespace(input_tokens=5, output_tokens=5, total_tokens=10),
        )

    instance._client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    return instance


def plan_with_unresolved(count: int) -> dict:
    """`count` targets that each have two conflicting facts, so none resolve."""
    return build_fill_plan(
        {"fields": [field(f"t-{i}", "driver.full_name") for i in range(count)]},
        [("s", {"facts": [
            fact("f-1", "driver.full_name", "Mohammad Khalifeh", entity_role="driver"),
            fact("f-2", "driver.full_name", "M. Khalifeh", entity_role="driver"),
        ]})],
    )


def test_large_target_sets_are_split_into_batches():
    payload = plan_with_unresolved(5)
    picks = [
        MappingChoices(choices=[
            MappingChoice(target_field_id=t["field"]["id"], candidate_id=t["candidates"][0]["id"], confidence=0.9, reasoning="ok")
            for t in payload["targets"][start : start + 2]
        ])
        for start in (0, 2, 4)
    ]
    gateway = sequenced_gateway(picks, batch_size=2)

    result, trace = adjudicate_mapping(payload, gateway, input_reference="ref")

    assert trace["batch_count"] == 3
    assert trace["outcome"] == "ok"
    assert trace["applied_count"] == 5
    assert trace["usage"]["total_tokens"] == 30
    assert all(t["selected_candidate_id"] for t in result["targets"])


def test_batches_run_concurrently_rather_than_one_after_another():
    payload = plan_with_unresolved(8)
    picks = [
        MappingChoices(choices=[
            MappingChoice(target_field_id=t["field"]["id"], candidate_id=t["candidates"][0]["id"], confidence=0.9, reasoning="ok")
            for t in payload["targets"][start : start + 2]
        ])
        for start in (0, 2, 4, 6)
    ]
    gateway = sequenced_gateway(picks, batch_size=2)
    inner = gateway._client.responses.parse
    lock = threading.Lock()
    live, peak = 0, 0

    def slow(**kwargs):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.25)
        with lock:
            live -= 1
        return inner(**kwargs)

    gateway._client.responses.parse = slow
    progress: list[tuple[int, int]] = []

    started = time.perf_counter()
    _, trace = adjudicate_mapping(payload, gateway, input_reference="ref", on_batch=lambda done, total: progress.append((done, total)))
    elapsed = time.perf_counter() - started

    assert trace["batch_count"] == 4
    assert peak > 1, "batches ran sequentially"
    assert elapsed < 0.25 * 4, "no faster than running them one after another"
    # Progress is reported from the calling thread, ending at 4 of 4.
    assert progress[0] == (0, 4)
    assert progress[-1] == (4, 4)


def test_results_keep_batch_order_regardless_of_completion_order():
    payload = plan_with_unresolved(4)
    ids = [t["candidates"][0]["id"] for t in payload["targets"]]
    picks = [
        MappingChoices(choices=[MappingChoice(target_field_id=f"t-{i}", candidate_id=ids[i], confidence=0.9, reasoning=f"batch{i}")])
        for i in range(4)
    ]
    gateway = sequenced_gateway(picks, batch_size=1)
    inner = gateway._client.responses.parse
    calls = {"n": 0}

    def jittered(**kwargs):
        # Finish in reverse submission order.
        with threading.Lock():
            calls["n"] += 1
            index = calls["n"]
        time.sleep(0.05 * (5 - index))
        return inner(**kwargs)

    gateway._client.responses.parse = jittered
    result, trace = adjudicate_mapping(payload, gateway, input_reference="ref")

    assert trace["batch_count"] == 4
    assert trace["applied_count"] == 4
    assert all(t["selected_candidate_id"] for t in result["targets"])


def test_one_timed_out_batch_does_not_discard_the_others():
    payload = plan_with_unresolved(4)
    good = MappingChoices(choices=[
        MappingChoice(target_field_id=t["field"]["id"], candidate_id=t["candidates"][0]["id"], confidence=0.9, reasoning="ok")
        for t in payload["targets"][:2]
    ])
    gateway = sequenced_gateway([good, TimeoutError("Request timed out")], batch_size=2)

    result, trace = adjudicate_mapping(payload, gateway, input_reference="ref")

    assert trace["outcome"] == "partial"
    assert trace["applied_count"] == 2
    assert "TimeoutError" in trace["error"]
    resolved = [t for t in result["targets"] if t["selected_candidate_id"]]
    assert len(resolved) == 2


def test_model_types_only_the_fields_rules_could_not_classify():
    schema = {
        "fields": [
            {"id": "f-1", "label": "Driver License No.", "writable": True, "field_type": "text"},
            {"id": "f-2", "label": "Name", "writable": True, "field_type": "text", "semantic_type": "applicant.full_name", "semantic_type_origin": "rule"},
        ]
    }
    gateway = gateway_returning(
        SemanticProposals(proposals=[
            SemanticProposal(target_field_id="f-1", semantic_type="driver.license_number", confidence=0.8),
            SemanticProposal(target_field_id="f-2", semantic_type="driver.full_name", confidence=0.9),
        ])
    )

    result, trace = propose_semantic_types(schema, gateway, input_reference="ref")

    typed = result["fields"][0]
    assert typed["semantic_type"] == "driver.license_number"
    assert typed["semantic_type_origin"] == "model"
    # A field a rule already typed is not offered to the model, so its proposal
    # for f-2 has nothing to apply to.
    assert result["fields"][1]["semantic_type"] == "applicant.full_name"
    assert result["fields"][1]["semantic_type_origin"] == "rule"
    assert trace["applied_count"] == 1
    assert trace["pending_count"] == 1


def test_signature_fields_are_never_typed_by_the_model():
    schema = {"fields": [{"id": "sig", "label": "Signature", "writable": False, "field_type": "signature"}]}
    _, trace = propose_semantic_types(schema, gateway_returning(SemanticProposals(proposals=[])), input_reference="ref")
    assert trace["outcome"] == "no_pending_fields"


def test_classification_relabels_a_fact_without_changing_its_value_or_provenance():
    original = fact("f-1", "applicant.phone", "555-0100", accepted=False)
    payload = {"facts": [original]}
    provenance_before = [dict(item) for item in original["provenance"]]
    gateway = gateway_returning(
        FactClassifications(classifications=[FactClassification(fact_id="f-1", key="broker.phone", entity_role="broker", confidence=0.87)])
    )

    result, trace = classify_evidence_facts(payload, gateway, input_reference="ref")

    relabelled = result["facts"][0]
    assert relabelled["key"] == "broker.phone"
    assert relabelled["entity_role"] == "broker"
    assert relabelled["value"] == "555-0100"
    assert relabelled["provenance"] == provenance_before
    # Relabelling does not promote a fact past the review it still needs.
    assert relabelled["accepted"] is False
    assert trace["applied_count"] == 1


def test_already_accepted_facts_are_not_sent_for_classification():
    payload = {"facts": [fact("f-1", "applicant.phone", "555-0100", accepted=True)]}
    _, trace = classify_evidence_facts(payload, gateway_returning(FactClassifications(classifications=[])), input_reference="ref")
    assert trace["outcome"] == "no_pending_facts"


def resolved_plan() -> dict:
    return build_fill_plan(
        {"fields": [field("driver-name", "driver.full_name")]},
        [("snap-1", {"facts": [fact("f-1", "driver.full_name", "Mohammad Khalifeh", entity_role="driver")]})],
    )


def test_model_verifier_reports_findings_without_changing_any_selection():
    payload = resolved_plan()
    snapshots = [{"facts": [fact("f-1", "driver.full_name", "Mohammad Khalifeh", entity_role="driver")]}]
    verifier = ModelEvidenceVerifier(
        gateway_returning(ModelFindings(findings=[ModelFinding(target_field_id="driver-name", code="conflicting_evidence", severity="review", message="Another document disagrees.")]))
    )

    before = target_of(payload, "driver-name")["selected_candidate_id"]
    report = build_verification_report(payload, snapshots, verifier)

    assert target_of(payload, "driver-name")["selected_candidate_id"] == before
    assert report["independent"]["status"] == "needs_review"
    assert report["independent"]["findings"][0]["code"] == "conflicting_evidence"


def test_findings_for_unknown_targets_are_discarded():
    payload = resolved_plan()
    verifier = ModelEvidenceVerifier(
        gateway_returning(ModelFindings(findings=[ModelFinding(target_field_id="a-field-that-does-not-exist", code="value_not_supported", severity="blocker", message="Bogus.")]))
    )
    assert verifier.verify(payload, [])["findings"] == []


def test_an_unavailable_verifier_does_not_read_as_a_pass():
    verifier = ModelEvidenceVerifier(ModelGateway(Settings(openai_api_key=None)))
    outcome = verifier.verify(resolved_plan(), [])
    assert outcome["status"] == "needs_review"
    assert outcome["unavailable"]["outcome"] == "disabled"
