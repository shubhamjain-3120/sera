import { expect, test } from "@playwright/test";

test("shows isolated evidence ingestion controls", async ({ page }) => {
  await page.route("**/api/v1/evidence/sources", (route) => route.fulfill({ json: [] }));
  await page.goto("/evidence");
  await expect(page.getByRole("heading", { name: /Trace every fact/ })).toBeVisible();
  await expect(page.getByLabel("Case")).toHaveValue("case-1");
  await expect(page.getByRole("button", { name: /Add source/ })).toBeVisible();
  await expect(page.getByText(/Filled reference outputs are rejected/)).toBeVisible();
});

test("navigates an evidence fact to its exact source region", async ({ page }) => {
  await page.route("**/api/v1/evidence/snapshots/snapshot-1", (route) => route.fulfill({ json: {
    id: "snapshot-1",
    artifact_id: "artifact-1",
    run_id: "run-1",
    parser_provider: "recorded",
    parser_version: "recorded-v1",
    extractor_version: "rules-evidence-v1",
    snapshot_sha256: "a".repeat(64),
    created_at: new Date().toISOString(),
    snapshot: {
      facts: [{ id: "fact-1", key: "person.given_name", label: "Given name", value: null, raw_value: "No given name", value_type: "text", entity_id: "applicant", entity_role: "applicant", provenance: [{ kind: "image_rect", artifact_id: "artifact-1", page: 1, rect: [44, 68, 280, 98], coordinate_system: "pixels-top-left", page_width: 640, page_height: 400 }], confidence: 0.98, uncertainty: [], unit: null, date_context: null, period_context: null, duplicate_of: null, contradicts: [], accepted: true, semantics: ["explicit_absence"] }],
      entities: [{ id: "applicant", role: "applicant" }], unreadable_regions: [], parse_blocks: [], parser_provider: "recorded", parser_version: "recorded-v1", extractor_version: "rules-evidence-v1", warnings: [], source_sha256: "b".repeat(64),
    },
  } }));
  await page.goto("/evidence/artifact-1/snapshot-1");
  await expect(page.getByRole("heading", { name: "Accepted fact" })).toBeVisible();
  await expect(page.getByText(/No value.*explicitly absent/i)).toBeVisible();
  await expect(page.getByText("Page 1 · [44, 68, 280, 98]", { exact: true })).toBeVisible();
  await expect(page.locator("button.pdf-field", { hasText: "" })).toHaveAttribute("aria-label", "Given name");
});

test("renders Reducto normalized top-left geometry as a visible highlight", async ({ page }) => {
  await page.route("**/api/v1/evidence/snapshots/snapshot-reducto", (route) => route.fulfill({ json: {
    id: "snapshot-reducto", artifact_id: "artifact-1", run_id: "run-1", parser_provider: "reducto", parser_version: "reducto-v1", extractor_version: "rules-evidence-v1", snapshot_sha256: "a".repeat(64), created_at: new Date().toISOString(),
    snapshot: { facts: [{ id: "fact-1", key: "name", label: "Name", value: "Example", raw_value: "Example", value_type: "text", entity_id: "applicant", entity_role: "applicant", provenance: [{ kind: "pdf_rect", artifact_id: "artifact-1", page: 1, rect: [0.1, 0.2, 0.4, 0.3], coordinate_system: "reducto-normalized-top-left" }], confidence: 0.98, uncertainty: [], unit: null, date_context: null, period_context: null, duplicate_of: null, contradicts: [], accepted: true, semantics: [] }], entities: [], unreadable_regions: [], parse_blocks: [], parser_provider: "reducto", parser_version: "reducto-v1", extractor_version: "rules-evidence-v1", warnings: [], source_sha256: "b".repeat(64) },
  } }));
  await page.goto("/evidence/artifact-1/snapshot-reducto");
  await expect(page.locator("button.pdf-field")).toHaveAttribute("style", /left: 10%; top: 20%; width: 30/);
});
