import type { Agency, Artifact, CaseSummary, Draft, EvidenceSnapshot, EvidenceSource, FillPlan, FillPlanSummary, ReviewAction, ReviewDecision, Run, TemplateSchema, TemplateVersion, VerificationReport } from "./types";

export const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(detail.detail ?? "Request failed");
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
  listFillPlans: () => request<FillPlanSummary[]>("/api/v1/fill-plans"),
  fillPlan: (id: string) => request<FillPlan>(`/api/v1/fill-plans/${id}`),
  createFillPlan: (caseKey: string, templateVersionId: string, agencyKey: string) =>
    request<Run>("/api/v1/fill-plans", {
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
  listVerifications: (fillPlanId: string) => request<VerificationReport[]>(`/api/v1/fill-plans/${fillPlanId}/verifications`),
  verifyFillPlan: (fillPlanId: string, revision: number) => request<VerificationReport>(`/api/v1/fill-plans/${fillPlanId}/verifications?revision=${revision}`, { method: "POST" }),
  listReviewDecisions: (fillPlanId: string) => request<ReviewDecision[]>(`/api/v1/fill-plans/${fillPlanId}/review-decisions`),
  reviewFillPlan: (fillPlanId: string, input: { expected_revision: number; target_field_id: string; action: ReviewAction; actor: string; reason?: string; candidate_id?: string; value?: unknown }) =>
    request<ReviewDecision>(`/api/v1/fill-plans/${fillPlanId}/review-decisions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
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
