import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";
import "./styles.css";

vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
vi.stubGlobal("scrollTo", vi.fn());
Element.prototype.scrollIntoView = vi.fn();
afterEach(cleanup);

function renderApp(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}

test("renders the Phase 1 template library", async () => {
  renderApp();
  expect(screen.getByText(/Make every field/)).toBeInTheDocument();
  expect(await screen.findByText("Drop in a PDF or workbook")).toBeInTheDocument();
});

const artifact = { id: "target-1", filename: "Rivington.pdf", media_type: "application/pdf", kind: "pdf", sha256: "a".repeat(64), size_bytes: 1000, created_at: "2026-09-23T00:00:00Z", run_id: "inspect-1", draft_id: "draft-1" };
const version = { id: "version-1", draft_id: "draft-1", version: 1, name: "Rivington", schema: { fields: [], repeating_groups: [], inspection: { format: "pdf" } }, source_revision: 3, schema_sha256: "b".repeat(64), published_at: "2026-09-23T00:00:00Z" };
const draft = { id: "draft-1", artifact_id: "target-1", revision: 3, name: "Rivington", schema: version.schema, created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z" };

function fillPlanFixture(targets: unknown[]) {
  return {
    id: "plan-1", case_key: "case-1", template_version_id: version.id, template_name: "Rivington", target_artifact_id: "target-1", target_kind: "pdf", evidence_bundle_id: "bundle-1", evidence_bundle_sha256: "c".repeat(64), current_revision: 1, issue_count: 0, blocker_count: 0, created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z", revision_id: "revision-1", mapper_version: "mapper-v1", payload_sha256: "d".repeat(64),
    payload: { mapper_version: "mapper-v1", template_schema: { fields: [], repeating_groups: [], inspection: { format: "pdf", page_count: 1 } }, targets, repeating_groups: [], derivations: [], issues: [], summary: { target_count: targets.length, proposed_count: 0, unresolved_count: targets.length, issue_count: 0, blocker_count: 0 }, evidence_bundle: { id: "bundle-1", case_key: "case-1", snapshot_ids: ["snapshot-1"], sha256: "c".repeat(64) } },
  };
}

function targetFixture(id: string, state: "proposed" | "unresolved" | "reviewed" | "not_applicable", candidate?: Record<string, unknown>) {
  return {
    field: { id, label: id, semantic_type: `template.${id}`, field_type: "text", writable: true, options: [], location: { kind: "pdf_rect", page: 1, rect: [10, 10, 40, 20], page_width: 612, page_height: 792 } },
    selected_candidate_id: candidate ? (candidate.id as string | undefined) ?? `${id}-candidate` : null,
    state,
    issues: [],
    candidates: candidate ? [{ id: `${id}-candidate`, target_field_id: id, value: `${id}-value`, canonical_value: `${id}-canonical`, write_value: `${id}-write`, raw_value: `${id}-raw`, value_type: "text", origin: "evidence", resolution: "direct", evidence_confidence: 0.99, match_score: 0.99, provenance: [], uncertainty: [], contradicts: [], selectable: true, review_required: false, ...candidate }] : [],
  };
}

function mockBaseApi(overrides: (url: URL, init?: RequestInit) => unknown | undefined = () => undefined) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), window.location.origin);
    const overridden = overrides(url, init);
    if (overridden !== undefined) return { ok: true, status: 200, json: async () => overridden };
    if (url.pathname === "/api/v1/artifacts") return { ok: true, status: 200, json: async () => [artifact] };
    if (url.pathname === "/api/v1/cases") return { ok: true, status: 200, json: async () => [{ case_key: "case-1", source_count: 1, snapshot_count: 1, created_at: "2026-09-23T00:00:00Z" }] };
    if (url.pathname === "/api/v1/agencies") return { ok: true, status: 200, json: async () => [{ key: "agency-1", name: "Agency", details: {}, is_default: true }] };
    if (url.pathname === "/api/v1/fill-plans" && init?.method !== "POST") return { ok: true, status: 200, json: async () => [] };
    if (url.pathname === "/api/v1/templates/draft-1/versions") return { ok: true, status: 200, json: async () => [version] };
    if (url.pathname === "/api/v1/templates/draft-1") return { ok: true, status: 200, json: async () => draft };
    if (url.pathname.includes("/verifications")) return { ok: true, status: 200, json: async () => [] };
    if (url.pathname.includes("/review-decisions")) return { ok: true, status: 200, json: async () => [] };
    return { ok: true, status: 200, json: async () => [] };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("shows the exact published version and blocks a template with a newer draft", async () => {
  mockBaseApi((url) => url.pathname === "/api/v1/templates/draft-1" ? { ...draft, revision: 4 } : undefined);
  renderApp("/fill-plans");
  expect(await screen.findByText("Selected: version 1 · revision 3")).toBeInTheDocument();
  expect(screen.getByText("Draft revision 4 is newer. Publish it before creating a Fill Plan.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Rivington.pdf/ })).toBeDisabled();
  expect(fetch).not.toHaveBeenCalledWith(expect.stringContaining("/api/v1/fill-plans"), expect.objectContaining({ method: "POST" }));
});

test("creates asynchronously, polls the run, then opens its Fill Plan", async () => {
  let pollCount = 0;
  const fetchMock = mockBaseApi((url, init) => {
    if (url.pathname === "/api/v1/fill-plans" && init?.method === "POST") return { id: "run-1", artifact_id: "target-1", status: "queued", stage: "queued", progress: 0, provider: "model" };
    if (url.pathname === "/api/v1/processing-runs/run-1") {
      pollCount += 1;
      return { id: "run-1", artifact_id: "target-1", status: pollCount > 1 ? "succeeded" : "running", stage: pollCount > 1 ? "complete" : "mapping", progress: pollCount > 1 ? 100 : 50, provider: "model", result: pollCount > 1 ? { fill_plan_id: "plan-1" } : {} };
    }
    if (url.pathname === "/api/v1/fill-plans/plan-1") return fillPlanFixture([]);
    return undefined;
  });
  renderApp("/fill-plans");
  const createButton = await screen.findByRole("button", { name: /Rivington.pdf/ });
  await waitFor(() => expect(createButton).toBeEnabled());
  fireEvent.click(createButton);
  expect(await screen.findByRole("status")).toHaveTextContent(/Creating Fill Plan/);
  expect(await screen.findByText(/Evidence frozen/)).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/fill-plans"), expect.objectContaining({ method: "POST" }));
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/processing-runs/run-1"), undefined);
  expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/api/v1/fill-plans/plan-1"), undefined);
});

test("defaults to exceptions, distinguishes system approval, shows value audit, and links to its fact", async () => {
  const targets = [
    targetFixture("system", "proposed", { approval_state: "system_approved", auto_approval_reasons: ["Direct evidence match"] }),
    targetFixture("unresolved", "unresolved"),
    targetFixture("review", "proposed", { approval_state: "needs_review", fact_id: "fact-linked", evidence_fact_ids: ["fact-linked", "fact-2"], evidence_sources: [{ fact_id: "fact-linked", snapshot_id: "snapshot-1", artifact_id: "source-1" }, { fact_id: "fact-2", snapshot_id: "snapshot-2", artifact_id: "source-2" }], snapshot_id: "snapshot-1", source_artifact_id: "source-1" }),
    targetFixture("human", "reviewed", { approval_state: "human_approved" }),
    targetFixture("excluded", "not_applicable"),
  ];
  const linkedFact = { id: "fact-linked", key: "applicant.name", label: "Linked applicant name", value: "Linked value", raw_value: "Linked value", value_type: "text", entity_id: "applicant", entity_role: "applicant", provenance: [{ kind: "pdf_rect", artifact_id: "source-1", page: 1, rect: [100, 100, 140, 120], coordinate_system: "pdf-top-left", page_width: 612, page_height: 792 }], confidence: 0.99, uncertainty: [], contradicts: [], accepted: true, semantics: [] };
  mockBaseApi((url) => url.pathname === "/api/v1/fill-plans/plan-1" ? fillPlanFixture(targets) : url.pathname === "/api/v1/evidence/snapshots/snapshot-1" ? { id: "snapshot-1", artifact_id: "source-1", run_id: "run-1", parser_provider: "test", parser_version: "1", extractor_version: "1", snapshot_sha256: "e".repeat(64), created_at: "2026-09-23T00:00:00Z", snapshot: { facts: [linkedFact], entities: [], unreadable_regions: [], parse_blocks: [], parser_provider: "test", parser_version: "1", extractor_version: "1", warnings: [], source_sha256: "f".repeat(64) } } : undefined);
  renderApp("/fill-plans/plan-1");
  expect(await screen.findByRole("heading", { name: "2 exceptions" })).toBeInTheDocument();
  const fieldList = document.querySelector(".field-scroll")!;
  expect(within(fieldList).getByRole("button", { name: /unresolved/ })).toBeInTheDocument();
  expect(within(fieldList).queryByRole("button", { name: /system/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "All fields" }));
  fireEvent.click(within(fieldList).getByRole("button", { name: /system/ }));
  expect(await screen.findByRole("heading", { name: "System approved" })).toBeInTheDocument();
  expect(screen.getByText("Automatically approved by the mapping system")).toBeInTheDocument();
  expect(screen.getByText("Direct evidence match")).toBeInTheDocument();
  const audit = document.querySelector(".candidate-card.selected .value-audit div:nth-child(2)")!;
  expect(within(audit).getByText("system-write")).toBeInTheDocument();
  expect(within(document.querySelector(".candidate-card.selected .value-audit")!).getByText("system-canonical")).toBeInTheDocument();
  fireEvent.click(within(fieldList).getByRole("button", { name: /^03 review/ }));
  expect(screen.getAllByRole("link", { name: "Open supporting fact fact-2 →" })[0]).toHaveAttribute("href", "/evidence/source-2/snapshot-2?fact=fact-2");
  fireEvent.click(screen.getAllByRole("link", { name: "Open supporting fact fact-linked →" })[0]);
  expect(await screen.findByText("Linked value", { exact: true })).toBeInTheDocument();
});

test("selects the linked fact and draws precise locations in top-left coordinates above coarse regions", async () => {
  const provenance = (rect: number[], precision?: string) => ({ kind: "pdf_rect", artifact_id: "source-1", page: 1, rect, coordinate_system: "pdf-top-left", page_width: 612, page_height: 792, precision });
  const facts = [
    { id: "fact-page", key: "coarse", label: "Page fact", value: "whole page", raw_value: "whole page", value_type: "text", entity_id: "applicant", entity_role: "applicant", provenance: [provenance([0, 0, 612, 792], "page")], confidence: 0.9, uncertainty: [], contradicts: [], accepted: true, semantics: [] },
    { id: "fact-name", key: "name", label: "Applicant name", value: "Amina", raw_value: "Amina", value_type: "text", entity_id: "applicant", entity_role: "applicant", provenance: [provenance([100, 100, 200, 120])], confidence: 0.99, uncertainty: [], contradicts: [], accepted: true, semantics: [] },
  ];
  mockBaseApi((url) => url.pathname === "/api/v1/evidence/snapshots/snapshot-1" ? { id: "snapshot-1", artifact_id: "source-1", run_id: "run-1", parser_provider: "test", parser_version: "1", extractor_version: "1", snapshot_sha256: "e".repeat(64), created_at: "2026-09-23T00:00:00Z", snapshot: { facts, entities: [], unreadable_regions: [], parse_blocks: [], parser_provider: "test", parser_version: "1", extractor_version: "1", warnings: [], source_sha256: "f".repeat(64) } } : undefined);
  renderApp("/evidence/source-1/snapshot-1?fact=fact-page");
  const coarse = await screen.findByRole("button", { name: "Page fact, location 1" });
  const precise = screen.getByRole("button", { name: "Applicant name, location 1" });
  expect(coarse).toHaveClass("coarse", "active");
  expect(coarse).toHaveStyle({ zIndex: 0 });
  expect(getComputedStyle(coarse).backgroundColor).toBe("rgba(0, 0, 0, 0)");
  expect(precise).toHaveClass("precise");
  expect(precise).toHaveStyle({ top: `${100 / 792 * 100}%`, zIndex: 1 });
  expect(getComputedStyle(precise).backgroundColor).toBe("rgba(0, 0, 0, 0)");
  fireEvent.click(precise);
  expect(await screen.findByText("Amina", { exact: true })).toBeInTheDocument();
});
