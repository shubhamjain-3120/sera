import { expect, test } from "vitest";
import { PDFDocument } from "pdf-lib";
import { editPdfPage } from "./pdfPageTools";

test("moving and rotating a page preserves form fields and page content", async () => {
  const pdf = await PDFDocument.create();
  const first = pdf.addPage([200, 300]);
  pdf.addPage([201, 300]);
  const field = pdf.getForm().createTextField("applicant");
  field.addToPage(first, { x: 10, y: 10, width: 80, height: 20 });
  field.setText("Alice");

  const moved = await editPdfPage(await pdf.save(), 1, "down");
  const rotated = await editPdfPage(moved.bytes, moved.nextPage, "rotate");
  const reopened = await PDFDocument.load(rotated.bytes);

  expect(reopened.getPages().map((page) => page.getWidth())).toEqual([201, 200]);
  expect(reopened.getPage(1).getRotation().angle).toBe(90);
  expect(reopened.getForm().getTextField("applicant").getText()).toBe("Alice");
});

test("deleting the only page is rejected", async () => {
  const pdf = await PDFDocument.create();
  pdf.addPage();
  await expect(editPdfPage(await pdf.save(), 1, "delete"))
    .rejects.toThrow("A PDF needs at least one page");
});
