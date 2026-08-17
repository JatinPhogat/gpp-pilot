# GPP-Pilot decisions and project story

## How the project reached its current state

GPP-Pilot began as a 3GPP standards assistant designed to answer engineering
questions with traceable specification evidence, not general-model knowledge.
The first delivery target is a working Release 18 prototype rather than a large
but unvalidated multi-release corpus.

1. **Corpus selected.** Release 18 was downloaded from the 3GPP archive. The
   local collection contains 1,826 source documents across the available series.
2. **Legacy formats made usable.** Modern DOCX documents are read directly.
   The 693 legacy DOC documents were converted once with LibreOffice into a
   separate normalized folder, leaving the original 3GPP downloads unchanged.
3. **A focused prototype was built.** The 38-series was ingested first: 133
   documents produced 56,901 chunks. This is the corpus currently indexed in
   the running application, so it must describe itself as a 38-series prototype,
   not as complete Release 18 coverage.
4. **Local retrieval was indexed.** Each chunk has Release, series, TS number,
   clause, content type, and source-document metadata. BGE-small embeddings are
   stored in Chroma and a BM25 lexical index is stored locally. The NVIDIA GPU
   is used for embedding/indexing and for the local cross-encoder reranker when
   CUDA is available.
5. **Grounding was made the priority.** At question time, dense and BM25 results
   are fused, reranked, and the strongest five excerpts are sent to Groq. Groq
   must answer only from those excerpts or refuse. The UI shows the supplied
   clause cards below the answer, never inline in the prose.

## Current architecture

```text
Question
  -> deterministic telecom-term expansion
  -> BGE dense retrieval + BM25 lexical retrieval
  -> reciprocal-rank fusion + local cross-encoder rerank
  -> up to five retrieved clauses
  -> Groq grounded answer OR refusal
  -> source cards in the interface
```

This is intentionally simpler than the original full roadmap. It makes the
prototype easier to test, demonstrate, and debug before the remaining Release
18 series are added.

## Decisions made

| Decision | Choice | Reason |
| --- | --- | --- |
| Starting scope | Release 18; 38-series indexed first | Validate accuracy and UI end-to-end before scaling. |
| Source format | Native DOCX; DOC converted once with LibreOffice | Preserves the original Word structure and avoids OCR errors. |
| Tables | Deterministic DOCX table-to-Markdown extraction | Avoids LLM transcription errors in technical values. |
| Images | Extracted and retained, but disabled in the current UI | Prevents unrelated logos or figures from being shown as evidence. Vision captions return when the full captioned corpus is built. |
| Retrieval | BGE-small + BM25 + RRF + bge-reranker-base | Combines semantic and exact technical-term matching with local GPU support. |
| Generation | Groq `llama-3.3-70b-versatile`, temperature 0 | Fast, low-variance answer generation from retrieved evidence only. |
| Citations | Source cards below the answer | Keeps technical prose readable while still showing the exact retrieved clauses. |
| Refusal | No supported excerpts means no answer | Minimizes hallucination rather than filling gaps with plausible telecom knowledge. |
| Excluded | Fine-tuning, web search, PaddleOCR, and paid non-Groq LLMs | They are unnecessary for this first evidence-first prototype. |

## Metadata policy

Each chunk keeps only metadata used by retrieval or traceability:

- `release`, `series`, `spec_id` for filtering;
- `clause_id`, `chunk_index` for title retention and source cards;
- `document_id`, `content_type`, `asset_ids` for future image support.

The full heading path is prepended inside the chunk text. Extra duplicated
metadata was removed to keep the JSONL corpus simple.

## Next milestone

Evaluate the 38-series prototype with realistic questions, correct retrieval
failures, then ingest and index the remaining Release 18 series using the same
pipeline. No architectural rewrite should be needed.
