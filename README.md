# GPP-Pilot

GPP-Pilot is a grounded 3GPP standards assistant. It retrieves clauses from a
local Release 18 corpus, then generates an answer only from those clauses.
The interface shows the source clauses beneath every supported answer.

> **Current prototype scope:** Release 18, indexed **38-series** specifications.
> It is not yet a complete all-series Release 18 assistant.

## What it does

- Answers questions about NR procedures, RRC messages, interfaces, and TS 38 clauses.
- Uses local dense retrieval, BM25 lexical search, and a local cross-encoder reranker.
- Generates a detailed answer with Groq only after retrieving evidence.
- Refuses when the retrieved clauses do not directly support an answer.
- Prints selected chunks in the terminal for every RAG request.

## RAG pipeline

```text
User question
      |
      v
Terminology expansion (UE, gNB, RRC, NR, ...)
      |
      v
BGE dense retrieval + BM25 lexical retrieval
      |
      v
Reciprocal Rank Fusion + local cross-encoder reranking
      |
      v
Top 5 relevant 3GPP chunks
      |
      v
Groq answer from retrieved evidence only, or refusal
      |
      v
Natural-language answer + source cards in the UI
```

## Current corpus

| Item | Current state |
| --- | --- |
| Target release | Release 18 |
| Downloaded documents | 1,826 total |
| Native DOCX files | 1,133 |
| Legacy DOC files | 693, converted once to DOCX with LibreOffice |
| Indexed prototype | 38 series: 133 documents, 56,901 chunks |
| Images | Extracted during ingestion but disabled in the current UI to avoid irrelevant visuals |

The original 3GPP documents are kept unchanged in `data/raw`. Converted files,
chunks, images, and indexes are local generated artifacts and are intentionally
ignored by Git.

## Project structure

```text
gpp-pilot/
├── app/
│   ├── config.py               # Paths and model settings
│   ├── glossary.py             # Deterministic telecom-term expansion
│   ├── retrieval.py            # Dense + BM25 + RRF + reranking
│   ├── generation.py           # Strict grounded Groq generation
│   ├── schemas.py              # Shared chunk and answer records
│   └── ui.py                   # Streamlit chat application
├── scripts/
│   ├── download_specs.py       # 3GPP downloader
│   ├── normalize_manifest.py   # Source-manifest normalization
│   ├── convert_legacy_docs.py  # DOC -> DOCX conversion
│   ├── ingest_specs.py         # Text/table/image extraction and chunking
│   ├── index_chunks.py         # Chroma + BM25 indexing
│   ├── preflight.py            # Environment checks
│   └── run_app.py              # Starts Streamlit
├── data/                       # Local source and generated RAG data (Git ignored)
│   ├── raw/rel18/              # Downloaded DOC/DOCX sources
│   ├── normalized/rel18/       # Converted legacy DOCX files
│   ├── processed/rel18/        # Chunk JSONL and ingestion report
│   ├── assets/rel18/           # Extracted embedded images
│   └── indexes/                # Chroma vector index and BM25 index
├── DECISIONS.md                # Project story and architecture decisions
├── requirements.txt            # Python dependencies
└── .env                        # Local Groq key; never committed
```

## Run the app

### 1. Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

### 2. Confirm `.env` contains your Groq key

```text
GROQ_API_KEY=your_key_here
GROQ_TEXT_MODEL=llama-3.3-70b-versatile
```

### 3. Start the application

```powershell
py -m scripts.run_app
```

Open `http://localhost:8501`.

No ingestion or indexing is needed to run the current prototype. On each
question, the terminal prints the selected evidence, for example:

```text
[RAG] Retrieved 5 chunks: TS 38.331 §5.3.3, TS 38.331 §6.2.2, ...
```

## Rebuild commands

Use these only when changing the corpus. They are **not** needed for normal app use.

```powershell
# Convert legacy .doc files once
py -m scripts.convert_legacy_docs

# Rebuild the current 38-series chunk corpus
py -m scripts.ingest_specs --series 38 --workers 5 --progress-every 15

# Rebuild Chroma and BM25 indexes; uses CUDA when available
py -m scripts.index_chunks --reset
```

## Grounding rules

1. The answer model receives only the retrieved specification excerpts.
2. It must not use outside knowledge or invent missing procedure steps.
3. It must return no answer when the excerpts are insufficient.
4. The source cards identify the exact chunks supplied to the model.

## Status and next step

The 38-series prototype is ready for demonstration and retrieval evaluation.
After validating it with realistic questions, the remaining Release 18 series
can be ingested and indexed using the same pipeline. See
[DECISIONS.md](DECISIONS.md) for the full design history and rationale.
