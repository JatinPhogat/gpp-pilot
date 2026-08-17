# GPP-Pilot: Design Decisions and Engineering Journey

## 1. Project origin

GPP-Pilot started from a practical problem: 3GPP specifications are authoritative
but difficult to search conversationally. An engineer may know the behaviour,
message or procedure they need, but locating the correct TS, clause and exact
wording across large Word documents is slow. A general LLM can give a fluent
answer, but it may mix releases, infer missing steps, or invent a clause.

The goal became a standards assistant that could produce useful technical
answers while staying tied to the actual 3GPP evidence used to answer them.

The project was first researched before it was implemented. The design was
informed by the retrieval patterns described in Telco-oRAG, TelcoAI and
Chat3GPP-related work: use standards as the primary corpus, preserve document
structure, combine lexical and semantic retrieval, and make grounding visible.
Those ideas were adapted to a local student-project environment with a Windows
laptop, an RTX 3050 GPU, free local retrieval models and Groq for generation.

## 2. Initial architecture considered

The original target was a full multimodal Release 18 RAG system:

- Release, series, TS, version and clause metadata;
- document parsing, table extraction and image descriptions;
- dense search, BM25, reciprocal-rank fusion and cross-encoder reranking;
- optional query rephrasing, abbreviation expansion and sub-query planning;
- Groq text generation and vision generation when a retrieved image mattered;
- answer citations, evaluation with RAGAS and a question benchmark; and
- later deployment through Docker.

This roadmap was valuable, but implementing everything at once would make it
hard to tell whether a failure came from the documents, extraction, retrieval,
vision, prompting or the user interface. The core decision was therefore to
build a smaller evidence-first prototype and validate each stage.

## 3. Corpus decision: Release 18 first

Release 18 was selected as the first corpus. It is a concrete, bounded scope
and supports future extension to other releases without redesigning metadata.

The 3GPP archive contains 1,826 downloaded documents for the selected Release
18 collection. The formats were mixed:

| Source type | Count | Decision |
| --- | ---: | --- |
| DOCX | 1,133 | Parse directly with `python-docx` |
| Legacy DOC | 693 | Convert once with LibreOffice, then parse as DOCX |

The original files are preserved under `data/raw`. Converted files are written
to `data/normalized`, making the process reproducible without modifying the
official source documents.

### Difficulty: legacy DOC conversion was slow

LibreOffice correctly handled the legacy format but is an office application,
so it is slower than ordinary text extraction. It was also initially unclear
whether conversion was necessary for RAG.

**Decision:** keep conversion because preserving Word headings, tables and
embedded objects is more valuable for standards retrieval than a faster but
lossy plain-text extractor. It is a one-time offline cost, not part of normal
question answering.

## 4. Prototype decision: index the 38 series before scaling

Rather than immediately ingest every series, the project indexed the 38 series
first. This produced a focused 5G NR prototype:

- 133 documents;
- 56,901 chunks; and
- a working query, retrieval and answer loop.

This scope made it possible to test the most important questions—RRC
establishment, RRC messages, NR procedures and clause queries—before adding
more material. It also keeps project claims honest: the running application is
an **indexed 38-series Release 18 prototype**, not an all-series assistant yet.

## 5. Parsing and chunking decisions

### Native Word parsing instead of forced OCR

The 3GPP sources have usable Word text layers. OCR would introduce avoidable
errors in message names, identifiers, fields and numerical values.

**Decision:** parse DOCX natively, use LibreOffice only to normalize legacy
DOC, and do not use PaddleOCR as a primary parser.

### Tables become deterministic Markdown

Technical tables often contain exact identifiers, options and numeric values.
LLM rewriting may change those values.

**Decision:** convert DOCX table cells to Markdown deterministically. The
answer model can read the extracted table text, but it is not trusted to
transcribe the table during ingestion.

### Heading-aware chunks

Standalone paragraphs from a specification can lose their meaning. A message
definition, for example, should retain its surrounding section context.

**Decision:** maintain the heading stack while parsing, prepend the full
section path to chunk text, and split only when content exceeds the target
size. This gives retrieval both local text and document context.

## 6. Vision and image decisions

Embedded images are extracted as source assets. The ingestion script can send
supported raster images to Groq Vision and add a structural description to the
associated text chunk.

### Difficulties encountered

- A previously configured Groq vision model identifier was unavailable and
  returned a 404 response.
- Vision requests were subject to token-per-minute limits.
- Documents contain logos and decorative figures as well as technical diagrams.
- The current 38-series prototype was ingested without image captions.

