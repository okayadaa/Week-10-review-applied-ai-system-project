# KeRAG: All Local Keras API Docs RAG-MCP Server

## What Are We Building?
A **local RAG (Retrieval-Augmented Generation)** system built on the **Keras online API docs**. It scrapes the docs from the web, cleans them up, stores them in a local vector database, and lets you ask natural language questions about Keras — grounded in the actual documentation.

---

## The Full RAG Flow

```
╔══════════════════════════════════════════════════════════════════╗
║                     PREPROCESSING PHASE                          ║
║                   (run once to build the DB)                     ║
╚══════════════════════════════════════════════════════════════════╝

  🌐 keras.io/api/ (live website)
          │
          │  scrape.py — wget mirror
          ▼
  📁 data/keras_docs/  (raw .html files on disk)
          │
          │  extract.py — trafilatura
          │  strips nav, footer, boilerplate
          ▼
  📄 data/extracted/   (one clean .json per page)
          │
          │  preprocess.py — chunk + deduplicate
          │  split at **Bold** section labels (one chunk per logical section)
          │  drop index-page summaries that duplicate detail pages
          ▼
  📋 data/chunks.jsonl  (final clean chunks, ready to embed)
          │
          │  ingest.py — gemini-embedding-001
          │  task_type = RETRIEVAL_DOCUMENT
          ▼
  🗄️  ChromaDB  (saved to disk at ./chroma_db)


╔══════════════════════════════════════════════════════════════════╗
║                          QUERY PHASE                             ║
║                    (runs on every question)                      ║
╚══════════════════════════════════════════════════════════════════╝

  💬 User's Question
          │
          │  gemini-embedding-001
          │  task_type = RETRIEVAL_QUERY
          ▼
  🔢 Question Vector
          │
          │  ChromaDB similarity search
          ▼
  📑 Top 5 Most Relevant Chunks  (with URL + section citations)
          │
          │  Stuffed into a prompt
          ▼
  🤖 Gemini 2.5 Flash
          │
          ▼
  💡 Answer  (grounded in the Keras docs)
```

---

## Tech Stack

| What | Tool | Why |
|---|---|---|
| Chat / generation | Gemini 2.5 Flash | Free tier, fast, smart |
| Embedding | `gemini-embedding-001` | Same API key, free tier (1000 req/day) |
| Vector database | ChromaDB | Runs locally, no setup needed |
| Web scraping | `wget` (system tool) | Reliable recursive mirror of static sites |
| HTML cleaning | `trafilatura` | Purpose-built for extracting article content from HTML |
| Chunking | Bold-label–based + LangChain fallback | Respects API doc structure |
| Secrets | `python-dotenv` | Keeps your API key out of your code |

### One-line install
```bash
pip install -r requirements.txt
```
`wget` is a system tool — on macOS: `brew install wget`. On Linux it's usually pre-installed.

---

## Project File Layout

```
rag_project/
├── .env              ← Your API key goes here (never commit this!)
├── config.py         ← All settings in one place
├── ingest.sh         ← Run the full preprocessing + ingestion pipeline
├── scrape.py         ← Step 1: wget mirror of keras.io/api
├── extract.py        ← Step 2: HTML → clean JSON via trafilatura
├── preprocess.py     ← Step 3: chunk + deduplicate → chunks.jsonl
├── ingest.py         ← Step 4: embed chunks + store in ChromaDB
├── retriever.py      ← Embed question → search ChromaDB
├── generator.py      ← Build prompt → call Gemini → return answer
├── kerag_cli.py      ← Interactive Q&A CLI (modes: naive / retrieval / RAG)
├── dataset.py        ← Built-in sample queries for testing
└── data/
    ├── keras_docs/   ← Raw HTML from wget
    ├── extracted/    ← Cleaned JSON, one file per page
    └── chunks.jsonl  ← Final chunks ready for embedding
```

---

## Key Settings (config.py)

| Setting | Value | What it means |
|---|---|---|
| `CHUNK_SIZE` | 1000 chars | Max size of each text piece stored in the DB |
| `CHUNK_OVERLAP` | 100 chars | How much adjacent chunks share (prevents cutting a sentence in half at a boundary) |
| `TOP_K` | 5 | How many chunks to retrieve per question |
| `EMBED_DELAY_SECS` | 0.65 s | Sleep between embedding calls to stay under the free-tier rate limit (100 req/min) |
| `EMBEDDING_MODEL` | `gemini-embedding-001` | Converts text → numbers |
| `GENERATIVE_MODEL` | `gemini-2.5-flash` | Writes the final answer |

---

## What Gets Stored Per Chunk

Every chunk in ChromaDB has a **vector** (for search) and **metadata** (for context):

| Field | Example | Purpose |
|---|---|---|
| `text` | *"Dense layer — applies transformation..."* | Passed to Gemini as context |
| `url` | `keras.io/api/layers/core/dense` | Shown as a citation link |
| `title` | `Dense layer` | Human-readable page name |
| `section` | `Arguments` | Which bold-label section of the page this came from |
| `chunk_index` | `2` | Which piece of this page |

---

## How to Use It (Once Built)

```bash
# Step 1: Run the full preprocessing + ingestion pipeline (~1 hour, run once)
./ingest.sh

# Or run steps individually if something fails
python scrape.py        # download keras docs (~once ever)
python extract.py       # clean HTML → JSON
python preprocess.py    # chunk + deduplicate
python ingest.py        # embed + load into ChromaDB

# Step 2: Ask questions
python kerag_cli.py
# Choose a mode from the interactive menu:
#   1) Naive LLM    — direct query to Gemini, no retrieval
#   2) Retrieval    — semantic search with citations
#   3) RAG          — retrieval + Gemini generation with citations
```

---

## Important Notes

**Why bold-label chunking (not character splitting) for API docs:**
API documentation is already logically structured. When trafilatura converts Keras HTML pages to markdown, the original `h4` section headers (e.g. *Arguments*, *Returns*, *Call arguments*) become standalone bold lines (`**Arguments**`). Splitting on these bold labels keeps each chunk self-contained — signature and its description stay together — which leads to much more precise retrieval than arbitrary character-count splits would.

**Why deduplication matters:**
The Keras docs have index pages (e.g. `keras.io/api/layers/`) that list and summarize every layer. Each layer also has its own detail page. Without deduplication, you'd end up with two near-identical chunks for every item — the index summary and the full detail. This dilutes retrieval and wastes your embedding quota.

**Two different task types for embeddings:**
When embedding document chunks you use `RETRIEVAL_DOCUMENT`, and when embedding a user's question you use `RETRIEVAL_QUERY`. This is intentional — it tells the model what role the text plays and meaningfully improves retrieval accuracy.

**The AI only answers from the docs:**
The prompt instructs Gemini to answer *only* from the retrieved context. If the answer isn't in the Keras docs, it will say so rather than guessing.

**Free tier limits:**
The Gemini free tier allows 100 embedding requests/minute. `ingest.py` automatically throttles itself via `EMBED_DELAY_SECS = 0.65s` between calls (~92 RPM), so you can leave it running unattended. The interactive CLI adds a 12-second gap between generation calls to stay within the 5 requests/minute generation limit.
