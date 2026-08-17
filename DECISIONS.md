# GPP-Pilot: My Design Journey and Architecture Decisions

## Why I started GPP-Pilot

I started GPP-Pilot because working with 3GPP specifications is very different
from reading ordinary technical documentation. The information is authoritative,
but it is spread across many long documents, written in formal standards
language, and connected through message names, clause references, timers,
Information Elements and release-specific behaviour. A normal search can find
keywords; it does not reliably explain a procedure or show which clause
supports an answer.

The first idea was simple: build a chatbot that can answer 3GPP questions. The
more important question was *how to prevent it from sounding correct while being
wrong*. That became the core design principle of the project:

> The system should retrieve 3GPP evidence before answering, and it should be
> allowed to say that the available evidence is insufficient.

This decision shaped every later choice: corpus design, parsing, chunking,
retrieval, the UI, and even the features I deliberately postponed.

## What I researched before building

Before writing the pipeline, I studied telecom-focused RAG work and existing
3GPP question-answering projects. The most useful references for the early
architecture were Telco-oRAG, TelcoAI, Chat3GPP-related work and existing open
source 3GPP tools. I did not copy one repository or paper. Instead, I extracted
the ideas that fit this project and tested them against the constraints of a
local Windows laptop, official Word documents, an RTX 3050 GPU and a Groq API
budget.

### What Telco-oRAG contributed

Telco-oRAG highlighted that telecom questions contain abbreviations, variants
of the same term and highly specific identifiers. It motivated three query-time
ideas:

1. **Abbreviation expansion** means adding the full form of a known telecom
   shorthand to the search query. For example, `UE` becomes *user equipment*,
   `gNB` becomes *gNodeB base station*, and `RRC` becomes *radio resource
   control*. This improves recall without changing the user's intent.
2. **Query rephrasing** means asking an LLM to restate a natural-language
   question in terminology closer to the specification. For example, a user may
   ask about “RRC connection setup,” whereas the specification commonly uses
   “RRC connection establishment.” Rephrasing can improve retrieval, but it can
   also alter a precise technical term or add latency.
3. **Sub-query planning** means splitting a genuinely multi-part question into
   smaller retrieval questions. For example, “compare procedure A and procedure
   B, including timers and failure handling” may be better handled as separate
   searches for A, B, timers and failure handling, followed by result fusion.

I implemented deterministic abbreviation and phrase expansion because it is
fast, inspectable and does not rewrite technical meaning. I deliberately did
**not** enable LLM rephrasing or automatic sub-query planning in the current
prototype. The 38-series corpus must first prove that basic hybrid retrieval is
reliable. Adding another LLM before retrieval would make errors harder to
diagnose: was the failure in the user's question, the rewrite, retrieval, or
generation? These remain planned enhancements, not abandoned ideas.

### What TelcoAI contributed

TelcoAI reinforced the importance of document structure and multimodal context.
Standards documents do not contain only paragraphs: they contain tables,
figures, message flows and diagrams. A chunk needs the meaning of its parent
section, not just isolated sentences.

This led to heading-aware chunking. During ingestion, the parser keeps the
heading hierarchy and prepends it to each chunk as a section path. A retrieved
message field therefore carries information about the procedure and chapter in
which it appears.

TelcoAI also motivated the intended figure strategy: extract a source image,
produce a structural description, and attach that description to the related
chunk. This allows a text query to retrieve a diagram without requiring a
separate CLIP-style image embedding system.

### What Chat3GPP-style retrieval contributed

Chat3GPP-style work emphasized two practices that matter in a standards
assistant:

- preserve heading or clause context inside the text sent to the embedding
  model; and
- make source provenance visible to the user.

I adopted both. Every chunk starts with its section path, and the final UI
shows the retrieved TS/clause source cards under a successful answer. I chose
cards instead of citations embedded in every sentence because the user should
be able to read normal engineering prose first, then inspect the evidence.

## The initial full roadmap

The original research produced a more ambitious target architecture:

```text
Official Release 18 documents
  -> native document parsing and table extraction
  -> figure extraction and optional Groq Vision descriptions
  -> hierarchy-aware chunks with release/series/spec/clause metadata
  -> local embeddings + vector database + BM25
  -> query expansion, optional rephrasing and optional sub-query planning
  -> fusion and cross-encoder reranking
  -> grounded Groq generation, citations and optional source-image display
  -> evaluation, then deployment
```

This is still the long-term direction. However, implementing every component
at once would create a large system without a clear way to validate it. I made
the important engineering decision to reduce the first deliverable: prove a
working evidence-first path on one series before scaling the corpus or adding
agents, multimodal UI features or deployment infrastructure.

