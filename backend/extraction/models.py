from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Word(Model):
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None


class Page(Model):
    number: int
    width: float
    height: float
    method: Literal['native_text', 'ocr', 'unread']
    state: Literal['readable', 'partial', 'unreadable']
    words: list[Word] = Field(default_factory=list)
    image_path: str | None = None
    image_size: tuple[int, int] | None = None
    image_to_page_scale: tuple[float, float] | None = None
    native_text: str = ''
    native_words: list[Word] = Field(default_factory=list)
    table_cells: list[tuple[float, float, float, float]] = Field(default_factory=list)
    fallback_reason: str | None = None
    errors: list[str] = Field(default_factory=list)
    coordinate_system: Literal['points_top_left'] = 'points_top_left'


class Document(Model):
    document_id: str
    kind: Literal['ledger', 'move_out_statement', 'lease', 'renewal']
    sha256: str | None
    original_path: str | None
    source: dict = Field(default_factory=dict)
    pages: list[Page] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    tools: dict[str, str] = Field(default_factory=dict)


class Citation(Model):
    document_id: str
    sha256: str
    page: int
    quote: str
    bbox: tuple[float, float, float, float]
    method: Literal['native_text', 'ocr']
    coordinate_system: Literal['points_top_left'] = 'points_top_left'


class CellCitation(Model):
    kind: Literal['source_cell'] = 'source_cell'
    source_id: str
    source_version: str | None
    row: int = Field(ge=1)
    column: Literal['Amount of Claim'] = 'Amount of Claim'
    quote: str


class Observation(Model):
    raw_value: str
    value: str | None
    citations: list[Citation | CellCitation] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    review_status: Literal['extracted'] = 'extracted'


class Fact(Model):
    state: Literal['present', 'missing', 'conflicting', 'unreadable', 'unreviewed']
    observations: list[Observation] = Field(default_factory=list)
    reason: str | None = None


class FinancialEntry(Model):
    entry_id: str
    kind: Literal['charge', 'payment', 'credit', 'deposit', 'reversal', 'unclassified']
    category: str
    description: str
    posting_date: Fact
    service_period: Fact
    amount: Fact
    running_balance: Fact
    citations: list[Citation]
    source_columns: dict[str, str] = Field(default_factory=dict)


class RentTerm(Model):
    start: Fact
    end: Fact
    monthly_rent: Fact
    frequency: Literal['monthly'] = 'monthly'


class DocumentFacts(Model):
    document: Document
    fields: dict[str, Fact]
    entries: list[FinancialEntry] = Field(default_factory=list)
    rent_terms: list[RentTerm] = Field(default_factory=list)
    clauses: list[Observation] = Field(default_factory=list)
    unparsed: list[Citation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DocumentInput(Model):
    path: str
    document_id: str = Field(min_length=1)
    kind: Literal['ledger', 'move_out_statement', 'lease', 'renewal']
    source: dict = Field(default_factory=dict)
    force_ocr_pages: list[int] = Field(default_factory=list)

    @model_validator(mode='after')
    def positive_pages(self):
        if any(number < 1 for number in self.force_ocr_pages):
            raise ValueError('force_ocr_pages uses one-based positive page numbers')
        return self


class Manifest(Model):
    claim_id: str = Field(min_length=1)
    documents: list[DocumentInput] = Field(min_length=1)
    effective_on: str | None = None
    recorded_requests: list[CellCitation] = Field(default_factory=list)

    @model_validator(mode='after')
    def unique_documents(self):
        ids = [doc.document_id for doc in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError('document_id must be unique within a claim')
        if self.effective_on:
            from datetime import date
            self.effective_on = date.fromisoformat(self.effective_on).isoformat()
        return self
