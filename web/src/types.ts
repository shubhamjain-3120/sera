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
  native_full_name?: string | null;
  native_object_id?: string | null;
  options: string[];
  choice_options?: Array<{ export_value: string; display_value: string }>;
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
  constraints?: Record<string, unknown>;
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

export interface Agency {
  key: string;
  name: string;
  details: Record<string, string>;
  is_default: boolean;
}

export interface CaseSummary {
  case_key: string;
  source_count: number;
  snapshot_count: number;
  created_at: string;
}

export interface FormFillSnippet { text: string; artifact_id?: string; snapshot_id?: string; page?: number; rect?: number[]; sheet?: string; cell_range?: string }
export interface FormFillField { field: TemplateField; write_value: unknown; origin?: "model" | "human" | "prefilled"; evidence_fact_ids?: string[]; source_block_ids?: string[]; assumption?: string | null; snippets?: FormFillSnippet[] }
export interface FormFillEvidence { fact: EvidenceFact; snapshot_id?: string; artifact_id?: string; used: boolean; field_ids: string[] }
export interface FormFillSummary { id: string; case_key: string; template_version_id: string; template_name: string; target_artifact_id: string; target_kind: "pdf" | "xlsx"; status: string; output_available?: boolean; output_error?: string | null; created_at?: string; updated_at?: string }
export interface FormFill extends FormFillSummary { agency_key?: string; fields: FormFillField[]; evidence: FormFillEvidence[]; output_path?: string | null }
