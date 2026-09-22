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
    options: list[str] = Field(default_factory=list)
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
