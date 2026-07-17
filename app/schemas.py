from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    file_path: str = Field(..., description="Path to a PDF relative to project root or absolute")
    document_key: str | None = Field(
        None,
        description="Stable document identity across versions. Defaults from filename.",
    )
    version_label: str | None = Field(
        None,
        description="Human label like v1 / v2. Defaults from filename.",
    )


class DocumentOut(BaseModel):
    id: int
    document_key: str
    title: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionOut(BaseModel):
    id: int
    document_id: int
    version_label: str
    source_filename: str
    page_count: int
    created_at: datetime
    node_count: int = 0

    model_config = {"from_attributes": True}


class IngestResponse(BaseModel):
    document: DocumentOut
    version: VersionOut
    nodes_created: int
    message: str


class NodeSummary(BaseModel):
    id: int
    section_number: str
    heading: str
    level: int
    parent_id: int | None
    content_hash: str
    sort_order: int
    body_preview: str = ""

    model_config = {"from_attributes": True}


class NodeDetail(BaseModel):
    id: int
    version_id: int
    section_number: str
    heading: str
    level: int
    parent_id: int | None
    body: str
    content_hash: str
    sort_order: int
    page_start: int | None
    children: list[NodeSummary] = []

    model_config = {"from_attributes": True}


class SearchHit(BaseModel):
    id: int
    section_number: str
    heading: str
    level: int
    content_hash: str
    snippet: str


class SectionChange(BaseModel):
    section_number: str
    change_type: str  # added | removed | modified | unchanged
    heading_v1: str | None = None
    heading_v2: str | None = None
    hash_v1: str | None = None
    hash_v2: str | None = None


class CompareResponse(BaseModel):
    version_a_id: int
    version_b_id: int
    added: list[SectionChange]
    removed: list[SectionChange]
    modified: list[SectionChange]
    unchanged_count: int


class SelectionCreate(BaseModel):
    version_id: int
    node_ids: list[int] = Field(..., min_length=1)
    name: str = ""


class SelectionItemOut(BaseModel):
    node_id: int
    section_number: str
    heading: str
    content_hash: str


class SelectionOut(BaseModel):
    id: int
    version_id: int
    name: str
    created_at: datetime
    items: list[SelectionItemOut]


class GenerationCreate(BaseModel):
    selection_id: int


class QATestCase(BaseModel):
    id: str
    title: str
    precondition: str
    steps: list[str]
    expected_result: str
    priority: str = "medium"
    source_sections: list[str] = []


class GenerationOut(BaseModel):
    id: int
    selection_id: int
    version_id: int
    status: str
    model_name: str
    is_stale: bool
    stale_reason: str | None
    error_message: str | None = None
    created_at: datetime
    test_cases: list[dict[str, Any]] = []
    source_sections: list[dict[str, Any]] = []
    affected_document_version: dict[str, Any] | None = None


class StalenessOut(BaseModel):
    generation_id: int
    is_stale: bool
    reason: str | None
    compared_against_version_id: int | None
    changed_sections: list[SectionChange] = []
