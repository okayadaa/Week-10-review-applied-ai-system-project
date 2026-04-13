# KeRAG Project Specification (Claude Implementation Guide)

## (Human) Description
KeRAG is an ALL local RAG-MCP server for Keras Online API Docs. 

## Project Overview
Build a local Retrieval-Augmented Generation (RAG) system using Gemini APIs and ChromaDB. The system ingests documents, embeds them into a local vector database, and answers user questions by retrieving relevant chunks and passing them to a generative model.

---

## Stack

| Role | Tool | Notes |
|---|---|---|
| Embedding model | `gemini-embedding-001` | `task_type=RETRIEVAL_DOCUMENT` for indexing, `RETRIEVAL_QUERY` for queries |
| Vector database | `chromadb` | Persistent local storage on disk |
| Generative model | `gemini-2.5-flash` | Via `google-genai` SDK |
| Chunking | `langchain` | `RecursiveCharacterTextSplitter` |
| Env management | `python-dotenv` | Load `GOOGLE_API_KEY` from `.env` |

### Install
```bash
pip install -r requirements.txt
```

---

## Environment
- Store `GOOGLE_API_KEY` in a `.env` file at project root.
- Load with `python-dotenv` before any API calls.

---

## Project Structure
```
KeRAG/
├── .env                  # GOOGLE_API_KEY=your_key_here
├── specs_claude.md
├── kerag_cli.py          # Interactive Q&A CLI (modes: naive / retrieval / RAG)
├── ingest.sh             # Pipeline runner: scrape → extract → preprocess → ingest
├── scrape.py             # wget mirror of keras.io/api docs
├── extract.py            # trafilatura over .html → clean .json per page
├── preprocess.py         # chunk + deduplicate cleaned docs → JSONL output
├── ingest.py             # embed chunks + store in ChromaDB
├── retriever.py          # Query embedding + ChromaDB similarity search
├── generator.py          # Prompt construction + Gemini 2.5 Flash call
├── config.py             # Constants (chunk size, overlap, top-k, model names, DB path)
├── dataset.py            # SAMPLE_QUERIES for testing
├── requirements.txt      # Contains the required Python libraries to run KeRAG
└── data/
    ├── keras_docs/       # Raw wget mirror output (.html files)
    ├── extracted/        # One .json per page after trafilatura
    └── chunks.jsonl      # Final deduplicated chunks ready for ingestion
```

---

---

## Preprocessing Pipeline

### scrape.py — wget Mirror

Run a recursive wget mirror of the Keras API docs. Output goes to `data/keras_docs/`.

```python
import subprocess

KERAS_URL = "https://keras.io/api/"
OUTPUT_DIR = "data/keras_docs"

def scrape():
    subprocess.run([
        "wget",
        "--mirror",
        "--convert-links",
        "--adjust-extension",
        "--page-requisites",
        "--no-parent",
        "-P", OUTPUT_DIR,
        KERAS_URL
    ], check=True)
```

CLI: `python scrape.py`

---

### extract.py — HTML → Clean JSON

Use `trafilatura` to strip nav, footer, and boilerplate from every `.html` file. Output one `.json` file per page to `data/extracted/`.

```bash
pip install trafilatura
```

#### Output schema per page
```python
{
    "url": str,       # reconstructed from file path, e.g. "keras.io/api/layers/dense"
    "title": str,     # <title> tag or trafilatura-extracted title
    "content": str    # clean body text, markdown-ish formatting preserved
}
```

#### Implementation notes
- Walk `data/keras_docs/` recursively for all `.html` files.
- Extract title via `trafilatura.extract_metadata(html).title` before extracting body content.
- Use `trafilatura.extract(html, output_format="markdown", include_formatting=True, include_tables=True)` — `output_format="markdown"` and `include_formatting=True` preserve headers and code blocks for the chunking step; `include_tables=True` retains API parameter tables.
- Skip files where trafilatura returns `None` (navigation-only pages, redirects). Log skipped files. Also skip pages where the extracted content is non-None but empty after stripping.
- Reconstruct `url` from the file path by stripping the `data/keras_docs/` prefix and `.html` extension.
- Write output as `data/extracted/<sanitized_filename>.json`.

