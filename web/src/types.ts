export type FieldType = "text" | "number" | "date" | "boolean" | "choice" | "signature" | "action" | "unknown";

export interface Location {
  kind: "pdf_rect" | "xlsx_range";
  page?: number;
  rect?: number[];
  rotation?: number;
  page_width?: number;
  page_height?: number;
  coordinate_system?: string;
  sheet?: string;
  cell_range?: string;
}

export interface TemplateField {
  id: string;
  label: string;
  field_type: FieldType;
  semantic_type?: string | null;
  semantic_type_origin?: "unknown" | "rule" | "model" | "human";
  semantic_type_confidence?: number | null;
  required?: boolean | null;
  writable: boolean;
  location: Location;
  native_name?: string | null;
  options: string[];
  current_value?: unknown;
  repeating_group_id?: string | null;
  notes?: string | null;
  label_origin?: "native" | "layout" | "human";
  label_confidence?: number | null;
  label_evidence?: Array<{ text: string; page: number; rect: number[]; coordinate_system: string; source: "native-text" | "reducto"; relation: string }>;
  review_state?: "needs_review" | "confirmed";
  widgets?: Location[];
  widget_count?: number;
  widget_options?: Array<{ export_value?: string | null; label?: string | null; location: Location }>;
}

export interface TemplateSchema {
  fields: TemplateField[];
  repeating_groups: Array<{ id: string; label: string; field_ids: string[]; capacity: number }>;
  inspection: Record<string, unknown>;
}

export interface Artifact {
  id: string;
  filename: string;
  media_type: string;
  kind: "pdf" | "xlsx";
  sha256: string;
  size_bytes: number;
  created_at: string;
  run_id: string;
  draft_id: string;
}

export interface Run {
  id: string;
  artifact_id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  progress: number;
  provider: string;
  result?: Record<string, unknown>;
  error?: string;
}

export type EvidenceLocationKind = "pdf_rect" | "image_rect" | "xlsx_range" | "text_span";

export interface EvidenceLocation {
  kind: EvidenceLocationKind;
  artifact_id: string;
  page?: number;
  rect?: number[];
  coordinate_system?: string;
  rotation?: number;
  rotation_transform?: number[];
  page_width?: number;
  page_height?: number;
  precision?: string;
  sheet?: string;
  cell_range?: string;
  char_start?: number;
  char_end?: number;
  excerpt?: string;
}

export interface EvidenceFact {
  id: string;
  key: string;
  label: string;
  value: unknown;
  raw_value: string;
  value_type: string;
  entity_id: string;
  entity_role: string;
  provenance: EvidenceLocation[];
  confidence: number;
  uncertainty: string[];
  unit?: string | null;
  date_context?: string | null;
  period_context?: string | null;
  duplicate_of?: string | null;
  contradicts: string[];
  accepted: boolean;
  semantics: string[];
}

export interface EvidenceSnapshot {
  id: string;
  artifact_id: string;
  run_id: string;
  parser_provider: string;
  parser_version: string;
  extractor_version: string;
  snapshot_sha256: string;
  created_at: string;
  snapshot: {
    facts: EvidenceFact[];
    entities: Array<{ id: string; role: string }>;
    unreadable_regions: Array<{ id: string; reason: string; provenance: EvidenceLocation; confidence: number }>;
    parse_blocks: Array<{ type: string; text: string; source: EvidenceLocation }>;
    parser_provider: string;
    parser_version: string;
    extractor_version: string;
    warnings: string[];
    source_sha256: string;
  };
}

export interface EvidenceSource {
  id: string;
  filename: string;
  media_type: string;
  kind: "pdf" | "xlsx" | "image" | "text";
  purpose: "source";
  case_key?: string | null;
  sha256: string;
  size_bytes: number;
  created_at: string;
  run_id: string;
  snapshot_id?: string | null;
}

export interface Draft {
  id: string;
  artifact_id: string;
  revision: number;
  name: string;
  schema: TemplateSchema;
  created_at: string;
  updated_at: string;
}

export interface TemplateVersion {
  id: string;
  draft_id: string;
  version: number;
  name: string;
  schema: TemplateSchema;
  source_revision: number;
  schema_sha256: string;
  published_at: string;
}

export interface MappingIssue {
  id: string;
  code: string;
  severity: "info" | "review" | "blocker";
  target_id: string;
  message: string;
}

export interface MappingCandidate {
  id: string;
  target_field_id: string;
  snapshot_id?: string | null;
  source_artifact_id?: string | null;
  fact_id?: string | null;
  fact_key?: string | null;
  entity_id?: string | null;
  entity_role?: string | null;
  value: unknown;
  raw_value: string;
  value_type: string;
  unit?: string | null;
  date_context?: string | null;
  period_context?: string | null;
  origin: "evidence" | "derivation";
  resolution: "direct" | "normalized" | "derived";
  evidence_confidence: number;
  match_score: number;
  provenance: EvidenceLocation[];
  uncertainty: string[];
  contradicts: string[];
  selectable: boolean;
  review_required: boolean;
  derivation?: { id: string; operation: string; input_ids: string[]; depth: number; as_of_date?: string | null };
}

export interface FillPlanTarget {
  field: TemplateField;
  selected_candidate_id?: string | null;
  candidates: MappingCandidate[];
  issues: MappingIssue[];
  state: "proposed" | "unresolved" | "not_applicable";
}

export interface FillPlanSummary {
  id: string;
  case_key: string;
  template_version_id: string;
  template_name: string;
  target_artifact_id: string;
  target_kind: "pdf" | "xlsx";
  evidence_bundle_id: string;
  evidence_bundle_sha256: string;
  current_revision: number;
  issue_count: number;
  blocker_count: number;
  created_at: string;
  updated_at: string;
}

export interface FillPlan extends FillPlanSummary {
  revision_id: string;
  mapper_version: string;
  payload_sha256: string;
  payload: {
    mapper_version: string;
    template_schema: TemplateSchema;
    targets: FillPlanTarget[];
    repeating_groups: Array<{ id: string; label: string; capacity: number; entity_ids: string[]; assigned_entity_ids: string[]; overflow_entity_ids: string[] }>;
    derivations: Array<Record<string, unknown>>;
    issues: MappingIssue[];
    summary: { target_count: number; proposed_count: number; unresolved_count: number; issue_count: number; blocker_count: number };
    evidence_bundle: { id: string; case_key: string; snapshot_ids: string[]; sha256: string };
  };
}
