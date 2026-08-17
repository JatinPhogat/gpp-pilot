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