CLI: `python extract.py`

---

### preprocess.py — Chunk + Deduplicate

Reads all `.json` files from `data/extracted/`, chunks them, deduplicates, and writes `data/chunks.jsonl`.

#### Chunking strategy
Split on **bold label lines** (lines whose entire content is `**Label**`, e.g. `**Arguments**`, `**Returns**`) as primary split points — each logical section of an API page becomes one chunk. This pattern is what trafilatura's markdown output produces for Keras doc section headers and is preferable to raw `##`/`###` splitting for these pages.

Fall back to `RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)` for any section that still exceeds `CHUNK_SIZE` characters after bold-header splitting.

#### Deduplication strategy
Index pages (e.g. `keras.io/api/layers/`) list items that have their own dedicated detail pages. These create near-duplicate content in the vector DB, which dilutes retrieval quality.

Detection heuristic: if a chunk's text is a subset (>85% token overlap) of a chunk from a detail page, drop it. Prefer the detail page.

Simple implementation: after chunking all pages, build a set of content hashes. For pages whose URL ends in `/` (index pages), check each of their chunks — if an identical or near-identical chunk exists from a non-index URL, drop the index version.

#### Output schema per chunk (JSONL)
```python
{
    "url": str,          # source page URL
    "title": str,        # page title
    "section": str,      # header text of the section this chunk came from
    "chunk_index": int,  # index within this page (0-based)
    "text": str          # chunk text passed to embedding model
}
```

CLI: `python preprocess.py`

---

## config.py — Constants
```python
EMBEDDING_MODEL = "gemini-embedding-001"
GENERATIVE_MODEL = "gemini-2.5-flash"
CHROMA_DB_PATH = "./chroma_db"
COLLECTION_NAME = "rag_documents"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100
TOP_K = 3

# Gemini free-tier embedding limit: 100 requests per minute.
# 0.65 s/call ≈ 92 RPM — stays under the cap with a small safety buffer.
# Set to 0 to disable throttling (e.g. in unit tests or paid-tier accounts).
EMBED_DELAY_SECS = 0.65
```

---

## ingest.py — Ingestion Pipeline

### Steps
1. Read all records from `data/chunks.jsonl` (output of `preprocess.py`).
2. For each chunk, call `gemini-embedding-001` with `task_type="RETRIEVAL_DOCUMENT"`.
3. Upsert into ChromaDB with the following metadata per chunk.

### Metadata Schema (per chunk)
```python
{
    "url": str,          # source page URL
    "title": str,        # page title
    "section": str,      # header/section the chunk came from
    "chunk_index": int,  # index within this page (0-based)
    "text": str          # raw chunk text — REQUIRED for retrieval
}
```

### ChromaDB Document ID
Use a deterministic ID: `f"{url}_chunk_{chunk_index}"` (URL-safe: replace `/` and `.` with `_`) to allow safe re-ingestion (upsert deduplicates).

### Embedding Call
```python
from google import genai
from google.genai import types

client = genai.Client()
result = client.models.embed_content(
    model="gemini-embedding-001",
    contents=chunk_text,
    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT")
)
vector = result.embeddings[0].values
```

---

## retriever.py — Query & Retrieval

### Steps
1. Embed the user query with `task_type="RETRIEVAL_QUERY"`.
2. Query ChromaDB collection with `n_results=TOP_K`.
3. Return list of dicts: `{"text": ..., "source": ..., "section": ..., "chunk_index": ..., "distance": ...}`.

### Embedding Call
```python
result = client.models.embed_content(
    model="gemini-embedding-001",
    contents=user_query,
    config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY")
)
query_vector = result.embeddings[0].values
```

### ChromaDB Query
```python
results = collection.query(
    query_embeddings=[query_vector],
    n_results=TOP_K,
    include=["metadatas", "distances"]
)
```

