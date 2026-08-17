"""Shared typed records. Stored JSON always uses ``to_dict`` output."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Chunk:
    """Minimal chunk record for citation-first RAG.

    Keep only fields needed for filtering, citations, and figure rendering.
    Section context lives inside ``text`` as a prepended heading path.
    """

    chunk_id: str
    document_id: str
    release: int
    series: str
    spec_id: str
    clause_id: str | None
    chunk_index: int
    content_type: str
    asset_ids: list[str]
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalResult:
    chunk: dict[str, Any]
    score: float
    dense_rank: int | None = None
    bm25_rank: int | None = None
    rerank_score: float | None = None


@dataclass
class Answer:
    text: str
    citations: list[str] = field(default_factory=list)
    image_paths: list[str] = field(default_factory=list)
    refused: bool = False
