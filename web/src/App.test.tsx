// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";

const fill = {
  id: "fill-1", case_key: "case-1", template_version_id: "version-1",
  template_name: "Test form", target_artifact_id: "artifact-1", target_kind: "pdf",
  status: "mapped", output_available: false, output_error: null,
  fields: [{
    field: { id: "applicant", label: "Applicant", field_type: "text", writable: true,
      location: { kind: "pdf_rect", page: 1, rect: [20, 20, 120, 40] } },
    write_value: "Alice", origin: "model", evidence_fact_ids: ["fact-a"],
    snippets: [{ text: "Applicant: Alice" }],
  }],
  evidence: [
    { fact: { id: "fact-a", label: "Name", value: "Alice", entity_role: "applicant" }, used: true, field_ids: ["applicant"] },
    { fact: { id: "fact-b", label: "Driver", value: "Bob", entity_role: "driver" }, used: false, field_ids: [] },
  ],
};

afterEach(() => vi.restoreAllMocks());

test("review shows stored hover source and lets an unused fact target a field", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, status: 200, json: async () => fill })));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={["/form-fills/fill-1"]}><App /></MemoryRouter></QueryClientProvider>);

  await screen.findByText("1 used · 1 unused");
  await waitFor(() => expect(document.querySelector(".pdf-field")?.getAttribute("title")).toBe("Applicant: Alice"));
  fireEvent.click(screen.getByText("Driver: Bob"));
  expect(screen.getByText("Use selected intake fact")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Save to field" })).toBeTruthy();
});
