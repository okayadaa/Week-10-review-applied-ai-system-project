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
| Document loading | `langchain-community` | `PyPDFLoader`, `TextLoader` |
| Chunking | `langchain` | `RecursiveCharacterTextSplitter` |
| Env management | `python-dotenv` | Load `GOOGLE_API_KEY` from `.env` |

### Install
```bash
pip install google-genai chromadb langchain langchain-google-genai langchain-community pypdf python-dotenv
```

---

## Environment
- Store `GOOGLE_API_KEY` in a `.env` file at project root.
- Load with `python-dotenv` before any API calls.

---

## Project Structure
```
rag_project/
├── .env                  # GOOGLE_API_KEY=your_key_here
├── specs_claude.md
├── main.py               # Entry point: CLI loop for Q&A
├── scrape.py             # wget mirror of keras.io/api docs
├── extract.py            # trafilatura over .html → clean .json per page
├── preprocess.py         # chunk + deduplicate cleaned docs → JSONL output
├── ingest.py             # embed chunks + store in ChromaDB
├── retriever.py          # Query embedding + ChromaDB similarity search
├── generator.py          # Prompt construction + Gemini 2.5 Flash call
├── config.py             # Constants (chunk size, overlap, top-k, model names, DB path)
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
- Use `trafilatura.extract(html, include_formatting=True)` — the `include_formatting=True` flag preserves headers and code blocks, which are critical for the chunking step.
- Skip files where trafilatura returns `None` (navigation-only pages, redirects). Log skipped files.
- Reconstruct `url` from the file path by stripping the `data/keras_docs/` prefix and `.html` extension.
- Write output as `data/extracted/<sanitized_filename>.json`.

CLI: `python extract.py`

---

### preprocess.py — Chunk + Deduplicate (REVIEW BY OWNER PRIOR OF IMPLEMENTING)

Reads all `.json` files from `data/extracted/`, chunks them, deduplicates, and writes `data/chunks.jsonl`.

#### Chunking strategy
Split on **markdown headers** (`##`, `###`) as primary split points — each function signature + its description becomes one chunk. This is preferable to character-count splitting for API docs because each function/class is a self-contained unit of meaning.

Fall back to `RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)` for any section that still exceeds `CHUNK_SIZE` after header splitting.

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
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TOP_K = 5
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
3. Return list of dicts: `{"text": ..., "source": ..., "page_number": ..., "chunk_index": ...}`.

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

### Prompt Template
```
You are a helpful assistant. Answer the user's question using ONLY the context provided below.
If the answer is not contained in the context, say "I don't have enough information to answer that."

Context:
{joined_chunk_texts}

Question: {user_question}

Answer:
```

### Gemini Call
```python
from google import genai

client = genai.Client()
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=prompt
)
return response.text
```

---

## main.py — Entry Point

### Behaviour
- `--scrape`: run `scrape.py` (wget mirror).
- `--extract`: run `extract.py` (trafilatura over HTML).
- `--preprocess`: run `preprocess.py` (chunk + deduplicate).
- `--ingest`: run `ingest.py` (embed + store in ChromaDB).
- `--all`: run all four steps in sequence.
- Default (no flags): interactive Q&A loop.
- Print retrieved source URL and section as citations alongside the answer.

### CLI Usage
```bash
# Full pipeline from scratch
python main.py --all

# Or step by step
python main.py --scrape
python main.py --extract
python main.py --preprocess
python main.py --ingest

# Start Q&A
python main.py
```

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