"""Pydantic schemas for API requests and responses."""
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    """User registration request."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=255)


class UserLogin(BaseModel):
    """User login request."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """User response."""

    id: uuid.UUID
    email: str
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class MessageTurn(BaseModel):
    """Single message in a chat turn."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatAttachment(BaseModel):
    """Attachment payload for rich chat inputs."""

    type: Literal["image", "video", "table", "formula", "code", "pdf"]
    name: Optional[str] = Field(default=None, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=255)
    text_content: Optional[str] = Field(default=None)
    data_url: Optional[str] = Field(default=None, max_length=6000000)
    language: Optional[str] = Field(default=None, max_length=50)


class MessageResponse(BaseModel):
    """Message response."""

    id: uuid.UUID
    thread_id: uuid.UUID
    role: str
    content: str
    attachments: list[ChatAttachment] = Field(default_factory=list)
    created_at: datetime

    class Config:
        from_attributes = True


class ThreadResponse(BaseModel):
    """Thread response with messages."""

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse]

    class Config:
        from_attributes = True


class ThreadListResponse(BaseModel):
    """Thread list response (without messages)."""

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    """Chat message request."""

    message: str = Field(min_length=1, max_length=8000)
    history: list[MessageTurn] = Field(default_factory=list)
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatResponse(BaseModel):
    """Chat response."""

    answer: str
    assistant_attachments: list[ChatAttachment] = Field(default_factory=list)
    message_id: Optional[uuid.UUID] = None
    thread_title: Optional[str] = None


class QueryDbRequest(BaseModel):
    """Natural language to SQL request for Query DB tool."""

    database_url: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    connection_id: Optional[str] = Field(default=None, min_length=1, max_length=64)
    question: str = Field(min_length=1, max_length=4000)


class QueryDbResponse(BaseModel):
    """Natural language SQL answer response."""

    answer: str
    sql: str
    row_count: int
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class SaveDbConnectionRequest(BaseModel):
    """Request payload to store a DB connection on server side."""

    label: str = Field(min_length=1, max_length=100)
    database_url: str = Field(min_length=1, max_length=2048)
    thread_id: Optional[uuid.UUID] = None


class DbConnectionResponse(BaseModel):
    """Stored DB connection metadata (never includes full URL)."""

    id: str
    label: str
    masked_url: str
    thread_id: Optional[uuid.UUID] = None
    updated_at: datetime


class GoogleSheetLoadRequest(BaseModel):
    """Load a Google Sheet into tabular preview data."""

    sheet_url_or_id: str = Field(min_length=1, max_length=4096)


class GoogleSheetLoadResponse(BaseModel):
    """Google Sheet preview response."""

    row_count: int
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class GoogleSheetQuestionRequest(BaseModel):
    """Ask a natural-language question against a Google Sheet."""

    sheet_url_or_id: str = Field(min_length=1, max_length=4096)
    question: str = Field(min_length=1, max_length=4000)


class GoogleSheetQuestionResponse(BaseModel):
    """Answer payload for Google Sheet natural-language questions."""

    answer: str
    row_count: int
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class DataframeQueryRequest(BaseModel):
    """Request for CSV/XLSX/Google Sheet dataframe question answering."""

    source_type: Literal["csv", "xlsx", "google_sheet"]
    source: str = Field(min_length=1, max_length=4096)
    question: str = Field(min_length=1, max_length=4000)
    worksheet_name: Optional[str] = Field(default=None, max_length=255)
    worksheet_index: Optional[int] = Field(default=None, ge=0)
    preview_rows: int = Field(default=20, ge=1, le=100)
    include_table: bool = True
    history: list[MessageTurn] = Field(default_factory=list)


class DataframeQueryResponse(BaseModel):
    """Response for dataframe question answering."""

    answer: str
    source_type: str
    row_count: int
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    intermediate_steps: list[str] = Field(default_factory=list)


class ResearchDigestRequest(BaseModel):
    """Request for iterative research digest generation over arXiv."""

    topic: str = Field(min_length=3, max_length=300)
    max_iterations: int = Field(default=3, ge=1, le=8)
    papers_per_iteration: int = Field(default=4, ge=1, le=10)


class ResearchDigestCitation(BaseModel):
    """Citation metadata for a paper used in the digest."""

    citation_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    published: str
    year: Optional[int] = None
    arxiv_id: str
    doi: Optional[str] = None
    pdf_url: str
    source_type: Literal["arxiv"] = "arxiv"
    relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_spans: list[str] = Field(default_factory=list)
    apa_citation: str = ""
    ieee_citation: str = ""
    bibtex_entry: str = ""
    csl_json: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""


class ResearchDigestVisualization(BaseModel):
    """Simple chart-ready visualization payload."""

    title: str
    kind: Literal["bar"] = "bar"
    labels: list[str] = Field(default_factory=list)
    values: list[int] = Field(default_factory=list)


class ResearchDigestResult(BaseModel):
    """Structured digest generated by the agent."""

    topic: str
    executive_summary: str
    key_findings: list[str] = Field(default_factory=list)
    evidence_assessment: str
    methodology_notes: str
    limitations: list[str] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)
    citations: list[ResearchDigestCitation] = Field(default_factory=list)
    visualizations: list[ResearchDigestVisualization] = Field(default_factory=list)
    iterations_used: int
    stopped_reason: str
    generated_at: datetime


class ResearchDigestProgressEvent(BaseModel):
    """SSE progress event emitted while the research digest is running."""

    phase: Literal[
        "agent",
        "search",
        "read_pdf",
        "summarize",
        "reflect",
        "finalize",
        "complete",
        "error",
    ]
    message: str
    iteration: int = Field(default=0, ge=0)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    details: dict[str, Any] = Field(default_factory=dict)


class ResearchDigestPdfExportRequest(BaseModel):
    """Request to export a generated digest as PDF."""

    digest: ResearchDigestResult
