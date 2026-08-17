# GPP-Pilot

Citation-first RAG for 3GPP Release 18 specifications.

## Run now

The current working prototype is the indexed 38-series corpus. No conversion,
ingestion, or indexing is needed again before running it:

```powershell
py -m scripts.run_app
```

It retrieves three clauses, asks Groq to answer only from those clauses, and
shows those clauses as source cards below the answer. If the clauses do not
support an answer, it refuses. Images are disabled in this prototype so an
unrelated logo or figure can never be shown as evidence.

The system reads the downloaded 3GPP Word documents, preserves their clause structure, converts tables to Markdown, saves embedded images, and adds Groq Vision descriptions for supported raster images. It then indexes the chunks for retrieval and produces cited answers.

## Current corpus

- Release: 18
- Documents: 1,826 total
- Native DOCX: 1,133
- Legacy DOC converted to DOCX: 693
- Original downloaded documents are never modified.

## Folder structure

```text
gpp-pilot/
├── app/                         # RAG application code
│   ├── config.py                # Central paths and settings
│   ├── schemas.py               # Minimal chunk metadata (spec/clause/assets)
│   ├── glossary.py              # Telecom term expansion
│   ├── retrieval.py             # Dense + BM25 + reranking retrieval
│   ├── generation.py            # Groq answer generation and citations
│   └── ui.py                    # Streamlit interface
├── scripts/                     # One-command pipeline stages
├── data/
│   ├── raw/rel18/               # Downloaded source DOC/DOCX files; keep unchanged
│   │   ├── *_series/            # Original specifications grouped by series
│   │   ├── manifest.json        # Downloader's original record
│   │   └── manifest.canonical.json # Normalized source metadata
│   ├── normalized/rel18/        # DOCX copies created only for legacy .doc files
│   │   ├── *_series/            # Converted DOCX files; used during ingestion
│   │   ├── manifest.conversion.json # Maps every source document to its usable DOCX path
│   │   └── .lo_profiles/        # Temporary LibreOffice worker profiles; no RAG data
│   ├── assets/rel18/            # Extracted embedded images, grouped by document ID
│   ├── processed/rel18/         # Generated ingestion output
│   │   ├── chunks.jsonl         # Main chunk corpus used for indexing
│   │   ├── ingestion_report.json # Main ingestion success/failure report
│   │   ├── test_23031.jsonl     # Isolated TS 23.031 test output; not used by the app
│   │   └── test_23031_report.json # Report for that isolated test
│   └── indexes/                 # Built retrieval indexes
│       ├── chroma/              # Chroma vector database
│       └── bm25/                # BM25 lexical index
├── .env                         # Local Groq API key and model IDs; never commit
├── .env.example                 # Safe environment-variable template
├── DECISIONS.md                 # Architecture decisions and research rationale
└── requirements.txt             # Python dependencies
```

## What each pipeline stage does

1. `convert_legacy_docs.py` converts only legacy `.doc` sources into separate DOCX copies. It does not touch `data/raw`.
2. `ingest_specs.py` reads the usable DOCX files in document order, extracts text/tables/images, calls Groq Vision only when `--caption-images` is supplied, and writes metadata-rich chunks to JSONL.
3. `index_chunks.py` creates the local BGE dense index in Chroma and the BM25 index. CUDA is used here when PyTorch detects the NVIDIA GPU.
4. `run_app.py` starts the question-answer interface, which retrieves chunks then asks Groq to generate a clause-cited answer.

## Image captions

For each supported embedded raster image (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`):

- The raw image is stored under `data/assets/rel18/<document_id>/`.
- Its ID is placed in the chunk's `asset_ids` metadata.
- Groq Vision's clean final description is inserted in the chunk text as `[FIGURE DESCRIPTION: <asset_id>]`.
- Vector assets such as EMF/WMF are saved but are not sent to Groq Vision yet.

The 3GPP logo test is expected to have a short caption such as `3GPP™ / A GLOBAL INITIATIVE`; it is not technical content.

## Commands

Run from the project root with the virtual environment active.

```powershell
# Verify installation, GPU visibility, corpus paths, and Groq key presence
py -m scripts.preflight

# Optional isolated visual-caption test; does not overwrite the main corpus
py -m scripts.ingest_specs --spec-id 23.031 --caption-images --workers 1 --output data\processed\rel18\test_23031.jsonl --report-output data\processed\rel18\test_23031_report.json

# Build the one-series prototype corpus (38 series, text/tables/raw images)
py -m scripts.ingest_specs --series 38 --workers 4 --progress-every 25

# Later: build the full corpus, including Groq Vision descriptions
py -m scripts.ingest_specs --caption-images --workers 4 --progress-every 25

# Build Chroma + BM25 indexes (uses the GPU when CUDA is available)
py -m scripts.index_chunks --reset

# Start the application
py -m scripts.run_app
```

## Important generated files

- Keep `manifest.conversion.json`, `chunks.jsonl`, `ingestion_report.json`, `assets/`, and `indexes/`: they are the traceable RAG corpus and retrieval data.
- `test_23031.jsonl` and `test_23031_report.json` are safe to delete after testing.
- `.lo_profiles/` is LibreOffice temporary conversion state. Since conversion is complete, it can be deleted later if disk space matters; do not delete the converted DOCX files or `manifest.conversion.json`.
