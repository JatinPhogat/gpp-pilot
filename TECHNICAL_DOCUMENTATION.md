# GPP-Pilot Technical Documentation

## 1. Purpose

GPP-Pilot is a Retrieval-Augmented Generation (RAG) assistant for 3GPP technical
specifications. Its purpose is to help an engineer ask natural-language
questions about a standard and receive an answer that is grounded in retrieved
specification clauses rather than in a language model's general knowledge.

The project is designed around a simple reliability rule:

> Retrieve the specification evidence first. Generate only from that evidence.
> If the evidence is not sufficient, do not guess.

This makes the application suitable for standards-oriented use cases, where an
answer can sound plausible while still being technically incorrect.

## 2. Current scope

The long-term target is a Release 18 3GPP corpus covering the relevant series.
The current demonstrable prototype is intentionally narrower:

| Area | Current state |
| --- | --- |
| Release | 18 |
| Downloaded source corpus | 1,826 documents |
| Source formats | 1,133 DOCX and 693 legacy DOC documents |
| Indexed subset | 38 series |
| Indexed documents | 133 |
| Indexed chunks | 56,901 |
| User interface | Local Streamlit web application |

Therefore, the current application can answer questions supported by the
indexed 38-series corpus. It should not present itself as complete all-series
Release 18 coverage until the remaining series are ingested and indexed.

## 3. System architecture

```text
                         OFFLINE PIPELINE

3GPP Release 18 DOC/DOCX sources
        |
        +--> Legacy DOC conversion (LibreOffice, once only)
        |
        +--> Text, headings, tables and embedded-image extraction
        |
        +--> Metadata-rich clause chunks (JSONL)
        |
        +--> BGE dense vectors in Chroma + BM25 lexical index

                         ONLINE PIPELINE

User question
        |
        +--> Deterministic telecom terminology expansion
        |
        +--> Dense retrieval + BM25 retrieval
        |
        +--> Reciprocal Rank Fusion (RRF)
        |
        +--> Cross-encoder reranking
        |
        +--> Five strongest source chunks
        |
        +--> Groq grounded generation, or refusal
        |
        +--> Natural-language answer and source cards
```

The offline pipeline is run only when the corpus changes. The online pipeline
runs for every user question.

## 4. Source acquisition and normalization

3GPP documents are downloaded from the 3GPP specification archive. The source
collection contains Word documents, not a single uniform file type:

- DOCX files are used directly.
- Legacy DOC files are converted to DOCX once with LibreOffice in headless mode.
- Original downloaded files are never overwritten.

The converted files are placed under `data/normalized/rel18`, separate from the
raw archive under `data/raw/rel18`. This separation is important for
traceability: the project can always identify the original download while using
a parser-friendly normalized copy during ingestion.

## 5. Ingestion and chunk construction

`scripts/ingest_specs.py` reads each usable document and creates clause-aware
chunks. Its main responsibilities are:

1. Read Word paragraphs in their original order.
2. Detect heading styles and maintain the current heading hierarchy.
3. Extract tables and serialize them as Markdown instead of asking an LLM to
   interpret numerical values or table layout.
4. Extract embedded images into `data/assets/rel18/<document_id>/`.
5. Prefix each chunk with its section path, preserving the surrounding standard
   context when the chunk is retrieved later.
6. Split oversized prose blocks while keeping structured tables intact whenever
   possible.

Every generated chunk receives a stable ID based on Release, TS number, source
version, clause and position. The runtime relies primarily on the following
fields:

| Field | Purpose |
| --- | --- |
| `release` | Release filtering and provenance |
| `series` | Series-level scope |
| `spec_id` | Exact TS filtering, for example `38.331` |
| `clause_id` | Source-card label and clause traceability |
| `chunk_index` | Ordering and title-chunk retention |
| `document_id` | Link between chunks and source assets |
| `content_type` | Text, table, figure or mixed-content handling |
| `asset_ids` | Future figure lookup |
| `text` | Section path plus the retrieved specification content |

The JSONL corpus also retains additional ingestion provenance fields where they
already exist. The application uses only the fields required for retrieval and
traceability.

## 6. Tables and images

Tables are converted deterministically from DOCX cells to Markdown. This is a
deliberate quality decision: technical identifiers, values and enumerations are
safer when extracted directly than when transcribed by a language model.

Images are saved as source assets. The ingestion script can optionally send
supported raster images to Groq Vision and append a structural description to
the relevant chunk. Vector image formats are retained but are not sent to the
vision model.

The current 38-series index was built without image captions, and the UI does
not render images. This prevents logos or unrelated figures from being shown as
evidence. Vision-assisted figure retrieval is a planned future extension after
the captioned corpus is built and evaluated.

## 7. Indexing

Index construction is performed by `scripts/index_chunks.py`.

