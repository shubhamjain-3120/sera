from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Location(BaseModel):
    kind: Literal["pdf_rect", "xlsx_range"]
    page: int | None = None
    rect: list[float] | None = None
    rotation: int | None = None
    page_width: float | None = None
    page_height: float | None = None
    sheet: str | None = None
    cell_range: str | None = None


class LabelEvidence(BaseModel):
    text: str
    page: int
    rect: list[float]
    coordinate_system: str
    source: Literal["native-text", "reducto"]
    relation: str


class WidgetOption(BaseModel):
    export_value: str | None = None
    label: str | None = None
    location: Location


class ChoiceOption(BaseModel):
    """Visible choice text and the value AcroForm expects when writing it."""

    export_value: str
    display_value: str


class TemplateField(BaseModel):
    id: str
    label: str
    field_type: Literal["text", "number", "date", "boolean", "choice", "signature", "action", "unknown"] = "unknown"
    semantic_type: str | None = None
    semantic_type_origin: Literal["unknown", "rule", "model", "human"] = "unknown"
    semantic_type_confidence: float | None = Field(default=None, ge=0, le=1)
    required: bool | None = None
    writable: bool = True
    location: Location
    native_name: str | None = None
    native_full_name: str | None = None
    native_object_id: str | None = None
    options: list[str] = Field(default_factory=list)
    choice_options: list[ChoiceOption] = Field(default_factory=list)
    current_value: Any = None
    repeating_group_id: str | None = None
    notes: str | None = None
    label_origin: Literal["native", "layout", "human"] = "native"
    label_confidence: float | None = Field(default=None, ge=0, le=1)
    label_evidence: list[LabelEvidence] = Field(default_factory=list)
    review_state: Literal["needs_review", "confirmed"] = "needs_review"
    widgets: list[Location] = Field(default_factory=list)
    widget_count: int = Field(default=1, ge=1)
    widget_options: list[WidgetOption] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)


class RepeatingGroup(BaseModel):
    id: str
    label: str
    field_ids: list[str] = Field(default_factory=list)
    capacity: int = Field(ge=1)


class TemplateSchema(BaseModel):
    fields: list[TemplateField] = Field(default_factory=list)
    repeating_groups: list[RepeatingGroup] = Field(default_factory=list)
    inspection: dict[str, Any] = Field(default_factory=dict)


class ArtifactResponse(BaseModel):
    id: str
    filename: str
    media_type: str
    kind: str
    sha256: str
    size_bytes: int
    created_at: datetime
    run_id: str
    draft_id: str


class EvidenceSourceResponse(BaseModel):
    id: str
    filename: str
    media_type: str
    kind: str
    purpose: str
    case_key: str | None
    sha256: str
    size_bytes: int
    created_at: datetime
    run_id: str
    snapshot_id: str | None = None


class EvidenceLocation(BaseModel):
    kind: Literal["pdf_rect", "image_rect", "xlsx_range", "text_span"]
    artifact_id: str
    page: int | None = None
    rect: list[float] | None = None
    coordinate_system: str | None = None
    rotation: int | None = None
    rotation_transform: list[float] | None = None
    page_width: float | None = None
    page_height: float | None = None
    precision: str | None = None
    sheet: str | None = None
    cell_range: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    excerpt: str | None = None


class EvidenceFact(BaseModel):
    id: str
    key: str
    label: str
    value: Any
    raw_value: str
    value_type: str = "text"
    entity_id: str
    entity_role: str
    provenance: list[EvidenceLocation]
    source_block_ids: list[str] = Field(default_factory=list)
    model_extraction: dict[str, Any] | None = None
    confidence: float = Field(ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list)
    unit: str | None = None
    date_context: str | None = None
    period_context: str | None = None
    duplicate_of: str | None = None
    contradicts: list[str] = Field(default_factory=list)
    accepted: bool = True
    semantics: list[str] = Field(default_factory=list)


class UnreadableRegion(BaseModel):
    id: str
    reason: str
    provenance: EvidenceLocation
    confidence: float = Field(ge=0, le=1)


class EvidenceSnapshotPayload(BaseModel):
    facts: list[EvidenceFact] = Field(default_factory=list)
    entities: list[dict[str, Any]] = Field(default_factory=list)
    unreadable_regions: list[UnreadableRegion] = Field(default_factory=list)
    parse_blocks: list[dict[str, Any]] = Field(default_factory=list)
    parser_provider: str
    parser_version: str
    extractor_version: str
    warnings: list[str] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    document_instructions: list[dict[str, Any]] = Field(default_factory=list)
    model_extraction: dict[str, Any] | None = None
    source_sha256: str


class EvidenceSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    artifact_id: str
    run_id: str
    parser_provider: str
    parser_version: str
    extractor_version: str
    snapshot_sha256: str
    snapshot: EvidenceSnapshotPayload
    created_at: datetime


class RunResponse(BaseModel):
    id: str
    artifact_id: str
    status: str
    stage: str
    progress: int
    provider: str
    provider_job_id: str | None
    parser_version: str
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    artifact_id: str
    revision: int
    name: str
    template_schema: TemplateSchema = Field(alias="schema")
    created_at: datetime
    updated_at: datetime


class DraftUpdate(BaseModel):
    expected_revision: int
    name: str | None = None
    template_schema: TemplateSchema = Field(alias="schema")


class VersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    draft_id: str
    version: int
    name: str
    template_schema: TemplateSchema = Field(alias="schema")
    source_revision: int
    schema_sha256: str
    published_at: datetime


class PublishRequest(BaseModel):
    expected_revision: int


class FormFillCreate(BaseModel):
    case_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    template_version_id: str
    evidence_snapshot_ids: list[str] | None = None
    agency_key: str | None = None


class FormFieldUpdate(BaseModel):
    write_value: Any = None
    evidence_fact_ids: list[str] | None = None
    geometry: dict[str, Any] | None = None


class FormFieldGeometryUpdate(BaseModel):
    geometry: dict[str, Any]


class FormFillResponse(BaseModel):
    id: str
    case_key: str
    template_version_id: str
    evidence_snapshot_ids: list[str]
    agency_key: str | None = None
    answers: list[dict[str, Any]]
    mapping_metadata: dict[str, Any] = Field(default_factory=dict)
    state: str
    model_execution_id: str | None = None
    error: str | None = None
    output_filename: str | None = None
    output_media_type: str | None = None
    output_sha256: str | None = None
    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None
    template_name: str = ""
    target_artifact_id: str = ""
    target_kind: str = ""
    status: str = "mapped"
    output_available: bool = False
    output_error: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class FormFillRunResponse(RunResponse):
    form_fill_id: str


class AgencyResponse(BaseModel):
    key: str
    name: str
    details: dict[str, str]
    is_default: bool


class AgencyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    details: dict[str, str]


class CaseResponse(BaseModel):
    case_key: str
    source_count: int
    snapshot_count: int
    created_at: datetime
