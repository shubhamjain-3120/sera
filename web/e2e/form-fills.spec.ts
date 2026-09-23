import { expect, test } from "@playwright/test";

test("reviews mapped and unused facts, then exports a whole form", async ({ page }) => {
  const data = {
    id: "fill-1", case_key: "case-1", template_version_id: "version-1",
    template_name: "Rivington", target_artifact_id: "target-1", target_kind: "pdf",
    status: "mapped", output_available: false, output_error: null,
    fields: [{ field: { id: "name", label: "Applicant name", field_type: "text", writable: true,
      location: { kind: "pdf_rect", page: 1, rect: [20, 30, 160, 50], page_width: 612, page_height: 792 } },
      write_value: "Alice", origin: "model", evidence_fact_ids: ["fact-a"],
      snippets: [{ text: "Applicant: Alice" }] }],
    evidence: [
      { fact: { id: "fact-a", label: "Applicant", value: "Alice", entity_role: "applicant" }, used: true, field_ids: ["name"] },
      { fact: { id: "fact-b", label: "Driver", value: "Bob", entity_role: "driver" }, used: false, field_ids: [] },
    ],
  };
  await page.route("**/api/v1/form-fills/fill-1", (route) => route.fulfill({ json: data }));
  await page.route("**/api/v1/form-fills/fill-1/fields/name", (route) => {
    data.fields[0].write_value = "Bob";
    data.fields[0].origin = "human";
    data.fields[0].evidence_fact_ids = ["fact-b"];
    data.fields[0].snippets = [{ text: "Driver: Bob" }];
    data.evidence[0].used = false;
    data.evidence[1].used = true;
    return route.fulfill({ json: data });
  });
  await page.route("**/api/v1/form-fills/fill-1/approve-and-export", (route) => {
    data.status = "exported";
    data.output_available = true;
    return route.fulfill({ json: data });
  });
  await page.route("**/api/v1/form-fills/fill-1/output**", (route) => route.fulfill({ contentType: "application/pdf", body: "" }));
  await page.route("**/api/v1/artifacts/target-1/pages/1.png**", (route) => route.fulfill({ contentType: "image/png", body: "" }));
  await page.goto("/form-fills/fill-1");

  await expect(page.getByText("1 used · 1 unused")).toBeVisible();
  await expect(page.locator("button.pdf-field")).toHaveAttribute("title", "Applicant: Alice");
  await page.getByText("Driver: Bob").click();
  await expect(page.getByText("Use selected intake fact")).toBeVisible();
  await page.getByRole("button", { name: "Save to field" }).click();
  await expect(page.locator("button.pdf-field")).toHaveAttribute("title", "Driver: Bob");
  await page.getByRole("button", { name: /Approve & Generate/ }).click();
  await expect(page.getByRole("link", { name: "Download generated file" })).toBeVisible();
});

test("shows the affected field when native output writing fails", async ({ page }) => {
  await page.route("**/api/v1/form-fills/fill-error", (route) => route.fulfill({ json: {
    id: "fill-error", case_key: "case-1", template_version_id: "version-1",
    template_name: "Workbook", target_artifact_id: "target-1", target_kind: "xlsx",
    status: "mapped", output_available: false, output_error: null,
    fields: [{ field: { id: "formula-cell", label: "Calculated total", field_type: "number", writable: true,
      location: { kind: "xlsx_range", sheet: "Sheet1", cell_range: "C3" } }, write_value: 10, origin: "human", snippets: [] }],
    evidence: [],
  } }));
  await page.route("**/api/v1/form-fills/fill-error/approve-and-export", (route) => route.fulfill({
    status: 422, json: { detail: { field_id: "formula-cell", error: "Cannot write into a formula cell" } },
  }));
  await page.route("**/api/v1/artifacts/target-1/sheets/Sheet1/grid**", (route) => route.fulfill({ json: { rows: [] } }));
  await page.goto("/form-fills/fill-error");
  await page.getByRole("button", { name: /Approve & Generate/ }).click();
  await expect(page.getByText("formula-cell: Cannot write into a formula cell")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download generated file" })).toHaveCount(0);
});