### Dense index

- **Embedding model:** `BAAI/bge-small-en-v1.5`
- **Vector size:** 384 dimensions
- **Storage:** Chroma persistent collection
- **Device:** CUDA when PyTorch detects the NVIDIA GPU

Dense vectors support semantic matching. For example, a user may use a natural
phrase that differs from the exact wording used in a specification clause.

### Lexical index

- **Method:** BM25
- **Storage:** local pickle file

BM25 supports exact technical matching for terms such as message names,
Information Elements, timer names and clause-specific vocabulary.

The two methods complement each other: dense search improves semantic recall,
while BM25 protects exact 3GPP terminology.

## 8. Query processing and retrieval

The runtime retrieval implementation is in `app/retrieval.py`.

1. The application extracts any explicit TS number or Release mentioned by the
   user and applies it as a metadata filter.
2. A small deterministic glossary expands common terms such as UE, gNB, RRC,
   NR, SRB and DRB. It also maps common wording such as “RRC connection setup”
   to “RRC connection establishment.”
3. Dense search and BM25 each return a candidate set.
4. Reciprocal Rank Fusion combines the two rankings without assuming that their
   numeric scores have the same scale.
5. A local `bge-reranker-base` cross-encoder scores the best candidates against
   the original user question.
6. The five strongest chunks are selected and printed in the terminal, for
   example:

   ```text
   [RAG] Retrieved 5 chunks: TS 38.331 §5.3.3, TS 38.331 §6.2.2, ...
   ```

For a direct “purpose/scope of TS x.yyy” question, the relevant document title
chunk is retained as source evidence. This is not a hardcoded answer: it is a
general rule that ensures the document's own title is not discarded by ranking.

## 9. Grounded answer generation

`app/generation.py` sends only the selected excerpts to Groq. The generation
prompt enforces the following controls:

- Answer only from supplied excerpts.
- Do not use external knowledge.
- Do not invent procedures, timers, fields or message sequences.
- Produce normal technical prose without inline TS/clause labels.
- Reply `NO_ANSWER` when the excerpts do not directly support the answer.

The production text model is configured through `.env`; the current default is
`llama-3.3-70b-versatile` through Groq. Temperature is set to zero to reduce
variation between runs.

After generation, the UI renders source cards beneath the answer. The cards
identify the exact clauses supplied to the model, while the answer itself
remains readable engineering prose.

## 10. User interface

The Streamlit interface in `app/ui.py` provides:

- a chat-style question and answer view;
- small-talk handling without unnecessary retrieval;
- example 38-series questions;
- visible “reading, searching, checking” progress states;
- source cards for grounded answers; and
- terminal visibility into the selected source chunks.

For short referential follow-up questions such as “tell me more about that,”
the previous user question is appended to the retrieval query. This helps
retrieval retain conversational context without sending an unrestricted chat
history to the answer model.

## 11. Running the application

From the project root:

```powershell
.\.venv\Scripts\Activate.ps1
py -m scripts.run_app
```

Ensure `.env` contains:

```text
GROQ_API_KEY=your_key_here
GROQ_TEXT_MODEL=llama-3.3-70b-versatile
```

Open `http://localhost:8501` after Streamlit starts.

To rebuild the current 38-series corpus only when needed:

```powershell
py -m scripts.ingest_specs --series 38 --workers 5 --progress-every 15
py -m scripts.index_chunks --reset
```

## 12. Reliability, limitations and evaluation

The main hallucination control is evidence restriction: the model sees only
retrieved chunks and is instructed to refuse unsupported questions. This does
not guarantee perfect accuracy; retrieval quality still determines answer
quality. A weakly retrieved but plausible chunk can lead to an incomplete
answer, which is why source cards and terminal retrieval logs are included.

Current limitations are intentional and transparent:

- Only the 38 series is currently indexed.
- Image captions are not part of the active prototype corpus.
- No web search is used; 3GPP documents remain the primary source.
- The project does not fine-tune a model; it uses retrieval so that answers can
  remain traceable to the source documents.

The next technical milestone is to create a small evaluation set of realistic
3GPP questions, inspect retrieval outputs, and measure answer grounding before
adding the remaining Release 18 series.

## 13. Technology stack

| Layer | Technology | Role in GPP-Pilot |
| --- | --- | --- |
| Language | Python | All ingestion, retrieval and application logic |
| UI | Streamlit | Local chat interface and progress display |
| Document parser | `python-docx` | Reads DOCX paragraphs, tables and embedded relationships |
| Legacy conversion | LibreOffice headless | Converts legacy DOC files to DOCX once |
| Dense embeddings | `BAAI/bge-small-en-v1.5` | Creates 384-dimensional document and query vectors |
| Vector store | ChromaDB | Persistent local dense-vector search and metadata filtering |
| Lexical search | `rank-bm25` | Exact keyword and identifier retrieval |
| Reranker | `BAAI/bge-reranker-base` | Cross-encoder relevance scoring of retrieval candidates |
| Generation and vision | Groq API | Grounded answer generation; optional image description during ingestion |
| GPU runtime | PyTorch CUDA | Accelerates embedding/indexing and local reranking when CUDA is available |
| Configuration | `python-dotenv` | Loads the local Groq key and optional model setting from `.env` |
| Testing support | pytest | Available for future automated evaluation tests |

