"""
ingest.py — Embed chunks and store them in ChromaDB.

Pipeline position: preprocess.py → **ingest.py** → main.py (Q&A)

Reads every record from data/chunks.jsonl, calls the Gemini embedding
model once per chunk, and upserts the resulting vector + metadata into
a persistent ChromaDB collection.

Re-running this script is safe: document IDs are deterministic, so
ChromaDB will overwrite existing entries rather than create duplicates.
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import (
    CHROMA_DB_PATH,
    COLLECTION_NAME,
    EMBED_DELAY_SECS,
    EMBEDDING_MODEL,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CHUNKS_PATH = Path("data/chunks.jsonl")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc_id(url: str, chunk_index: int) -> str:
    """Return a URL-safe ChromaDB document ID for a chunk.

    ChromaDB requires IDs to be unique strings without special characters
    that could confuse the underlying SQLite storage.  We replace the most
    common offenders (slashes and dots) with underscores and append the
    chunk index so every chunk from the same page is distinct.

    Example:
        url="keras.io/api/layers/dense/", chunk_index=2
        → "keras_io_api_layers_dense__chunk_2"
    """
    safe_url = re.sub(r"[/.]", "_", url)
    return f"{safe_url}_chunk_{chunk_index}"


def get_collection() -> chromadb.Collection:
    """Open (or create) the persistent ChromaDB collection.

    chromadb.PersistentClient stores the vector index on disk at
    CHROMA_DB_PATH so embeddings survive between Python sessions.
    get_or_create_collection is idempotent — safe to call on every run.
    """
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return client.get_or_create_collection(name=COLLECTION_NAME)


def embed_chunk(
    client: genai.Client,
    text: str,
    delay_secs: float = EMBED_DELAY_SECS,
) -> list[float] | None:
    """Call the Gemini embedding model and return the embedding vector.

    Uses task_type="RETRIEVAL_DOCUMENT" which tells the model to optimise
    the embedding for being retrieved later (as opposed to RETRIEVAL_QUERY,
    which is used at query time in retriever.py).

    Rate limiting: the Gemini free tier allows 100 embedding requests per
    minute (≈ 1 every 0.6 s).  After every successful call this function
    sleeps for `delay_secs` to stay within that budget.  The default value
    (EMBED_DELAY_SECS = 0.65 s) gives ~92 RPM — comfortably under the cap.
    Pass delay_secs=0 to disable throttling, e.g. on a paid-tier account or
    in unit tests where the API is mocked.

    Returns None and logs a warning if the API call fails for any reason
    (rate limit, invalid key, network error, …) so the caller can skip the
    problematic chunk and continue ingesting the rest.

    Args:
        client:     An authenticated google.genai.Client instance.
        text:       The raw chunk text to embed.
        delay_secs: Seconds to sleep after a successful call. Adjust this
                    if you upgrade to a higher API quota tier.

    Returns:
        A list of floats (the embedding vector), or None on failure.
        The Gemini embedding-001 model returns 3072-dimensional vectors.
    """
    try:
        result: types.EmbedContentResponse = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        # result.embeddings is a list[ContentEmbedding]; we always pass a
        # single string so index 0 is the only entry.
        vector: list[float] = result.embeddings[0].values

        # Throttle *after* a successful call so the quota counter resets
        # relative to actual API hits, not skipped chunks.
        if delay_secs > 0:
            time.sleep(delay_secs)

        return vector
    except Exception as exc:  # noqa: BLE001 — intentional broad catch
        log.warning("Embedding failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main ingestion logic
# ---------------------------------------------------------------------------

def load_chunks(path: Path) -> list[dict[str, Any]]:
    """Read every JSON record from a JSONL file and return them as a list.

    Each line in chunks.jsonl is a self-contained JSON object (one chunk).
    Lines that cannot be parsed are skipped with a warning so a single
    corrupt line does not abort the entire ingestion run.

    Expected keys per record (see preprocess.py for the contract):
        url, title, section, chunk_index, text
    """
    chunks: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                chunks.append(json.loads(line))
            except json.JSONDecodeError as exc:
                log.warning("Skipping malformed JSON on line %d: %s", lineno, exc)
    return chunks


def ingest(chunks: list[dict[str, Any]]) -> None:
    """Embed every chunk and upsert it into ChromaDB.

    ChromaDB's collection.upsert() inserts new documents and overwrites
    existing ones (matched by ID), so this function is idempotent.

    Chunks that fail to embed are logged and skipped; the rest are batched
    into a single upsert call for efficiency.  ChromaDB accepts parallel
    lists: ids, embeddings, metadatas, and documents must all be the same
    length and correspond positionally.

    The "documents" field stores the raw chunk text inside ChromaDB so it
    can be returned in query results without a secondary lookup.  We also
    duplicate the text inside "metadatas" (keyed as "text") because that is
    the field retriever.py reads when constructing the prompt context.
    """
    load_dotenv()  # Ensure GOOGLE_API_KEY is available before the first API call
    gemini = genai.Client()
    collection = get_collection()

    # Accumulators for a single bulk upsert at the end.
    ids: list[str] = []
    embeddings: list[list[float]] = []
    metadatas: list[dict[str, Any]] = []
    documents: list[str] = []

    skipped = 0

    for i, chunk in enumerate(chunks):
        url: str = chunk.get("url", "")
        title: str = chunk.get("title", "")
        section: str = chunk.get("section", "")
        chunk_index: int = chunk.get("chunk_index", i)
        text: str = chunk.get("text", "")

        if not text:
            log.warning("Chunk %d has empty text, skipping.", i)
            skipped += 1
            continue

        vector = embed_chunk(gemini, text)
        if vector is None:
            # embed_chunk already logged the warning.
            skipped += 1
            continue

        doc_id = _make_doc_id(url, chunk_index)

        ids.append(doc_id)
        embeddings.append(vector)
        # ChromaDB metadata values must be str | int | float | bool.
        # chunk_index is already an int; the rest are strings.
        metadatas.append(
            {
                "url": url,
                "title": title,
                "section": section,
                "chunk_index": chunk_index,
                # Store the raw text here so retriever.py can surface it
                # alongside the similarity score without a second query.
                "text": text,
            }
        )
        # "documents" is ChromaDB's native full-text field; keeping it in
        # sync with metadata["text"] lets us use ChromaDB's optional keyword
        # search features in the future.
        documents.append(text)

        if (i + 1) % 100 == 0:
            log.info("Embedded %d / %d chunks…", i + 1, len(chunks))

    if not ids:
        log.error("No chunks were successfully embedded. Nothing to upsert.")
        return

    log.info(
        "Upserting %d chunks into ChromaDB (skipped %d)…",
        len(ids),
        skipped,
    )
    # upsert accepts lists; matching positions tie each id → embedding →
    # metadata → document together.
    collection.upsert(
        ids=ids,
        embeddings=embeddings,
        metadatas=metadatas,
        documents=documents,
    )
    log.info(
        "Done. Collection '%s' now contains %d documents.",
        COLLECTION_NAME,
        collection.count(),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not CHUNKS_PATH.exists():
        log.error(
            "Chunks file not found at '%s'. Run preprocess.py first.",
            CHUNKS_PATH,
        )
        sys.exit(1)

    log.info("Loading chunks from %s …", CHUNKS_PATH)
    chunks = load_chunks(CHUNKS_PATH)
    log.info("Loaded %d chunks.", len(chunks))

    ingest(chunks)


if __name__ == "__main__":
    main()