---

## generator.py — Answer Generation

Two public functions. The caller (`kerag_cli.py`) passes the shared `genai.Client`.

### `naive_generate(client, query) -> str`
Direct LLM call with no retrieval context. Used in mode 1.
Returns an error string (not an exception) on API failure.

### `rag_generate(client, query, chunks) -> str`
Builds a grounded prompt from retrieved chunks and calls Gemini. Used in mode 3.
Each chunk in `chunks` is a dict with keys: `text`, `source`, `section`, `chunk_index`, `distance`.

### Prompt Templates
**Naive:**
```
You are a helpful Keras documentation assistant.
Answer the following question as clearly and concisely as possible.

Question: {query}

Answer:
```

**RAG:**
```
You are a helpful Keras documentation assistant. Answer the user's question
using ONLY the context provided below. If the answer is not contained in the
context, say "I don't have enough information to answer that."

Context:
[1] Source: {url} | Section: {section}
{chunk_text}
...

Question: {query}

Answer:
```

### Gemini Call
```python
from google import genai

response = client.models.generate_content(
    model=GENERATIVE_MODEL,
    contents=prompt,
)
return response.text
```

---

## kerag_cli.py — Interactive Q&A Entry Point

### Behaviour
One `genai.Client()` is created at startup and shared across all modes —
both retrieval (query embedding) and generation use the same client instance.
All three modes require `GOOGLE_API_KEY` because query embedding calls the
Gemini API.

A global rate-limit guard (`_RATE_LIMIT_DELAY = 12s`) ensures at least 12 seconds
pass between consecutive API calls, respecting the Gemini free tier's
5 requests-per-minute generation limit. If enough time has already elapsed
(e.g. the user spent time at the menu), no sleep occurs.

#### Modes
| # | Name | Description |
|---|---|---|
| 1 | Naive LLM | Query sent directly to Gemini with no retrieval context |
| 2 | Retrieval | Semantic search via ChromaDB; results printed with source/section citations |
| 3 | RAG | Retrieval + Gemini generation grounded in the top-K chunks |

For each mode the user can either type a single custom query or press Enter
to run all queries in `dataset.SAMPLE_QUERIES`.

### CLI Usage
```bash
python kerag_cli.py
```

---

## ingest.sh — Ingestion Pipeline Runner

Runs the four preprocessing and ingestion steps in sequence.
Re-running is safe: all steps are idempotent.

```bash
chmod +x ingest.sh
./ingest.sh
```

Steps executed:
```
[1/4] scrape.py      → wget mirror  → data/keras_docs/
[2/4] extract.py     → HTML → JSON  → data/extracted/
[3/4] preprocess.py  → chunk + dedup → data/chunks.jsonl
[4/4] ingest.py      → embed + upsert → chroma_db/
```

To skip scraping when the mirror already exists, comment out step 1 in the script.

---

## ChromaDB Setup (shared utility)
```python
import chromadb

def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    return client.get_or_create_collection(name=COLLECTION_NAME)
```

---

## Error Handling Requirements
- Catch and log Gemini API errors (rate limits, invalid key).
- Skip chunks that fail to embed (log warning, continue ingestion).
- If ChromaDB collection is empty at query time, print a friendly message prompting the user to ingest documents first.
- Validate file extension before loading (only `.pdf` and `.txt` supported).

---

## Pipeline Order

```
scrape.py → extract.py → preprocess.py → ingest.py → main.py (Q&A)
```

Each step is independent and can be re-run in isolation. Scraping only needs to happen once unless the docs are updated.

---

## Constraints
- Everything runs locally except Gemini API calls (embedding + generation) and the initial wget scrape.
- No LangChain chains or agents — use the SDK and ChromaDB directly for transparency.
- Do not use `localStorage`, servers, or any remote database.
- Keep all secrets in `.env`, never hardcode the API key.
- `chunks.jsonl` is the contract between `preprocess.py` and `ingest.py` — the schema must not change without updating both files.