The project uses free local retrieval models and a Groq-hosted generation model.
This separates retrieval quality from answer fluency: source selection happens
locally and answer wording happens after evidence has been selected.

## 14. Codebase reference

### Application modules: `app/`

| File | Main responsibilities | Important functions/classes |
| --- | --- | --- |
| `config.py` | Defines all project-relative paths and model IDs in one place. | `ROOT`, `RAW`, `NORMALIZED`, `PROCESSED`, `ASSETS`, `CHROMA_PATH`, `BM25_PATH`, `EMBEDDING_MODEL`, `RERANKER_MODEL` |
| `schemas.py` | Provides small typed records exchanged between ingestion, retrieval and generation. | `Chunk`, `RetrievalResult`, `Answer` |
| `glossary.py` | Performs deterministic abbreviation and phrase expansion before retrieval. | `ABBREVIATIONS`, `PHRASE_EXPANSIONS`, `expand_abbreviations()` |
| `retrieval.py` | Loads local indexes/models and returns ranked source chunks. | `query_filters()`, `is_scope_question()`, `usable()`, `HybridRetriever.retrieve()` |
| `generation.py` | Builds evidence context, calls Groq, enforces refusal parsing and prepares source labels. | `source_label()`, `build_context()`, `answer()` |
| `ui.py` | Implements Streamlit state, examples, small-talk, progress display, chat rendering and source cards. | `load_retriever()`, `small_talk()`, `search_question()`, `show_assistant()` |

### Pipeline scripts: `scripts/`

| File | Input | Output | Main responsibility |
| --- | --- | --- | --- |
| `download_specs.py` | Official 3GPP Release listing | `data/raw/rel18`, `manifest.json` | Discovers release/series ZIPs, extracts the Word document, and records source provenance. |
| `normalize_manifest.py` | Download manifest | `manifest.canonical.json` | Builds stable IDs such as `rel18__38.331__ia0`, extracts release/series/TS/version metadata, and can add SHA-256 hashes. |
| `convert_legacy_docs.py` | Canonical manifest and DOC sources | `data/normalized/rel18`, `manifest.conversion.json` | Converts only DOC files with LibreOffice; passes original DOCX paths through unchanged. |
| `ingest_specs.py` | Conversion manifest and usable DOCX files | `chunks.jsonl`, assets, ingestion report | Extracts ordered text/tables/images, maintains heading context, builds chunks and optionally obtains Groq vision descriptions. |
| `index_chunks.py` | `chunks.jsonl` | Chroma collection and `bm25.pkl` | Encodes chunks, persists vectors, and serializes the BM25 index plus chunk records. |
| `preflight.py` | Local `.env`, conversion manifest and CUDA runtime | Terminal result | Confirms GPU visibility, Groq key presence and usable converted paths before expensive processing. |
| `run_app.py` | Application files and indexes | Local Streamlit server | Launches `streamlit run app/ui.py`. |

## 15. Important implementation flow

### 15.1 Download and canonical metadata

`download_specs.py` reads the official 3GPP FTP HTML listings, selects the
requested Release/series/specifications, downloads ZIP archives, and extracts
the largest contained Word file. It writes a download manifest with the source
URL, ZIP version code and local path.

`normalize_manifest.py` converts each filename into a canonical identity. For
example, an archive name such as `38331-ia0.zip` becomes the specification ID
`38.331`, version code `ia0`, and document ID `rel18__38.331__ia0`. This avoids
using fragile display filenames as retrieval identifiers.

### 15.2 Legacy conversion

`convert_legacy_docs.py` uses a `ThreadPoolExecutor` so several independent
LibreOffice conversions can run concurrently. LibreOffice locks its user
profile, so every conversion worker receives a dedicated temporary profile
directory. The resulting conversion manifest records a usable DOCX path for
every document, whether it was originally DOCX or converted from DOC.

### 15.3 Ingestion

`ingest_specs.py` is the main corpus builder. Its implementation uses these
core operations:

- `ordered_blocks()` preserves paragraph/table order from Word XML.
- `heading_level()` detects Word heading styles.
- `clause_and_title()` separates a clause number from the visible heading title.
- `markdown_table()` serializes tables without an LLM.
- `extract_images()` saves embedded images, skips external relationships safely,
  and optionally requests Groq Vision captions for supported raster formats.
