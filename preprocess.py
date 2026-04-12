"""
preprocess.py — Chunk + Deduplicate

Reads all .json files from data/extracted/, chunks each page on markdown
headers, deduplicates index-page summaries against detail pages, and writes
data/chunks.jsonl (one JSON object per line).

CLI: python preprocess.py
"""

# Internal
import re
import json
import logging
from pathlib import Path

# External
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Global Variables
EXTRACTED_DIR = Path("data/extracted")
OUTPUT_PATH = Path("data/chunks.jsonl")
CHUNK_SIZE = 1000  # max characters per chunk passed to the embedding model
CHUNK_OVERLAP = (
    100  # characters shared between adjacent chunks to avoid cutting mid-sentence
)
DEDUP_THRESHOLD = 0.85  # fraction of index-chunk tokens that must appear in a detail chunk to trigger a drop

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_BOLD_HEADER_RE = re.compile(r"^\*\*(.+?)\*\*\s*$", re.MULTILINE)


# Instantiate once — stateless and safe to reuse across pages
_char_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
)


def _split_on_bold_headers(content: str) -> list[tuple[str, str]]:
    """
    Split markdown content on lines that are exclusively a bold label, e.g.:
        **Arguments**
        **Call arguments**

    Returns a list of (section_label, text) tuples.
    The first tuple has section_label="" for content before the first header.


    last_end = 0          # cursor: how far into `content` we've consumed
    current_section = ""  # label for the *current* section being accumulated

    FOR each line that matches "**SomeLabel**" in content:

        # Everything between the previous match-end and this match-start
        # is the body text of the *current* section
        chunk_text = content[last_end : match.start()].strip()

        IF chunk_text is non-empty:
            SAVE (current_section, chunk_text)   # e.g. ("", "intro text...")

        # The matched line IS the new section label — extract just the inner text
        current_section = inner text of **...**  # e.g. "Arguments"

        # Advance cursor past the matched **label** line
        last_end = match.end()

    # After the loop: anything remaining after the last **label** line
    tail = content[last_end:].strip()
    IF tail is non-empty:
    SAVE (current_section, tail)             # e.g. ("Returns", "A tensor of...")

    """
    sections = []
    last_end = 0
    current_section = ""

    for match in _BOLD_HEADER_RE.finditer(content):
        chunk_text = content[last_end : match.start()].strip()
        if chunk_text:
            sections.append((current_section, chunk_text))
        current_section = match.group(1).strip()
        last_end = match.end()

    # Remainder after the last header
    tail = content[last_end:].strip()
    if tail:
        sections.append((current_section, tail))

    return sections


def chunk_page(doc: dict) -> list[dict]:
    """
    Split one extracted page into chunks.

    Primary split: bold-marker headers (**Section**) — one chunk per logical section.
    Fallback: RecursiveCharacterTextSplitter for any section still over CHUNK_SIZE.
    """
    url = doc["url"]
    title = doc.get("title", "")
    content = doc.get("content", "")

    sections = _split_on_bold_headers(content)

    chunks = []
    for section_label, section_text in sections:
        sub_texts = (
            _char_splitter.split_text(section_text)
            if len(section_text) > CHUNK_SIZE
            else [section_text]
        )
        for sub in sub_texts:
            if sub.strip():
                chunks.append(
                    {
                        "url": url,
                        "title": title,
                        "section": section_label,
                        "chunk_index": len(chunks),
                        "text": sub,
                    }
                )

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


def deduplicate(
    all_chunks: list[dict], threshold: float = DEDUP_THRESHOLD
) -> list[dict]:
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
    for json_path in json_files:
        try:
            doc = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Skipped {json_path.name}: {e}")
            continue
        all_chunks.extend(chunk_page(doc))

    logger.info(f"Chunks before dedup: {len(all_chunks)}")

    clean_chunks = deduplicate(all_chunks)

    logger.info(f"Chunks after dedup:  {len(clean_chunks)}")

    # Write one JSON object per line — JSONL format lets ingest.py stream records
    # without loading the full file into memory
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        for chunk in clean_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    logger.info(f"Wrote {len(clean_chunks)} chunks → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
