import { PDFDocument, degrees } from "pdf-lib";

export type PageAction = "rotate" | "delete" | "up" | "down";

export async function editPdfPage(bytes: ArrayBuffer | Uint8Array, page: number, action: PageAction) {
  const pdf = await PDFDocument.load(bytes);
  const index = page - 1;
  if (index < 0 || index >= pdf.getPageCount()) throw new Error("Page is no longer available");
  let nextPage = page;
  if (action === "rotate") {
    const selected = pdf.getPage(index);
    selected.setRotation(degrees((selected.getRotation().angle + 90) % 360));
  } else if (action === "delete") {
    if (pdf.getPageCount() === 1) throw new Error("A PDF needs at least one page");
    pdf.removePage(index);
    nextPage = Math.min(page, pdf.getPageCount());
  } else {
    const destination = index + (action === "up" ? -1 : 1);
    if (destination < 0 || destination >= pdf.getPageCount()) throw new Error("Page cannot move farther");
    const selected = pdf.getPage(index);
    pdf.insertPage(index < destination ? destination + 1 : destination, selected);
    pdf.removePage(index < destination ? index : index + 1);
    nextPage = destination + 1;
  }
  return { bytes: await pdf.save(), pageCount: pdf.getPageCount(), nextPage };
}
