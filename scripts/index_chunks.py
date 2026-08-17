"""Build persistent Chroma dense and local BM25 indexes from chunks.jsonl."""

from __future__ import annotations

import argparse
import json
import pickle
import shutil

import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer
import torch

from app.config import BM25_PATH, CHROMA_PATH, CHUNKS_PATH, EMBEDDING_MODEL


def tokenize(text: str) -> list[str]:
    return text.lower().split()


def metadata(chunk: dict) -> dict:
    """Store only filter/citation fields in Chroma metadata."""
    keep = {"document_id", "release", "series", "spec_id", "clause_id", "content_type"}
    return {key: value for key, value in chunk.items() if key in keep and value is not None}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", default=str(CHUNKS_PATH))
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    chunks = [json.loads(line) for line in open(args.chunks, encoding="utf-8")]
    if args.reset and CHROMA_PATH.exists():
        shutil.rmtree(CHROMA_PATH)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Embedding device: {device}")
    print(f"Embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL, device=device)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    collection = client.get_or_create_collection("gpp_rel18", metadata={"embedding_model": EMBEDDING_MODEL})
    if args.reset:
        try:
            client.delete_collection("gpp_rel18")
        except ValueError:
            pass
        collection = client.get_or_create_collection("gpp_rel18", metadata={"embedding_model": EMBEDDING_MODEL})
    # BGE-M3 is larger than bge-small; keep batches modest on an 8 GB laptop GPU.
    batch_size = min(args.batch_size, 32) if device == "cuda" else args.batch_size
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        texts = [chunk["text"] for chunk in batch]
        embeddings = model.encode(texts, normalize_embeddings=True, batch_size=batch_size).tolist()
        collection.upsert(ids=[chunk["chunk_id"] for chunk in batch], documents=texts, metadatas=[metadata(chunk) for chunk in batch], embeddings=embeddings)
        done = min(start + batch_size, len(chunks))
        if done == len(chunks) or done % 512 == 0 or start == 0:
            print(f"Embedded {done}/{len(chunks)} chunks", flush=True)
    BM25_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BM25_PATH.open("wb") as stream:
        pickle.dump({"chunks": chunks, "bm25": BM25Okapi([tokenize(chunk["text"]) for chunk in chunks])}, stream)
    print(f"Indexed {len(chunks)} chunks")


if __name__ == "__main__":
    main()
