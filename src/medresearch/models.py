from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["none", "ollama", "external"]
State = Literal["queued", "running", "awaiting_review", "completed", "failed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(StrictModel):
    number: int = Field(ge=1)
    text: str = Field(max_length=200_000)
    method: Literal["text", "ocr", "manual"] = "text"
    warnings: list[str] = Field(default_factory=list)


class Article(StrictModel):
    pmid: str
    doi: str | None = None
    title: str
    date: str
    publication_types: list[str]
    abstract: str
    availability: str
    url: str


class Finding(StrictModel):
    doc_ref: str
    document_quote: str = Field(min_length=8, max_length=2000)
    pmid: str
    evidence_quote: str = Field(min_length=8, max_length=2000)


class Conflict(StrictModel):
    left_ref: str
    left_quote: str = Field(min_length=8, max_length=2000)
    right_ref: str
    right_quote: str = Field(min_length=8, max_length=2000)


class Analysis(StrictModel):
    findings: list[Finding] = Field(default_factory=list, max_length=30)
    conflicts: list[Conflict] = Field(default_factory=list, max_length=30)


class ReviewRequest(StrictModel):
    query: str = Field(min_length=2, max_length=1000)
    provider: Provider = "ollama"
    limit: int = Field(default=10, ge=1, le=50)


class ResearchRequest(StrictModel):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
