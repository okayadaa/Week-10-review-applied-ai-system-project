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
          │  split at markdown headers (one chunk per function/class)
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
| Chunking | Header-based + LangChain fallback | Respects API doc structure |
| Secrets | `python-dotenv` | Keeps your API key out of your code |

### One-line install
```bash
pip install google-genai chromadb langchain langchain-google-genai langchain-community trafilatura python-dotenv
```
`wget` is a system tool — on macOS: `brew install wget`. On Linux it's usually pre-installed.

---

## Project File Layout

```
rag_project/
├── .env              ← Your API key goes here (never commit this!)
├── config.py         ← All settings in one place
├── scrape.py         ← Step 1: wget mirror of keras.io/api
├── extract.py        ← Step 2: HTML → clean JSON via trafilatura
├── preprocess.py     ← Step 3: chunk + deduplicate → chunks.jsonl
├── ingest.py         ← Step 4: embed chunks + store in ChromaDB
├── retriever.py      ← Embed question → search ChromaDB
├── generator.py      ← Build prompt → call Gemini → return answer
├── main.py           ← The thing you actually run
└── data/
    ├── keras_docs/   ← Raw HTML from wget
    ├── extracted/    ← Cleaned JSON, one file per page
    └── chunks.jsonl  ← Final chunks ready for embedding
```

---

## Key Settings (config.py)

| Setting | Value | What it means |
|---|---|---|
| `CHUNK_SIZE` | 800 chars | Each piece of text stored in the DB |
| `CHUNK_OVERLAP` | 100 chars | How much adjacent chunks share (prevents cutting a sentence in half at a boundary) |
| `TOP_K` | 5 | How many chunks to retrieve per question |
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
| `section` | `## call() method` | Which part of the page this came from |
| `chunk_index` | `2` | Which piece of this page |

---

## How to Use It (Once Built)

```bash
# Step 1: Run the full preprocessing + ingestion pipeline
python main.py --all

# Or run steps individually if something fails
python main.py --scrape       # download keras docs (~once ever)
python main.py --extract      # clean HTML → JSON
python main.py --preprocess   # chunk + deduplicate
python main.py --ingest       # embed + load into ChromaDB

# Step 2: Ask questions
python main.py
> What arguments does the Dense layer accept?
> How does Dropout behave differently during training vs inference?
```

---

## Important Notes

**Why header-based chunking (not character splitting) for API docs:**
API documentation is already logically structured — each function or class is a natural unit. Splitting at `##` headers keeps each chunk self-contained (signature + description together), which leads to much more precise retrieval than arbitrary character-count splits would.

**Why deduplication matters:**
The Keras docs have index pages (e.g. `keras.io/api/layers/`) that list and summarize every layer. Each layer also has its own detail page. Without deduplication, you'd end up with two near-identical chunks for every item — the index summary and the full detail. This dilutes retrieval and wastes your embedding quota.

**Two different task types for embeddings:**
When embedding document chunks you use `RETRIEVAL_DOCUMENT`, and when embedding a user's question you use `RETRIEVAL_QUERY`. This is intentional — it tells the model what role the text plays and meaningfully improves retrieval accuracy.

**The AI only answers from the docs:**
The prompt instructs Gemini to answer *only* from the retrieved context. If the answer isn't in the Keras docs, it will say so rather than guessing.

**Free tier limits:**
The Gemini free tier allows 1,000 embedding requests/day. The full Keras API docs will likely produce several thousand chunks — run `--ingest` over a couple of days if needed, or batch carefully.
