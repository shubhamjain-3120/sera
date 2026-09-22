export type FieldType = "text" | "number" | "date" | "boolean" | "choice" | "signature" | "action" | "unknown";

export interface Location {
  kind: "pdf_rect" | "xlsx_range";
  page?: number;
  rect?: number[];
  rotation?: number;
  page_width?: number;
  page_height?: number;
  sheet?: string;
  cell_range?: string;
}

export interface TemplateField {
  id: string;
  label: string;
  field_type: FieldType;
  semantic_type?: string | null;
  required?: boolean | null;
  writable: boolean;
  location: Location;
  native_name?: string | null;
  options: string[];
  current_value?: unknown;
  repeating_group_id?: string | null;
  notes?: string | null;
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
  result?: TemplateSchema;
  error?: string;
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
