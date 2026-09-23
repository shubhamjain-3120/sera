import { expect, test } from "@playwright/test";

test("reviews a tentative answer, adds marks, and warns before download", async ({ page }) => {
  const data = {
    id: "fill-1", case_key: "case-1", template_version_id: "version-1", template_name: "Carrier",
    target_artifact_id: "target-1", target_kind: "pdf", status: "mapped", output_available: false,
    fields: [{ field: { id: "name", label: "Applicant name", field_type: "text", writable: true,
      location: { kind: "pdf_rect", page: 1, rect: [20, 30, 160, 50], page_width: 612, page_height: 792 } },
      write_value: "Alice", origin: "model", classification: "tentative", snippets: [{ text: "Applicant: Alice" }] },
      { field: { id: "yesno", label: "Has drivers?", field_type: "boolean", writable: true,
        location: { kind: "pdf_rect", page: 1, rect: [180, 30, 200, 50], page_width: 612, page_height: 792 } },
        write_value: null, origin: "prefilled", snippets: [] }],
    evidence: [], mapping_metadata: { annotations: [] },
  };
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/pS8AAAAASUVORK5CYII=", "base64");
  await page.route("**/api/v1/form-fills/fill-1", (route) => route.fulfill({ json: data }));
  await page.route("**/api/v1/form-fills/fill-1/fields/name", async (route) => {
    const patch = route.request().postDataJSON();
    Object.assign(data.fields[0], { write_value: patch.write_value ?? data.fields[0].write_value, review_status: patch.review_status ?? null });
    return route.fulfill({ json: data });
  });
  let yesNoValue: unknown = null;
  await page.route("**/api/v1/form-fills/fill-1/fields/yesno", async (route) => {
    yesNoValue = route.request().postDataJSON().write_value;
    return route.fulfill({ json: data });
  });
  await page.route("**/api/v1/form-fills/fill-1/annotations", async (route) => {
    data.mapping_metadata.annotations = route.request().postDataJSON().annotations;
    return route.fulfill({ json: data });
  });
  await page.route("**/api/v1/artifacts/target-1/pages/1.png**", (route) => route.fulfill({ contentType: "image/png", body: png }));
  await page.goto("/form-fills/fill-1");
  await expect(page.getByText("1 need review")).toBeVisible();
  await page.getByRole("button", { name: "Download PDF" }).click();
  await expect(page.getByRole("dialog")).toContainText("1 answers still need review");
  await page.getByRole("button", { name: "Continue reviewing" }).click();
  await page.getByRole("button", { name: "+ Y" }).click();
  await page.locator(".review-paper").click({ position: { x: 300, y: 300 } });
  await expect(page.locator(".review-annotation")).toContainText("Y");
  await expect.poll(() => data.mapping_metadata.annotations.length).toBe(1);
  await page.getByRole("button", { name: "+ Y" }).click();
  await page.locator(".review-field").nth(1).click();
  await expect.poll(() => yesNoValue).toBe(true);
  await page.locator(".review-field").first().click();
  await page.getByRole("textbox", { name: "Edit Applicant name on form" }).fill("Bob");
  await page.getByRole("textbox", { name: "Edit Applicant name on form" }).blur();
  await expect.poll(() => data.fields[0].write_value).toBe("Bob");
  await expect(page.getByText("0 need review")).toBeVisible();
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
