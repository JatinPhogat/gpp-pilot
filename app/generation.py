"""Strict Groq generation: answer from retrieved clauses or refuse."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from groq import Groq

from app.schemas import Answer, RetrievalResult

load_dotenv()


def source_label(chunk: dict) -> str:
    if chunk.get("clause_id"):
        return f"TS {chunk['spec_id']} · Clause {chunk['clause_id']}"
    return f"TS {chunk['spec_id']} · Document title"


def build_context(results: list[RetrievalResult], budget_words: int = 2600) -> str:
    blocks: list[str] = []
    used = 0
    for item in results:
        text = item.chunk["text"].strip()
        words = len(text.split())
        if used + words > budget_words:
            continue
        blocks.append(f"SOURCE: {source_label(item.chunk)}\n{text}")
        used += words
    return "\n\n---\n\n".join(blocks)


def answer(question: str, results: list[RetrievalResult]) -> Answer:
    if not results:
        return Answer("I do not have enough matching evidence in the indexed Release 18 specifications to answer that.", refused=True)

    evidence = build_context(results)
    if not evidence:
        return Answer("I do not have enough matching evidence in the indexed Release 18 specifications to answer that.", refused=True)

    prompt = f"""You are a precise 3GPP standards assistant.

Answer the user's question using ONLY the supplied specification excerpts.
- Do not use outside knowledge or infer missing steps.
- Write a useful, detailed technical answer. Cover all relevant details present in the excerpts; do not compress a procedure into a few sentences. Use bullets only when they make a procedure clearer.
- Never include citations, clause numbers, TS numbers, source labels, brackets, or a sources section in the answer. The interface shows sources separately.
- If the excerpts do not directly support an answer, reply with exactly: NO_ANSWER

QUESTION:
{question}

SPECIFICATION EXCERPTS:
{evidence}
"""
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = client.chat.completions.create(
        model=os.getenv("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_completion_tokens=900,
    )
    text = (response.choices[0].message.content or "").strip()
    if text.upper().startswith("NO_ANSWER"):
        return Answer("I do not have enough direct evidence in the retrieved clauses to answer that.", refused=True)

    # Cards identify the exact excerpts supplied to Groq; prose remains citation-free.
    citations = list(dict.fromkeys(source_label(item.chunk) for item in results))
    return Answer(text=text, citations=citations)
