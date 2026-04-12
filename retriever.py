"""
## retriever.py — Query & Retrieval

### Steps
1. Embed the user query with `task_type="RETRIEVAL_QUERY"`.
2. Query ChromaDB collection with `n_results=TOP_K`.
3. Return list of dicts: `{"text": ..., "source": ..., "section": ..., "chunk_index": ..., "distance": ...}`.

Public API for other modules:
    retrieve(client, text_query) -> list[dict] | None
"""

# Internal
import time
import logging
from typing import Any

# External
import chromadb

from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import (
    TOP_K,
    CHROMA_DB_PATH,
    COLLECTION_NAME,
    EMBED_DELAY_SECS,
    EMBEDDING_MODEL,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def embed_query(
    client: genai.Client,
    query: str,
    delay_secs: float = EMBED_DELAY_SECS,
) -> list[float] | None:
    """Embed a user query string using the Gemini embedding model.

    Uses task_type="RETRIEVAL_QUERY" so the model optimises the vector for
    similarity search (contrast with RETRIEVAL_DOCUMENT used at ingest time).

    Rate limiting: the Gemini free tier allows 100 embedding requests per
    minute (≈ 1 every 0.6 s).  After every successful call this function
    sleeps for `delay_secs` to stay within that budget.  The default value
    (EMBED_DELAY_SECS = 0.65 s) gives ~92 RPM — comfortably under the cap.
    Pass delay_secs=0 to disable throttling, e.g. on a paid-tier account or
    in unit tests where the API is mocked.

    Args:
        client:     An authenticated google.genai.Client instance.
        query:      The raw query text to embed.
        delay_secs: Seconds to sleep after a successful call.

    Returns:
        A list of floats (the embedding vector), or None on failure.
        The Gemini embedding-001 model returns 3072-dimensional vectors.
    """
    try:
        result: types.EmbedContentResponse = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=query,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        vector: list[float] = result.embeddings[0].values

        # Throttle *after* a successful call so the quota counter resets
        # relative to actual API hits, not skipped queries.
        if delay_secs > 0:
            time.sleep(delay_secs)

        return vector
    except Exception as exc:  # noqa: BLE001 — intentional broad catch
        log.warning("Query embedding failed: %s", exc)
        return None


def semantic_search(query_vector: list[float]) -> dict[str, Any] | None:
    """Query the ChromaDB collection and return the TOP_K closest chunks.

    Args:
        query_vector: A float embedding vector produced by embed_query().

    Returns:
        The raw ChromaDB result dict (keys: "metadatas", "distances"), or
        None if the collection is empty or the query fails.
    """
    try:
        chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        collection = chroma_client.get_or_create_collection(name=COLLECTION_NAME)

        if collection.count() == 0:
            log.warning(
                "ChromaDB collection '%s' is empty. Run `python main.py --ingest` first.",
                COLLECTION_NAME,
            )
            return None

        return collection.query(
            query_embeddings=[query_vector],
            n_results=TOP_K,
            include=["metadatas", "distances"],
        )
    except Exception as exc:  # noqa: BLE001
        log.error("ChromaDB query failed: %s", exc)
        return None


def format_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the raw ChromaDB result into a list of context-chunk dicts.

    ChromaDB returns nested lists (one entry per query; we always send one).
    This function unwraps that structure and maps metadata field names to the
    schema expected by generator.py.

    Args:
        raw: The dict returned by collection.query() with "metadatas" and
             "distances" included.

    Returns:
        A list of dicts, each with:
            text        — raw chunk text (stored in ChromaDB metadata)
            source      — source page URL
            section     — markdown header the chunk came from
            chunk_index — 0-based position within the source page
            distance    — cosine distance (lower = closer match)
    """
    # Outer list is per-query; index 0 because we always send a single query.
    metadatas: list[dict[str, Any]] = raw["metadatas"][0]
    distances: list[float] = raw["distances"][0]

    return [
        {
            "text": meta.get("text", ""),
            "source": meta.get("url", ""),
            "section": meta.get("section", ""),
            "chunk_index": meta.get("chunk_index", -1),
            "distance": dist,
        }
        for meta, dist in zip(metadatas, distances)
    ]


def retrieve(client: genai.Client, text_query: str) -> list[dict[str, Any]] | None:
    """End-to-end retrieval: embed a query and return formatted context chunks.

    This is the primary function called by generator.py and main.py.

    Args:
        client:     An authenticated google.genai.Client instance.
        text_query: The user's natural-language question.

    Returns:
        A list of context-chunk dicts (see format_results), or None if
        embedding or the ChromaDB query fails.
    """
    query_vector = embed_query(client, text_query)
    if query_vector is None:
        log.error("Retrieval aborted: could not embed query.")
        return None

    raw = semantic_search(query_vector)
    if raw is None:
        return None

    return format_results(raw)


def main(text_query: str) -> None:
    load_dotenv()  # Ensure GOOGLE_API_KEY is available before the first API call
    gemini = genai.Client()

    chunks = retrieve(gemini, text_query)
    if not chunks:
        log.error("No results returned.")
        return

    for i, chunk in enumerate(chunks, start=1):
        print(f"\n--- Result {i} (distance: {chunk['distance']:.4f}) ---")
        print(f"Source : {chunk['source']}")
        print(f"Section: {chunk['section']}")
        print(f"Chunk  : {chunk['chunk_index']}")
        print(f"Text   :\n{chunk['text']}")


if __name__ == "__main__":
    text_query = "How do I use Conv3D?"
    main(text_query)
