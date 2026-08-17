from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "rel18"
NORMALIZED = ROOT / "data" / "normalized" / "rel18"
PROCESSED = ROOT / "data" / "processed" / "rel18"
ASSETS = ROOT / "data" / "assets" / "rel18"
INDEXES = ROOT / "data" / "indexes"
CANONICAL_MANIFEST = RAW / "manifest.canonical.json"
CONVERSION_MANIFEST = NORMALIZED / "manifest.conversion.json"
CHUNKS_PATH = PROCESSED / "chunks.jsonl"
INGESTION_REPORT = PROCESSED / "ingestion_report.json"
CHROMA_PATH = INDEXES / "chroma"
BM25_PATH = INDEXES / "bm25" / "bm25.pkl"

# Match the already-built Chroma index (bge-small, 384-d).
# Upgrade to BAAI/bge-m3 + BAAI/bge-reranker-v2-m3 later when you can re-index overnight.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL = "BAAI/bge-reranker-base"

