"""
preprocess.py — Chunk + Deduplicate

Reads all .json files from data/extracted/, chunks each page on markdown
headers, deduplicates index-page summaries against detail pages, and writes
data/chunks.jsonl (one JSON object per line).

CLI: python preprocess.py
"""

# Internal
import json
import logging
from typing import List
from pathlib import Path

# External
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

# Global Variables
EXTRACTED_DIR = Path("data/extracted")
OUTPUT_PATH = Path("data/chunks.jsonl")
CHUNK_SIZE = 1000       # max characters per chunk passed to the embedding model
CHUNK_OVERLAP = 100    # characters shared between adjacent chunks to avoid cutting mid-sentence
DEDUP_THRESHOLD = 0.85 # fraction of index-chunk tokens that must appear in a detail chunk to trigger a drop

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Instantiate once — these are stateless and safe to reuse across pages
_header_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=[("##", "h2"), ("###", "h3")],
    strip_headers=False,  # keep the header text inside the chunk so the section label is searchable
)
_char_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)


def chunk_page(doc: dict) -> list[dict]:
    """
    Split one extracted page into chunks.

    Primary split: markdown ## / ### headers — one chunk per logical section
    (function signature + description stay together).
    Fallback: RecursiveCharacterTextSplitter for any section still over CHUNK_SIZE.
    """
    url = doc["url"]
    title = doc.get("title", "")
    content = doc.get("content", "")

    header_chunks: List = _header_splitter.split_text(content)

    chunks = []
    for hchunk in header_chunks:
        text = hchunk.page_content.strip()
        if not text:
            continue

        # Prefer ## label; fall back to ### if no ## was present in this section
        section = hchunk.metadata.get("h2") or hchunk.metadata.get("h3") or ""
        
        if section != "": 
            print(f"This is section {section}")

        sub_texts = _char_splitter.split_text(text) if len(text) > CHUNK_SIZE else [text]

        for sub in sub_texts:
            if sub.strip():
                chunks.append({
                    "url": url,
                    "title": title,
                    "section": section,
                    "chunk_index": len(chunks),  # 0-based position within this page
                    "text": sub,
                })

    return chunks


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def _token_set(text: str) -> set[str]:
    return set(text.lower().split())


def _containment(small: set, large: set) -> float:
    """
    Fraction of `small` tokens that are present in `large`.
    Returns 1.0 when small is a perfect subset of large.
    """
    if not small:
        return 0.0
    return len(small & large) / len(small)


def _is_index_url(url: str) -> bool:
    # Index pages end with "/index" or are exactly "index"
    return url.endswith("/index") or url == "index"


def deduplicate(all_chunks: list[dict], threshold: float = DEDUP_THRESHOLD) -> list[dict]:
    """
    Remove index-page chunks that are near-subsets of a detail-page chunk.

    Strategy:
      1. Partition chunks into index-page vs detail-page buckets.
      2. Pre-compute token sets for all detail chunks (done once).
      3. For each index chunk, compute containment against every detail token set.
         If containment >= threshold, the index chunk is a weaker duplicate → drop it.
      4. Return detail chunks + surviving index chunks.
    """
    index_chunks = [c for c in all_chunks if _is_index_url(c["url"])]
    detail_chunks = [c for c in all_chunks if not _is_index_url(c["url"])]

    # Pre-build token sets so we don't re-tokenize on every comparison
    detail_token_sets = [_token_set(c["text"]) for c in detail_chunks]

    kept_index, dropped = [], 0
    for chunk in index_chunks:
        ctokens = _token_set(chunk["text"])
        is_dup = any(_containment(ctokens, dt) >= threshold for dt in detail_token_sets)
        if is_dup:
            dropped += 1
        else:
            kept_index.append(chunk)

    logger.info(
        f"Deduplication: dropped {dropped}/{len(index_chunks)} index-page chunks "
        f"(threshold={threshold})"
    )
    return detail_chunks + kept_index


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    json_files = sorted(EXTRACTED_DIR.glob("*.json"))
    logger.info(f"Processing {len(json_files)} extracted files from {EXTRACTED_DIR}")

    all_chunks: list[dict] = []
    for json_path in json_files[:]:
        try:
            doc = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Skipped {json_path.name}: {e}")
            continue
        all_chunks.extend(chunk_page(doc))

    # logger.info(f"Chunks before dedup: {len(all_chunks)}")

    # clean_chunks = deduplicate(all_chunks)

    # logger.info(f"Chunks after dedup:  {len(clean_chunks)}")

    # # Write one JSON object per line — JSONL format lets ingest.py stream records
    # # without loading the full file into memory
    # with OUTPUT_PATH.open("w", encoding="utf-8") as f:
    #     for chunk in clean_chunks:
    #         f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    # logger.info(f"Wrote {len(clean_chunks)} chunks → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
