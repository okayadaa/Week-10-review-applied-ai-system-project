# Internal
import os
import json
import logging
from pathlib import Path

# External
import trafilatura

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Global Variables
INPUT_DIR = Path("data/keras_docs")
OUTPUT_DIR = Path("data/extracted")


def extract_page(html_path: Path) -> dict | None:
    html = html_path.read_text(encoding="utf-8", errors="replace")

    metadata = trafilatura.extract_metadata(html)
    title = metadata.title if (metadata and metadata.title) else ""

    content = trafilatura.extract(
        html,
        output_format="markdown",
        include_formatting=True,  # preserves headers and code blocks for downstream chunking
        include_tables=True,
    )

    # trafilatura returns None for pages that are navigation-only or redirects there is no meaningful content to embed, so we drop them early.
    if content is None:
        return None
    content = content.strip()

    # A non-None result with empty text can happen on near-empty pages (e.g. pure JavaScript entry points). Guard here so we don't write empty JSON files.
    if not content:
        return None

    # Mirror the original URL structure by stripping the local download prefix. The resulting string ("keras.io/api/layers/dense") is what gets stored as metadata in ChromaDB and surfaced as a citation in answers.
    rel = html_path.relative_to(INPUT_DIR)
    url = str(rel.with_suffix("")).replace(os.sep, "/")

    return {
        "url": url,
        "title": title,
        "content": content,
    }


def sanitize_filename(url: str) -> str:
    """
    Flatten the URL path into a single filename so we don't have to recreate the nested directory structure under data/extracted/.
    """
    return url.replace("/", "_").replace(".", "_").strip("_")


def main() -> None:
    """
    Main
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    html_files = list(INPUT_DIR.rglob("*.html"))
    logger.info(f"Found {len(html_files)} HTML files in {INPUT_DIR}")

    success, skipped = 0, 0

    for html_path in html_files:
        try:
            result = extract_page(html_path)
        except Exception as e:
            # Malformed HTML or encoding issues should not abort the whole run.
            logger.warning(f"Error processing {html_path}: {e}")
            skipped += 1
            continue

        if result is None:
            logger.warning(f"Skipped (no content extracted): {html_path}")
            skipped += 1
            continue

        filename = sanitize_filename(result["url"]) + ".json"
        out_path = OUTPUT_DIR / filename
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        success += 1

    logger.info(f"Done. Extracted: {success}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
