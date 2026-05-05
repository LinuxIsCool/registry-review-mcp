"""Pydantic schemas for Registry Review MCP.

All data models with validation for session, document, requirement, and finding structures.
"""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .base import BaseModel, ConfidenceScore

# ============================================================================
# Session Models
# ============================================================================


class DocumentSource(BaseModel):
    """Tracks a single document source (upload, path, or link)."""

    source_type: Literal["upload", "path", "link"]
    added_at: datetime
    metadata: dict  # Type-specific metadata

    # Example metadata by type:
    # upload: {"directory": "/path/to/temp", "file_count": 3}
    # path: {"path": "/absolute/path", "file_count": 4}
    # link: {"url": "https://...", "access_mode": "mirror|reference"}


class ProjectMetadata(BaseModel):
    """Metadata about the project being reviewed."""

    project_name: str = Field(min_length=1, max_length=200)
    project_id: str | None = Field(None, pattern=r"^C\d{2}-\d+$")
    crediting_period: str | None = None
    submission_date: datetime | None = None
    methodology: str = "soil-carbon-v1.2.2"
    proponent: str | None = None
    scope: str | None = None  # "farm", "meta", or None (all requirements)
    documents_path: str | None = None  # Optional for backward compatibility

    @field_validator("documents_path")
    @classmethod
    def validate_path_exists(cls, value: str | None) -> str | None:
        """Validate that the documents path exists (for backward compatibility)."""
        if value is None:
            return None
        # Convert to absolute path
        path = Path(value).absolute()
        # For backward compatibility, allow non-existent paths
        # (they will be validated when documents are actually accessed)
        if path.exists() and not path.is_dir():
            raise ValueError(f"Path is not a directory: {value}")
        return str(path)


class WorkflowProgress(BaseModel):
    """Tracks progress through the 8-stage workflow."""

    initialize: Literal["pending", "in_progress", "completed"] = "pending"
    document_discovery: Literal["pending", "in_progress", "completed"] = "pending"
    requirement_mapping: Literal["pending", "in_progress", "completed"] = "pending"
    evidence_extraction: Literal["pending", "in_progress", "completed"] = "pending"
    cross_validation: Literal["pending", "in_progress", "completed"] = "pending"
    report_generation: Literal["pending", "in_progress", "completed"] = "pending"
    human_review: Literal["pending", "in_progress", "completed"] = "pending"
    completion: Literal["pending", "in_progress", "completed"] = "pending"


class SessionStatistics(BaseModel):
    """Aggregated statistics for the review session."""

    documents_found: int = 0
    requirements_total: int = 0
    requirements_covered: int = 0
    requirements_partial: int = 0
    requirements_missing: int = 0
    validations_passed: int = 0
    validations_failed: int = 0


class Session(BaseModel):
    """Complete session state."""

    session_id: str
    created_at: datetime
    updated_at: datetime
    status: str
    project_metadata: ProjectMetadata
    document_sources: list[DocumentSource] = Field(default_factory=list)
    workflow_progress: WorkflowProgress
    statistics: SessionStatistics


# ============================================================================
# Checklist Models
# ============================================================================


class Requirement(BaseModel):
    """A single requirement from the checklist.

    The ``requirement_id`` accepts two patterns:

    * ``REQ-NNN`` — legacy v1 generic pattern, retained for backward
      compatibility with existing session fixtures.
    * ``CNN-AAA-LL-NNN`` — v2 protocol-locked pattern adopted from Becca's
      official template (e.g. ``C06-REGISTRATION-ML-001``). The first
      segment encodes the credit class, the second the workflow phase,
      and the third the methodology level.
    """

    requirement_id: str = Field(
        pattern=r"^(REQ-\d{3}|C\d{2}-[A-Z]+-[A-Z]{2}-\d{3})$"
    )
    category: str
    requirement_text: str
    source: str  # "Program Guide, Section X.Y"
    accepted_evidence: str
    mandatory: bool = True
    validation_type: Literal[
        "document_presence",
        "cross_document",
        "date_alignment",
        "structured_field",
        "manual",
    ]


class Checklist(BaseModel):
    """Complete checklist for a methodology."""

    methodology_id: str
    methodology_name: str
    version: str
    protocol: str
    program_guide_version: str
    program_guide_url: str | None = None  # Canonical published methodology page
    requirements: list[Requirement]


# ============================================================================
# Document Models
# ============================================================================


class DocumentMetadata(BaseModel):
    """Metadata extracted from a document."""

    page_count: int | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None
    file_size_bytes: int
    has_tables: bool = False
    content_hash: str | None = None  # SHA256 hash for deduplication


class Document(BaseModel):
    """A discovered and classified document."""

    document_id: str
    filename: str
    filepath: str
    classification: str
    confidence: ConfidenceScore
    classification_method: str  # "filename", "content", "manual"
    metadata: DocumentMetadata
    indexed_at: datetime


# ============================================================================
# Requirement Mapping Models
# ============================================================================


class RequirementMapping(BaseModel):
    """Mapping between a requirement and supporting documents."""

    requirement_id: str = Field(
        pattern=r"^(REQ-\d{3}|C\d{2}-[A-Z]+-[A-Z]{2}-\d{3})$"
    )
    mapped_documents: list[str] = []  # List of document_ids
    mapping_status: Literal["suggested", "confirmed", "unmapped", "manual"] = "suggested"
    confidence: ConfidenceScore | None = None
    suggested_by: Literal["agent", "manual"] = "agent"
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class MappingCollection(BaseModel):
    """Collection of all requirement mappings for a session."""

    session_id: str
    mappings: list[RequirementMapping]
    total_requirements: int
    mapped_count: int
    unmapped_count: int
    confirmed_count: int
    created_at: datetime
    updated_at: datetime
