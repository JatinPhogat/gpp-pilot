"""Small, deterministic hybrid retriever for the indexed 3GPP corpus."""

from __future__ import annotations

import builtins
import logging
import os
import pickle
import re
from pathlib import Path

import chromadb
import torch

# These messages are emitted by optional models inside the installed Transformers
# package. They are not PaddleOCR execution and do not affect this application.
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "critical")
logging.getLogger("transformers").setLevel(logging.CRITICAL)
_print = builtins.print


def _quiet_optional_model_messages(*args, **kwargs):
    if args and isinstance(args[0], str) and "is part of" in args[0] and "not documented" in args[0]:
        return
    _print(*args, **kwargs)


builtins.print = _quiet_optional_model_messages
try:
    from sentence_transformers import CrossEncoder, SentenceTransformer
finally:
    builtins.print = _print

from app.config import BM25_PATH, CHROMA_PATH, EMBEDDING_MODEL, RERANKER_MODEL
from app.glossary import expand_abbreviations
from app.schemas import RetrievalResult


def query_filters(question: str) -> dict:
    """Use explicit user-provided release or specification filters only."""
    filters: dict = {}
    release = re.search(r"\brel(?:ease)?\s*[- ]?(1[5-9]|2\d)\b", question, re.I)
    spec = re.search(r"\b(\d{2}\.\d{3}(?:-\d+)?)\b", question)
    if spec:
        filters["spec_id"] = spec.group(1)
    elif release:
        filters["release"] = int(release.group(1))
    return filters


def is_scope_question(question: str) -> str | None:
    """Find a request for the purpose/scope of a named specification."""
    match = re.search(
        r"\b(?:purpose|scope|overview|about|function)\s+(?:of\s+)?ts\s*(\d{2}\.\d{3}(?:-\d+)?)\b",
        question,
        re.I,
    )
    return match.group(1) if match else None


def usable(chunk: dict) -> bool:
    """Exclude front matter while preserving each document's title chunk."""
    return bool(chunk.get("clause_id") and chunk["clause_id"] not in {"front", "legal"}) or chunk.get("chunk_index") == 1


class HybridRetriever:
    """Dense + BM25 RRF retrieval followed by a cross-encoder rerank."""

    def __init__(self, chroma_path: Path = CHROMA_PATH, bm25_path: Path = BM25_PATH):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.embedder = SentenceTransformer(EMBEDDING_MODEL, device=self.device)
        self.reranker = CrossEncoder(RERANKER_MODEL, device=self.device)
        self.collection = chromadb.PersistentClient(path=str(chroma_path)).get_collection("gpp_rel18")
        with bm25_path.open("rb") as stream:
            data = pickle.load(stream)
        self.chunks: list[dict] = data["chunks"]
        self.bm25 = data["bm25"]
        self.by_id = {chunk["chunk_id"]: chunk for chunk in self.chunks}

    def retrieve(self, question: str, top_k: int = 5, candidate_k: int = 80) -> list[RetrievalResult]:
        filters = query_filters(question)
        retrieval_text = expand_abbreviations(question)

        embedding = self.embedder.encode(retrieval_text, normalize_embeddings=True).tolist()
        dense = self.collection.query(
            query_embeddings=[embedding],
            n_results=min(candidate_k, len(self.chunks)),
            where=filters or None,
            include=["distances"],
        )
        dense_ids = dense["ids"][0]

        allowed_positions = [
            index for index, chunk in enumerate(self.chunks)
            if usable(chunk) and all(chunk.get(key) == value for key, value in filters.items())
        ]
        if not allowed_positions:
            allowed_positions = [index for index, chunk in enumerate(self.chunks) if usable(chunk)]

        bm25_scores = self.bm25.get_scores(retrieval_text.lower().split())
        bm25_ids = [
            self.chunks[index]["chunk_id"]
            for index in sorted(allowed_positions, key=lambda index: bm25_scores[index], reverse=True)[:candidate_k]
        ]

        fused: dict[str, RetrievalResult] = {}
        for rank, chunk_id in enumerate(dense_ids, start=1):
            chunk = self.by_id.get(chunk_id)
            if chunk and usable(chunk):
                fused[chunk_id] = RetrievalResult(chunk=chunk, score=1 / (60 + rank), dense_rank=rank)
        for rank, chunk_id in enumerate(bm25_ids, start=1):
            chunk = self.by_id[chunk_id]
            item = fused.setdefault(chunk_id, RetrievalResult(chunk=chunk, score=0.0))
            item.score += 1 / (60 + rank)
            item.bm25_rank = rank

        # A title is direct evidence for an explicit document-purpose question.
        scope_spec = is_scope_question(question)
        title_chunk_id = None
        if scope_spec:
            for chunk in self.chunks:
                if chunk.get("spec_id") == scope_spec and chunk.get("chunk_index") == 1:
                    item = fused.setdefault(chunk["chunk_id"], RetrievalResult(chunk=chunk, score=0.0))
                    item.score += 0.5
                    title_chunk_id = chunk["chunk_id"]
                    break

        candidates = sorted(fused.values(), key=lambda item: item.score, reverse=True)[:candidate_k]
        if not candidates:
            return []
        scores = self.reranker.predict([(question, item.chunk["text"]) for item in candidates])
        for item, score in zip(candidates, scores):
            item.rerank_score = float(score)
        ranked = sorted(candidates, key=lambda item: item.rerank_score if item.rerank_score is not None else float("-inf"), reverse=True)

        # Keep the document title as direct evidence when the user explicitly asks
        # what that document is for; the remaining results stay reranker-ranked.
        selected = []
        if title_chunk_id and title_chunk_id in fused:
            selected.append(fused[title_chunk_id])
        selected.extend(item for item in ranked if item.chunk["chunk_id"] != title_chunk_id)
        selected = selected[:top_k]

        labels = ", ".join(
            f"TS {item.chunk['spec_id']} §{item.chunk.get('clause_id') or 'title'}"
            for item in selected
        )
        print(f"[RAG] Retrieved {len(selected)} chunks: {labels}", flush=True)
        return selected