## Decision 1: start with Release 18 and a 38-series prototype

I selected Release 18 as the initial corpus because the metadata model can
later support multiple releases without redesign. I downloaded the Release 18
collection from the official 3GPP archive: 1,826 documents in total.

The files were not uniform. There were 1,133 DOCX documents and 693 legacy DOC
documents. Instead of manually changing files or ignoring the DOC documents, I
built a normalization stage that converts only legacy DOC files to DOCX using
LibreOffice. The original files remain untouched under `data/raw`, while the
converted copies and conversion manifest live under `data/normalized`.

I then indexed the 38 series first: 133 specifications became 56,901 chunks.
This was a deliberate scope decision. The prototype can now be tested against
NR/RRC questions, and I can accurately say what it covers. It is not presented
as complete all-series Release 18 coverage yet.

## Decision 2: parse Word documents natively instead of using OCR

At one point I considered OCR-heavy approaches. That would have been
unnecessary and risky for this corpus. The 3GPP documents are Word files with
native text, headings and tables. OCR can misread exactly the information a
standards assistant must preserve: field names, timer labels, enum values,
version numbers and message identifiers.

The final choice was:

- use `python-docx` for DOCX;
- use LibreOffice once for legacy DOC conversion;
- preserve table contents deterministically as Markdown; and
- use vision only for embedded diagrams when a captioned corpus is built.

This was slower at the conversion stage than a plain-text shortcut, but it is a
one-time offline cost and gives much better structural input for retrieval.

### Difficulty: LibreOffice conversion and parallel processing

LibreOffice uses a user profile lock, so naive parallel conversion can conflict.
The converter was updated to create a separate temporary LibreOffice profile
for each worker. Progress logging was added because a long conversion without
visible output looked like a frozen process. The final conversion produced a
usable DOCX path for every document with no reported conversion failures.

## Decision 3: preserve headings, tables and traceability in chunks

I did not want to split the corpus into arbitrary blocks of text. A sentence
such as “the UE shall…” has little value if retrieval loses the procedure,
message or clause that defines it.

The ingestion design therefore does the following:

1. reads paragraphs and tables in document order;
2. keeps a stack of Word heading levels;
3. extracts the clause number and title when present;
4. prepends the complete section path to the chunk text;
5. keeps tables as Markdown; and
6. stores Release, series, TS number, clause, document ID, content type and
   source asset identifiers alongside the chunk.

The metadata fields are not only for display. They allow exact TS filtering,
source cards, image association and later incremental extension to other
releases.

## Decision 4: use hybrid retrieval, not only a vector database

I chose hybrid retrieval because 3GPP questions require two different kinds of
matching.

- **Semantic matching:** a user may ask in normal language about a concept
  whose wording differs from the document.
- **Exact matching:** a user may ask for `RRCSetupRequest`, `T310`, `SRB1` or
  a precise Information Element name. These terms should not be diluted by
  semantic similarity alone.

The current retrieval pipeline uses:

| Stage | Decision | Why |
| --- | --- | --- |
| Dense retrieval | `bge-small-en-v1.5` in Chroma | Captures semantic similarity locally. |
| Lexical retrieval | BM25 | Protects exact 3GPP term matching. |
| Fusion | Reciprocal Rank Fusion | Combines ranking positions without pretending dense and BM25 scores are directly comparable. |
| Reranking | `bge-reranker-base` | Scores candidate chunks against the original question more precisely. |
| Final context | Up to five chunks | Provides enough evidence for detailed answers without flooding the prompt. |

The embedding model and reranker use CUDA when PyTorch detects the RTX 3050.
Document parsing remains CPU-oriented because Word parsing itself does not have
a meaningful GPU implementation.

### Difficulty: early retrieval returned weak evidence

Initial tests exposed a real RAG lesson: having the right document in the index
does not guarantee that the right clause will be selected. Broad questions could
retrieve test material, a title chunk could be lost during reranking, and a
procedure could be represented by partial fragments.

The solution was not to hardcode answers. Instead, I widened the retrieval
candidate pool, kept the source title when a user explicitly asks for a
document's purpose/scope, reranked more candidates, and printed the final
selected TS/clause labels in the terminal. This makes the retrieval process
observable and keeps future debugging focused on evidence rather than guesswork.

## Decision 5: make hallucination control more important than answer coverage

The most serious failure mode is a fluent but unsupported standards answer. I
therefore made the generation stage intentionally strict.

Groq receives only the retrieved excerpts and the question. The system prompt
instructs it to:

- use only the supplied specification text;
- avoid external knowledge and inferred steps;
- write normal technical prose rather than citation-heavy text; and
- return `NO_ANSWER` when the evidence does not directly support a response.

The UI shows source cards only below a supported answer. It does not print
`[TS 38.xxx]` after every sentence because those labels made answers difficult
to read. The cards keep the response traceable without making it look like a
raw retrieval dump.

## Decision 6: keep multimodal retrieval as a staged feature

The original plan included Groq Vision for diagrams. During testing, several
practical issues appeared:

- an initial vision model name was no longer available;
- Groq vision requests could hit token-per-minute limits;
- documents include logos, trademarks and decorative figures alongside genuine
  technical diagrams; and
- the active 38-series index was created without figure descriptions.

I corrected the Groq model configuration and added retry/rate-limit handling in
the ingestion script. However, I decided not to display images in the current
prototype. Showing a random logo as evidence is worse than showing no image.
The next multimodal step is to rebuild a captioned corpus and verify that a
retrieved caption really corresponds to the user's requested diagram before
rendering it.

## Decision 7: keep the interface transparent and simple

The Streamlit UI was improved through direct testing. The final prototype keeps
the useful pieces:

- a simple chat interaction;
- immediate handling for greetings and thanks, without an unnecessary RAG call;
- clear progress messages while a technical question is processed;
- source cards below answers; and
- terminal output such as `[RAG] Retrieved 5 chunks: ...`.

I removed behaviour that looked impressive but was misleading: irrelevant
images, inline source clutter, hidden retrieval activity and overly complex
agent-style steps. The interface should make it obvious that the answer is a
retrieval-grounded response, not magic.

## Features intentionally postponed

These features remain part of the roadmap, but are not active in the current
prototype:

| Feature | What it means | Why it is postponed |
| --- | --- | --- |
| LLM query rephrasing | An LLM rewrites the question into standards terminology before retrieval. | It can change the meaning of a precise technical question and makes debugging harder. |
| Sub-query planning | A classifier detects a multi-part question and splits it into up to three retrieval queries before merging results. | It is useful only after basic retrieval is measured; otherwise it adds cost and complexity without proof. |
| Figure captions in production | Groq Vision describes embedded technical diagrams and descriptions are indexed with chunks. | The current index has no evaluated captions, so images are disabled for correctness. |
| Multimodal answer generation | A retrieved source image is passed to a vision model with text evidence. | Requires reliable caption retrieval and a strict image relevance check first. |
| Remaining Release 18 series | Extending beyond the current 38-series prototype. | The pipeline is ready, but retrieval quality should be evaluated before scaling. |
| Docker and cloud deployment | Packaging the system for hosted use. | Current local Chroma/BM25/model assets are large; deployment follows validated retrieval. |

## What I decided not to use

I explicitly excluded several alternatives for this stage:

- **Fine-tuning:** it does not make the source of an answer visible, and RAG
  quality must be proven before adding model-training complexity.
- **Live web search:** the project is designed to treat official 3GPP documents
  as the primary evidence base.
- **PaddleOCR as the primary parser:** native Word parsing is safer for this
  particular source corpus.
- **Full image embeddings:** caption-in-chunk retrieval is simpler to validate
  before introducing a second embedding modality.
- **Always-on agents and routers:** a smaller deterministic pipeline is easier
  to test, explain and demonstrate.

## Where the project is now

GPP-Pilot has reached a complete end-to-end prototype state:

```text
Official Release 18 documents
  -> DOC normalization where required
  -> heading-aware chunks and metadata
  -> Chroma dense index + BM25 index
  -> hybrid retrieval + cross-encoder reranking
  -> strict Groq evidence-grounded answer or refusal
  -> Streamlit answer with source cards and terminal retrieval trace
```

The active scope is Release 18, 38-series documents. This is enough to
demonstrate the core architecture honestly. It also gives a clear next step:
evaluate retrieval with realistic question sets, correct weak cases, then use
the same pipeline to add the remaining Release 18 series.

## What I would explain in an interview

The main lesson from this project is that domain RAG is a retrieval and evidence
engineering problem before it is a chatbot problem. The important work was not
only calling an LLM. It was handling mixed document formats, retaining hierarchy,
combining semantic and exact-term retrieval, observing failure cases, preventing
unsupported generation, and knowing when to postpone features until the core
system could be tested.

GPP-Pilot is therefore not presented as a finished “AI that knows 3GPP.” It is
presented as a working, inspectable standards-retrieval prototype with a clear
path from source document to answer and a disciplined plan for expansion.