### Decision

Images remain stored for traceability, but image rendering is disabled in the
current UI. This avoids showing a 3GPP logo or an unrelated illustration as
technical evidence. Multimodal retrieval will return only after the captioned
corpus is built and evaluated.

## 7. Retrieval decisions

### Why hybrid retrieval

Pure vector search is useful for semantic wording, but 3GPP questions often
contain exact terms such as `RRCSetupRequest`, timer names and Information
Elements. Pure keyword retrieval misses paraphrases.

**Decision:** combine:

1. `bge-small-en-v1.5` dense embeddings in Chroma;
2. BM25 lexical retrieval; and
3. Reciprocal Rank Fusion (RRF) to merge their rankings.

A local `bge-reranker-base` cross-encoder then scores the highest-ranked
candidates against the original user question. CUDA is used when available.

### Retrieval challenges and corrections

Early UI tests exposed several issues:

- broad questions retrieved unrelated test-specification clauses;
- a document-purpose question could lose the document title chunk during
  reranking;
- a procedure question could retrieve supporting fragments but not enough of
  the actual procedure;
- no visible retrieval output made debugging difficult.

The response was to expand the candidate pool before reranking, retain a title
chunk for an explicit TS purpose/scope query, provide up to five final chunks,
and print the selected TS/clause labels in the terminal. These are retrieval
rules, not hardcoded answers for individual specifications.

## 8. Generation and hallucination-control decisions

The main risk in a standards assistant is a confident unsupported answer.
GPP-Pilot handles this with several layers:

- Groq receives only the selected source excerpts;
- the prompt prohibits external knowledge and invented steps;
- generation temperature is zero for lower variance;
- the model must return `NO_ANSWER` when support is absent; and
- source cards are shown below the answer only when generation succeeds.

Inline clause labels were intentionally removed from the prose. Engineers need
readable technical explanations, while the source cards preserve traceability
without turning every sentence into a citation string.

## 9. User interface decisions

The UI began as a simple Streamlit chat page. Testing showed that reliability
was not only a backend concern: users need to see that a standards search is in
progress and need to know where the answer came from.

The current interface therefore includes:

- clear progress states: reading terminology, searching clauses and checking
  evidence;
- a small set of examples known to fit the indexed corpus;
- direct source cards under answers;
- a visible terminal retrieval trace; and
- local handling for greetings and thanks, avoiding unnecessary model calls.

Visual results are intentionally omitted from the current prototype because
the active index does not yet contain evaluated technical image descriptions.

## 10. What was deliberately excluded

| Excluded approach | Reason for excluding it now |
| --- | --- |
| Fine-tuning | It reduces traceability and is unnecessary before RAG quality is validated. |
| Live web search | 3GPP documents are the designated primary source. |
| PaddleOCR primary parsing | Native Word extraction is more accurate for this corpus. |
| Full multimodal embeddings | Caption-in-chunk retrieval is simpler to validate first. |
| Always-on LLM query rewriting | It adds latency and may distort technical wording; deterministic expansion is safer now. |
| Full Docker deployment | Packaging comes after retrieval quality is validated. |

## 11. Current state

The project now has a complete working path from local 3GPP documents to a
grounded answer:

```text
Release 18 source documents
    -> normalized DOCX where needed
    -> heading-aware chunks and metadata
    -> Chroma + BM25 indexes
    -> hybrid retrieval and reranking
    -> Groq evidence-grounded answer
    -> source cards in Streamlit
```

The 38-series prototype is ready for demonstration and targeted evaluation.
The project is not presented as finished all-series coverage; it is presented
as a validated architecture with a working, inspectable first corpus.

## 12. Next milestones

1. Create a realistic 3GPP evaluation set and record retrieval/grounding
   results.
2. Improve difficult retrieval cases before scaling.
3. Ingest the remaining Release 18 series with the same pipeline.
4. Build and evaluate Groq Vision captions for actual technical diagrams.
5. Add Docker packaging and deployment only after corpus and evaluation goals
   are satisfied.

## 13. Interview summary

The core engineering lesson from GPP-Pilot is that a domain RAG system is not
just “put documents in a vector database.” It requires format normalization,
structure-aware chunking, hybrid retrieval, careful user-facing traceability
and a strict refusal path. The prototype deliberately trades feature breadth
for reliability: it proves the end-to-end evidence-first workflow on the 38
series before expanding to the rest of Release 18.
