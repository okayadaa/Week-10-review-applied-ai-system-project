"""
generator.py — Prompt construction and Gemini generation.

Two public functions:
    naive_generate(client, query)        → direct LLM answer, no retrieval
    rag_generate(client, query, chunks)  → answer grounded in retrieved chunks

The caller (kerag_cli.py) creates one genai.Client() and passes it here,
the same instance used by retriever.py for query embeddings.
"""

import logging
from typing import Any

from google import genai

from config import GENERATIVE_MODEL

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_NAIVE_PROMPT = """\
You are a helpful Keras documentation assistant.
Answer the following question as clearly and concisely as possible.

Question: {query}

Answer:\
"""

_RAG_PROMPT = """\
You are a helpful Keras documentation assistant. Answer the user's question \
using ONLY the context provided below. If the answer is not contained in the \
context, say "I don't have enough information to answer that."

Context:
{context}

Question: {query}

Answer:\
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def naive_generate(client: genai.Client, query: str) -> str:
    """Call Gemini directly with no retrieval context.

    Args:
        client: An authenticated google.genai.Client instance.
        query:  The user's natural-language question.

    Returns:
        The model's answer as a string, or an error message on failure.
    """
    prompt = _NAIVE_PROMPT.format(query=query)
    try:
        response = client.models.generate_content(
            model=GENERATIVE_MODEL,
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.error("Naive generation failed: %s", exc)
        return "Error: generation failed. Check your GOOGLE_API_KEY and quota."


def rag_generate(
    client: genai.Client,
    query: str,
    chunks: list[dict[str, Any]],
) -> str:
    """Generate an answer grounded in retrieved ChromaDB chunks.

    Each chunk dict is expected to have the keys returned by retriever.retrieve():
        text, source, section, chunk_index, distance

    Args:
        client: An authenticated google.genai.Client instance.
        query:  The user's natural-language question.
        chunks: Retrieved context chunks from retriever.retrieve().

    Returns:
        The model's grounded answer as a string, or an error message on failure.
    """
    if not chunks:
        return "I don't have enough information to answer that."

    context_parts = []
    for i, chunk in enumerate(chunks, start=1):
        context_parts.append(
            f"[{i}] Source: {chunk['source']} | Section: {chunk['section']}\n"
            f"{chunk['text']}"
        )
    context = "\n\n".join(context_parts)

    prompt = _RAG_PROMPT.format(context=context, query=query)
    try:
        response = client.models.generate_content(
            model=GENERATIVE_MODEL,
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.error("RAG generation failed: %s", exc)
        return "Error: generation failed. Check your GOOGLE_API_KEY and quota."