- `build_chunks()` maintains a heading stack, adds section-path context, chunks
  oversized content, and assigns source metadata.

The ingestion command supports `--series`, `--spec-id`, `--workers`,
`--caption-images`, `--output`, `--report-output` and `--progress-every`.

### 15.4 Indexing

`index_chunks.py` loads every JSONL record, creates normalized BGE embeddings,
and upserts them into a persistent Chroma collection named `gpp_rel18`. The
same chunk text is tokenized and passed to BM25. The BM25 pickle includes both
the lexical index and its chunk records, which is why the application can
retrieve without reading `chunks.jsonl` during normal runtime.

### 15.5 Retrieval

`HybridRetriever` loads the BGE embedding model, cross-encoder reranker,
Chroma collection and BM25 pickle once per Streamlit process. For a query it:

1. Extracts an explicit TS number or Release filter, if present.
2. Expands common 3GPP abbreviations deterministically.
3. Encodes the query with BGE and searches Chroma.
4. Scores the same query using BM25 over the local chunks.
5. Fuses ranks using `1 / (60 + rank)` reciprocal-rank scoring.
6. Reranks up to 80 fused candidates with the cross-encoder.
7. Returns the top five chunks and prints their TS/clause labels.

### 15.6 Generation and source cards

`answer()` takes only the final retrieval results. `build_context()` limits the
combined evidence to approximately 2,600 words, avoiding unbounded prompt
growth. The Groq prompt receives the evidence and the original question. If
Groq returns the exact `NO_ANSWER` control value, the UI displays a refusal and
does not show source cards. Otherwise, the UI renders the answer followed by
source cards derived from the supplied chunks.

## 16. Command reference

Run all commands from the repository root with the virtual environment active.

```powershell
# Activate the environment
.\.venv\Scripts\Activate.ps1

# Check GPU, Groq key and conversion paths
py -m scripts.preflight

# Download all current Release 18 specifications
py -m scripts.download_specs --release 18

# Download only selected series or specifications
py -m scripts.download_specs --release 18 --series 38
py -m scripts.download_specs --release 18 --specs 38.331 38.321

# Create canonical metadata after downloading
py -m scripts.normalize_manifest

# Convert legacy DOC files; original DOCX files need no conversion
py -m scripts.convert_legacy_docs --workers 4

# Ingest the current 38-series prototype
py -m scripts.ingest_specs --series 38 --workers 5 --progress-every 15

# Optional isolated image-caption test for one specification
py -m scripts.ingest_specs --spec-id 23.031 --caption-images --workers 1

# Build/rebuild dense and BM25 indexes
py -m scripts.index_chunks --reset

# Start the application
py -m scripts.run_app
```

`--reset` deletes and rebuilds the existing Chroma index. It must only be used
when rebuilding the corpus/index, never for normal application startup.

## 17. Configuration and secrets

`app/config.py` is the single source for local paths and model names. The
current runtime models are:

```python
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL = "BAAI/bge-reranker-base"
```

The local `.env` file must not be committed. Its required configuration is:

```text
GROQ_API_KEY=your_private_key
GROQ_TEXT_MODEL=llama-3.3-70b-versatile
```

`GROQ_TEXT_MODEL` is optional because the same value is the runtime default.
For optional image captioning, `GROQ_VISION_MODEL` may also be configured.

## 18. Runtime behaviour and diagnostics

The expected local execution behaviour is:

1. Streamlit starts on `http://localhost:8501`.
2. The first technical question initializes the cached retriever and may take
   longer because local BGE and reranker weights are loaded.
3. Subsequent questions reuse the cached models and indexes.
4. The terminal prints the final selected chunks for each retrieval.

Useful diagnostics:

| Symptom | First check |
| --- | --- |
| No answers or startup error | Confirm `GROQ_API_KEY` is set in `.env`. |
| GPU not used while indexing | Run `py -m scripts.preflight`; ensure CUDA-enabled PyTorch is installed. |
| Wrong or incomplete answer | Inspect `[RAG] Retrieved ...` terminal output before changing the prompt. |
| Missing index error | Rebuild with `py -m scripts.index_chunks --reset`. |
| Legacy conversion failure | Confirm LibreOffice is installed and rerun with `--soffice` if required. |
| Groq vision rate limit | Retry later; ingestion keeps source assets and continues rather than dropping the document. |

## 19. Deployment boundary

The checked-in repository deliberately excludes `data/` indexes, source files
and model caches. The current architecture is intended for local execution or a
persistent Python host where Chroma, BM25 and local model weights remain
available. A serverless frontend deployment alone cannot run the local retriever
without separately packaging or hosting those assets.

This is a deployment decision, not a limitation of the RAG design itself.
