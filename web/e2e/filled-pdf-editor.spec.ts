import { expect, test } from "@playwright/test";
import { PDFDocument } from "pdf-lib";
import { readFile } from "node:fs/promises";

test("opens a generated PDF and downloads an edited copy", async ({ page }) => {
  const pdf = await PDFDocument.create();
  pdf.addPage([300, 400]);
  const bytes = await pdf.save();
  await page.route("http://localhost:8000/api/v1/form-fills/fill-1", (route) => route.fulfill({ json: {
    id: "fill-1", template_name: "Test form", target_kind: "pdf", output_available: true,
  } }));
  await page.route("http://localhost:8000/api/v1/form-fills/fill-1/output?preview=1", (route) => route.fulfill({
    body: Buffer.from(bytes), contentType: "application/pdf",
  }));

  await page.goto("/form-fills/fill-1/edit");
  await expect(page.getByRole("button", { name: "Download edited copy" })).toBeEnabled();
  await expect(page.getByLabel("Page to edit")).toHaveValue("1");
  await page.getByRole("button", { name: "Rotate page" }).click();
  await expect(page.getByRole("button", { name: "Download edited copy" })).toBeEnabled();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download edited copy" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("Test-form-edited.pdf");
  const edited = await PDFDocument.load(await readFile(await file.path()));
  expect(edited.getPage(0).getRotation().angle).toBe(90);
});
