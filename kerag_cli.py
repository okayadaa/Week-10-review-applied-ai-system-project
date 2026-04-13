"""
kerag_cli.py — Interactive Q&A over Keras API docs (KeRAG).

One genai.Client() is created at startup and shared across all modes:
    - retriever.py uses it for query embeddings  (embed_content)
    - generator.py uses it for text generation   (generate_content)

All three modes require a valid GOOGLE_API_KEY because even retrieval
depends on the Gemini embedding model to embed the user query before
querying ChromaDB.

Modes:
    1) Naive LLM   — query sent directly to Gemini, no retrieval
    2) Retrieval   — semantic search via ChromaDB, results printed with citations
    3) RAG         — retrieval + Gemini generation grounded in retrieved chunks

Usage:
    python kerag_cli.py
"""

import logging
import os
import time

from dotenv import load_dotenv

load_dotenv()  # Must happen before genai.Client() reads GOOGLE_API_KEY

from google import genai

from dataset import SAMPLE_QUERIES
from generator import naive_generate, rag_generate
from retriever import retrieve

logging.basicConfig(
    level=logging.WARNING,  # Suppress INFO-level noise from retriever/injest
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_DIVIDER = "=" * 60
_RATE_LIMIT_DELAY = 12  # seconds — Gemini free tier: 5 requests/minute

# Persists across menu selections so the rate-limit window is shared globally.
_last_api_call: float | None = None


def _rate_limit_wait() -> None:
    """Sleep only the remaining portion of the rate-limit window, if needed.

    If enough time has already elapsed since the last API call (e.g. the user
    spent time typing at the menu or composing their query), no sleep occurs.
    """
    global _last_api_call
    if _last_api_call is None:
        return
    elapsed = time.monotonic() - _last_api_call
    remaining = _RATE_LIMIT_DELAY - elapsed
    if remaining > 0:
        print(f"Rate limit: waiting {remaining:.1f}s before next API call...\n")
        time.sleep(remaining)


def _record_api_call() -> None:
    """Stamp the time of the most recent API call."""
    global _last_api_call
    _last_api_call = time.monotonic()


# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

def _create_client() -> genai.Client | None:
    """Create a single genai.Client shared for embedding and generation.

    Returns None and prints a warning if GOOGLE_API_KEY is missing or
    client initialisation fails.
    """
    if not os.getenv("GOOGLE_API_KEY"):
        print(
            "Warning: GOOGLE_API_KEY is not set.\n"
            "All modes require this key — query embedding and generation both\n"
            "depend on the Gemini API. Add it to your .env file and restart.\n"
        )
        return None
    try:
        return genai.Client()
    except Exception as exc:
        print(f"Warning: Could not initialise Gemini client: {exc}\n")
        return None


# ---------------------------------------------------------------------------
# Mode helpers
# ---------------------------------------------------------------------------

def _get_queries() -> tuple[list[str], str]:
    """Prompt for a single custom query or fall back to built-in samples."""
    print("\nPress Enter to run built-in sample queries.")
    custom = input("Or type a custom query: ").strip()
    if custom:
        return [custom], "custom query"
    return SAMPLE_QUERIES, "sample queries"


def run_naive(client: genai.Client) -> None:
    """Mode 1 — direct Gemini call, no retrieval."""
    queries, label = _get_queries()
    print(f"\nRunning naive LLM mode on {label}...\n")

    for query in queries:
        _rate_limit_wait()
        print(_DIVIDER)
        print(f"Question: {query}\n")
        answer = naive_generate(client, query)
        _record_api_call()
        print("Answer:")
        print(answer)
        print()


def run_retrieval(client: genai.Client) -> None:
    """Mode 2 — semantic search only, no generation."""
    queries, label = _get_queries()
    print(f"\nRunning retrieval-only mode on {label}...\n")

    for query in queries:
        _rate_limit_wait()
        print(_DIVIDER)
        print(f"Question: {query}\n")

        chunks = retrieve(client, query)
        _record_api_call()
        if not chunks:
            print(
                "No results found. "
                "Make sure the ChromaDB collection is populated — run ./ingest.sh first.\n"
            )
            continue

        for i, chunk in enumerate(chunks, start=1):
            snippet = chunk["text"][:200].replace("\n", " ")
            print(f"  [{i}] distance: {chunk['distance']:.4f}")
            print(f"       Source : {chunk['source']}")
            print(f"       Section: {chunk['section']}")
            print(f"       Text   : {snippet}...")
            print()


def run_rag(client: genai.Client) -> None:
    """Mode 3 — retrieval followed by Gemini generation with citations."""
    queries, label = _get_queries()
    print(f"\nRunning RAG mode on {label}...\n")

    for query in queries:
        _rate_limit_wait()
        print(_DIVIDER)
        print(f"Question: {query}\n")

        chunks = retrieve(client, query)
        if not chunks:
            _record_api_call()
            print(
                "No results found. "
                "Make sure the ChromaDB collection is populated — run ./ingest.sh first.\n"
            )
            continue

        answer = rag_generate(client, query, chunks)
        _record_api_call()
        print("Answer:")
        print(answer)

        print("\nCitations:")
        for i, chunk in enumerate(chunks, start=1):
            print(f"  [{i}] {chunk['source']} — {chunk['section']}")
        print()


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def _print_menu(has_client: bool) -> None:
    print("\nChoose a mode:")
    avail = "" if has_client else " (unavailable — GOOGLE_API_KEY not set)"
    print(f"  1) Naive LLM    — direct query to Gemini, no retrieval{avail}")
    print(f"  2) Retrieval    — semantic search with citations{avail}")
    print(f"  3) RAG          — retrieval + Gemini generation with citations{avail}")
    print("  q) Quit")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("KeRAG — Keras API Docs Q&A")
    print("===========================\n")

    client = _create_client()
    has_client = client is not None

    while True:
        _print_menu(has_client)
        choice = input("Enter choice: ").strip().lower()

        if choice == "q":
            print("\nGoodbye.")
            break
        elif choice in ("1", "2", "3"):
            if not has_client:
                print("\nAll modes require GOOGLE_API_KEY. Set it in .env and restart.\n")
                continue
            if choice == "1":
                run_naive(client)
            elif choice == "2":
                run_retrieval(client)
            elif choice == "3":
                run_rag(client)
        else:
            print("\nUnknown choice. Please pick 1, 2, 3, or q.\n")


if __name__ == "__main__":
    main()
