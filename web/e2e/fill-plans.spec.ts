import { expect, test } from "@playwright/test";

test("shows frozen evidence, target overlay, alternatives, and unresolved issues", async ({ page }) => {
  const location = { kind: "pdf_rect", artifact_id: "source-1", page: 1, rect: [0.1, 0.2, 0.4, 0.3], coordinate_system: "reducto-normalized-top-left" };
  await page.route("**/api/v1/fill-plans/plan-1", (route) => route.fulfill({ json: {
    id: "plan-1", case_key: "case-2", template_version_id: "version-1", template_name: "Rivington", target_artifact_id: "target-1", target_kind: "pdf", evidence_bundle_id: "bundle-1", evidence_bundle_sha256: "a".repeat(64), current_revision: 1, issue_count: 1, blocker_count: 0, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), revision_id: "revision-1", mapper_version: "deterministic-mapper-v1", payload_sha256: "b".repeat(64),
    payload: {
      mapper_version: "deterministic-mapper-v1",
      evidence_bundle: { id: "bundle-1", case_key: "case-2", snapshot_ids: ["snapshot-1"], sha256: "a".repeat(64) },
      template_schema: { fields: [], repeating_groups: [], inspection: { format: "pdf", page_count: 1 } },
      targets: [{
        field: { id: "name", label: "Applicant name", semantic_type: "person.given_name", field_type: "text", writable: true, options: [], location: { kind: "pdf_rect", page: 1, rect: [20, 30, 160, 50], page_width: 612, page_height: 792 } },
        selected_candidate_id: null,
        state: "unresolved",
        issues: [{ id: "issue-1", code: "ambiguous_candidates", severity: "review", target_id: "name", message: "Multiple similarly matched facts have different values" }],
        candidates: [
          { id: "candidate-1", target_field_id: "name", snapshot_id: "snapshot-1", source_artifact_id: "source-1", fact_id: "fact-1", fact_key: "person.given_name", entity_id: "applicant", entity_role: "applicant", value: "Amina", raw_value: "Amina", value_type: "text", origin: "evidence", resolution: "direct", evidence_confidence: 0.97, match_score: 1, provenance: [location], uncertainty: [], contradicts: ["fact-2"], selectable: false, review_required: true },
          { id: "candidate-2", target_field_id: "name", snapshot_id: "snapshot-1", source_artifact_id: "source-1", fact_id: "fact-2", fact_key: "person.given_name", entity_id: "applicant", entity_role: "applicant", value: "Amira", raw_value: "Amira", value_type: "text", origin: "evidence", resolution: "direct", evidence_confidence: 0.91, match_score: 1, provenance: [location], uncertainty: [], contradicts: ["fact-1"], selectable: false, review_required: true },
        ],
      }],
      repeating_groups: [], derivations: [], issues: [{ id: "issue-1", code: "ambiguous_candidates", severity: "review", target_id: "name", message: "Multiple similarly matched facts have different values" }],
      summary: { target_count: 1, proposed_count: 0, unresolved_count: 1, issue_count: 1, blocker_count: 0 },
    },
  } }));
  await page.route("**/api/v1/artifacts/target-1/pages/1.png**", (route) => route.fulfill({ contentType: "image/png", body: "" }));
  await page.route("**/api/v1/fill-plans/plan-1/verifications", (route) => route.fulfill({ json: [{
    id: "report-1", fill_plan_revision_id: "revision-1", fill_plan_revision: 1, status: "needs_review", deterministic_version: "deterministic-validation-v1", verifier_version: "independent-evidence-verifier-v1", provider: "local-independent", model: null, input_sha256: "c".repeat(64), report_sha256: "d".repeat(64), created_at: new Date().toISOString(),
    report: {
      status: "needs_review", selection_unchanged: true, selection_hash_before: "e".repeat(64), selection_hash_after: "e".repeat(64), trace: {},
      deterministic: { version: "deterministic-validation-v1", status: "pass", findings: [] },
      independent: { version: "independent-evidence-verifier-v1", status: "needs_review", additional_evidence: [{ target_id: "name", snapshot_fact_id: "fact-1", value: "Amina", confidence: 0.97, provenance: [location] }], duration_ms: 2, findings: [{ id: "finding-1", source: "verifier", code: "missed_evidence", severity: "review", target_id: "name", message: "The verifier found plausible evidence that the mapper did not select.", evidence: [location] }] },
    },
  }] }));
  await page.goto("/fill-plans/plan-1");
  await expect(page.getByText(/Evidence frozen/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Resolve exception" })).toBeVisible();
  await expect(page.getByText("Amina", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Amira", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Multiple similarly matched facts have different values")).toBeVisible();
  await expect(page.getByText("Page 1 · [0.1, 0.2, 0.4, 0.3]", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Verification · revision 1")).toBeVisible();
  await expect(page.getByText("The verifier found plausible evidence that the mapper did not select.")).toBeVisible();
  await expect(page.getByText(/Selections unchanged/)).toBeVisible();
});
