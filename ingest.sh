#!/usr/bin/env bash
# ingest.sh — KeRAG ingestion pipeline
#
# Runs the four preprocessing + ingestion steps in order:
#   scrape.py      → wget mirror of keras.io/api docs → data/keras_docs/
#   extract.py     → trafilatura HTML → JSON           → data/extracted/
#   preprocess.py  → chunk + deduplicate               → data/chunks.jsonl
#   injest.py      → embed + upsert into ChromaDB      → chroma_db/
#
# Usage:
#   chmod +x ingest.sh
#   ./ingest.sh
#
# Re-running is safe: scraping overwrites the mirror, extraction overwrites
# JSON files, preprocessing rewrites chunks.jsonl, and ChromaDB upserts are
# idempotent (deterministic IDs).
#
# To skip scraping (docs already mirrored), comment out step 1.

set -euo pipefail  # Exit on error, unset variable, or pipe failure

echo "=== KeRAG Ingestion Pipeline ==="
echo ""
echo "WARNING: This pipeline scrapes, extracts, chunks, and embeds the full"
echo "Keras API docs. Runtime varies — expect 15 minutes to over an hour"
echo "depending on your internet connection and disk I/O speed."
echo ""
read -rp "Press Enter to continue, or Ctrl+C to abort... "
echo ""

echo "[1/4] Scraping Keras API docs (wget mirror)..."
python scrape.py

echo ""
echo "[2/4] Extracting HTML to JSON (trafilatura)..."
python extract.py

echo ""
echo "[3/4] Chunking and deduplicating..."
python preprocess.py

echo ""
echo "[4/4] Embedding chunks and ingesting into ChromaDB..."
python injest.py

echo ""
echo "Done. Run 'python kerag_cli.py' to start Q&A."
