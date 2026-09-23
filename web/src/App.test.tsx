// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

const fill = {
  id: "fill-1", case_key: "case-1", template_version_id: "version-1", template_name: "Carrier form",
  target_artifact_id: "artifact-1", target_kind: "pdf", status: "mapped", output_available: false,
  fields: [{ field: { id: "applicant", label: "Applicant", field_type: "text", writable: true,
    location: { kind: "pdf_rect", page: 1, rect: [20, 20, 120, 40] } },
    write_value: "Alice", origin: "model", classification: "tentative", evidence_fact_ids: ["fact-a"],
    snippets: [{ text: "Applicant: Alice", artifact_id: "source-1", snapshot_id: "snapshot-1", kind: "pdf_rect", page: 1, rect: [20, 20, 130, 40], page_width: 300, page_height: 300 }] }],
  evidence: [{ fact: { id: "fact-a", label: "Name", value: "Alice", entity_role: "applicant" }, used: true, field_ids: ["applicant"] }],
};
function mount(path = "/form-fills/fill-1") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("edits the PDF answer directly, saves it, and resolves its review item", async () => {
  const calls: Array<{ url: string; body?: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push({ url: input, body: init?.body as string | undefined });
    return { ok: true, json: async () => fill };
  }));
  mount();
  const input = await screen.findByRole("textbox", { name: "Edit Applicant on form" });
  expect(screen.getByText("1 need review")).toBeTruthy();
  fireEvent.change(input, { target: { value: "Bob" } });
  fireEvent.blur(input);
  await waitFor(() => expect(calls.some((call) => call.url.includes("/fields/applicant") && call.body?.includes('"write_value":"Bob"'))).toBe(true));
  await waitFor(() => expect(screen.getByText("0 need review")).toBeTruthy());
  expect(screen.getByRole("img", { name: "Source screenshot, page 1" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /Open source/ }).getAttribute("href")).toContain("fact=fact-a");
});

test("places a check mark on the form and exposes undo and redo", async () => {
  const calls: Array<{ url: string; body?: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push({ url: input, body: init?.body as string | undefined });
    return { ok: true, json: async () => fill };
  }));
  mount();
  await screen.findByRole("button", { name: "+ ✓" });
  fireEvent.click(screen.getByRole("button", { name: "+ ✓" }));
  fireEvent.click(document.querySelector(".review-paper")!);
  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/annotations") && call.body?.includes('"kind":"check"'))).toBe(true));
  expect(screen.getByRole("button", { name: "Undo" }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Undo" }));
  await waitFor(() => expect(document.querySelectorAll(".review-annotation").length).toBe(0));
  fireEvent.click(screen.getByRole("button", { name: "Redo" }));
  await waitFor(() => expect(document.querySelectorAll(".review-annotation").length).toBe(1));
});

test("retains a failed edit for retry and blocks export until it saves", async () => {
  let fail = true;
  let exports = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (input.includes("/fields/applicant") && init?.method === "PATCH" && fail) return { ok: false, json: async () => ({ detail: "Temporary save failure" }) };
    if (input.includes("approve-and-export")) exports += 1;
    return { ok: true, json: async () => fill };
  }));
  mount();
  const input = await screen.findByRole("textbox", { name: "Edit Applicant on form" });
  fireEvent.change(input, { target: { value: "Corrected" } });
  fireEvent.blur(input);
  await screen.findByText("Save failed");
  fireEvent.click(screen.getByRole("button", { name: "Download PDF" }));
  await screen.findByText("Save failed. Retry the save before downloading.");
  expect(exports).toBe(0);
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry save" }));
  await waitFor(() => expect(screen.getByText("Saved")).toBeTruthy());
});

test("reviews spreadsheet confidence, edits cells, and writes marks into the selected cell", async () => {
  const xlsxFill = {
    ...fill,
    target_kind: "xlsx",
    fields: [{ ...fill.fields[0], confidence: "low" as const, field: { ...fill.fields[0].field, location: { kind: "xlsx_range" as const, sheet: "Application", cell_range: "A1" } } }],
  };
  const calls: Array<{ url: string; body?: string }> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push({ url: input, body: init?.body as string | undefined });
    if (input.includes("/sheets/")) return { ok: true, json: async () => ({ rows: [[{ coordinate: "A1", value: "Alice", formula: false, locked: false, number_format: "" }]] }) };
    return { ok: true, json: async () => xlsxFill };
  }));
  mount();
  expect(await screen.findByText("1 need review")).toBeTruthy();
  const cell = await waitFor(() => {
    const result = document.querySelector('[data-field-id="applicant"]');
    expect(result?.className).toContain("confidence-low");
    return result!;
  });
  fireEvent.mouseEnter(cell);
  expect(await screen.findAllByRole("img", { name: "Source screenshot, page 1" })).toHaveLength(2);
  const value = screen.getByRole("textbox", { name: "Value" });
  fireEvent.change(value, { target: { value: "Bob" } });
  fireEvent.click(screen.getByRole("button", { name: "Save value" }));
  await waitFor(() => expect(calls.some((call) => call.url.includes("/fields/applicant") && call.body?.includes('"write_value":"Bob"'))).toBe(true));
  for (const mark of ["✓", "Y", "N"]) {
    fireEvent.click(screen.getByRole("button", { name: mark }));
    await waitFor(() => expect(calls.some((call) => call.url.includes("/fields/applicant") && call.body?.includes(`"write_value":"${mark}"`) && call.body?.includes('"review_status":"accepted"'))).toBe(true));
  }
});
