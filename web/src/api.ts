import type { Agency, Artifact, CaseSummary, Draft, EvidenceSnapshot, EvidenceSource, FormFill, FormFillSummary, PageAnnotation, ReviewStyle, Run, TemplateSchema, TemplateVersion } from "./types";

export const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    const message = detail.detail;
    if (message && typeof message === "object" && "field_id" in message && "error" in message) {
      throw new Error(`${message.field_id}: ${message.error}`);
    }
    throw new Error(typeof message === "string" ? message : "Request failed");
  }
  return response.json() as Promise<T>;
}

export const api = {
  listArtifacts: () => request<Artifact[]>("/api/v1/artifacts"),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Artifact>("/api/v1/artifacts", { method: "POST", body });
  },
  listEvidenceSources: () => request<EvidenceSource[]>("/api/v1/evidence/sources"),
  uploadEvidenceSource: (file: File, caseKey?: string) => {
    const body = new FormData();
    body.append("file", file);
    const query = caseKey ? `?case_key=${encodeURIComponent(caseKey)}` : "";
    return request<EvidenceSource>(`/api/v1/evidence/sources${query}`, { method: "POST", body });
  },
  evidenceSnapshot: (id: string) => request<EvidenceSnapshot>(`/api/v1/evidence/snapshots/${id}`),
  run: (id: string) => request<Run>(`/api/v1/processing-runs/${id}`),
  draft: (id: string) => request<Draft>(`/api/v1/templates/${id}`),
  updateDraft: (id: string, revision: number, name: string, schema: TemplateSchema) =>
    request<Draft>(`/api/v1/templates/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: revision, name, schema }),
    }),
  publish: (id: string, revision: number) =>
    request<{ version: number }>(`/api/v1/templates/${id}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: revision }),
    }),
  versions: (draftId: string) => request<TemplateVersion[]>(`/api/v1/templates/${draftId}/versions`),
  listFormFills: () => request<FormFillSummary[]>("/api/v1/form-fills"),
  formFill: (id: string) => request<FormFill>(`/api/v1/form-fills/${id}`),
  createFormFill: (caseKey: string, templateVersionId: string, agencyKey: string) =>
    request<Run>("/api/v1/form-fills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case_key: caseKey, template_version_id: templateVersionId, agency_key: agencyKey }),
    }),
  listAgencies: () => request<Agency[]>("/api/v1/agencies"),
  updateAgency: (key: string, details: Record<string, string>) =>
    request<Agency>(`/api/v1/agencies/${encodeURIComponent(key)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ details }),
    }),
  listCases: () => request<CaseSummary[]>("/api/v1/cases"),
  updateFormFillField: (id: string, fieldId: string, input: { write_value?: unknown; evidence_fact_ids?: string[]; geometry?: { page: number; rect: [number, number, number, number]; coordinate_system: "normalized-top-left" }; style?: ReviewStyle; review_status?: "accepted" | "needs_review" }) =>
    request<FormFill>(`/api/v1/form-fills/${id}/fields/${encodeURIComponent(fieldId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  updateFormFillGeometry: (id: string, fieldId: string, geometry: { page: number; rect: [number, number, number, number]; coordinate_system: "normalized-top-left" }) =>
    request<FormFill>(`/api/v1/form-fills/${id}/fields/${encodeURIComponent(fieldId)}/geometry`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ geometry }),
    }),
  updateFormAnnotations: (id: string, annotations: PageAnnotation[]) => request<FormFill>(`/api/v1/form-fills/${id}/annotations`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ annotations }),
  }),
  approveAndExport: (id: string) => request<FormFill>(`/api/v1/form-fills/${id}/approve-and-export`, { method: "POST" }),
  formFillOutput: (id: string) => `${API}/api/v1/form-fills/${id}/output`,
  grid: (artifactId: string, sheet: string, row = 1, column = 1) => {
    const minRow = Math.max(1, row - 8);
    const minCol = Math.max(1, column - 3);
    return request<{ rows: GridCell[][] }>(`/api/v1/artifacts/${artifactId}/sheets/${encodeURIComponent(sheet)}/grid?min_row=${minRow}&max_row=${minRow + 79}&min_col=${minCol}&max_col=${minCol + 29}`);
  },
};

export interface GridCell {
  coordinate: string;
  value: string | number | boolean | null;
  formula: boolean;
  locked: boolean;
  number_format: string;
  fill?: string | null;
}
