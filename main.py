"""
Fetch GenBank release-note pages, archive the raw <pre> blocks, and write
parsed statistics to a CSV file.

Usage:
    uv run main.py --urls urls.txt --output-dir out/
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "genbank-release-archiver/1.0"})
REQUEST_TIMEOUT = 30  # seconds


def fetch_page(url: str) -> str | None:
    """Return the HTML text for *url*, or None on any network/HTTP error."""
    try:
        response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        log.warning("Could not fetch %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------
def extract_pre_block(html: str) -> str | None:
    """Return the text content of the first <pre> element, or None."""
    soup = BeautifulSoup(html, "html.parser")
    pre = soup.find("pre")
    if pre is None:
        return None
    return pre.get_text()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------
def _clean_number(raw: str) -> str:
    """Strip commas/spaces from a number string returned by a regex match."""
    return raw.replace(",", "").replace(" ", "")


def _find_number(pattern: str, text: str) -> str | None:
    """Return the first captured group of *pattern* in *text*, cleaned."""
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return _clean_number(match.group(1))
    return None


def _parse_release_date(text: str) -> str | None:
    """
    Try to extract a release date from the <pre> block.

    GenBank release notes typically contain a date near the top of the block
    in one of these formats:
        "Month DD, YYYY"  e.g. "December 15, 2023"
        "MM/DD/YYYY"      e.g. "12/15/2023"

    Returns the date in ISO 8601 format (YYYY-MM-DD) or None if not found.
    """
    # "December 15, 2023" or "Dec 15, 2023"
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|"
        r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|"
        r"Nov|Dec)\s+(\d{1,2}),?\s+(\d{4})\b",
        text,
        re.IGNORECASE,
    )
    if match:
        try:
            dt = datetime.strptime(
                f"{match.group(1)} {match.group(2)} {match.group(3)}",
                "%B %d %Y" if len(match.group(1)) > 3 else "%b %d %Y",
            )
            return dt.date().isoformat()
        except ValueError:
            pass

    # "MM/DD/YYYY"
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if match:
        try:
            dt = datetime.strptime(match.group(0), "%m/%d/%Y")
            return dt.date().isoformat()
        except ValueError:
            pass

    return None


def parse_pre_block(text: str) -> dict[str, str | None]:
    """
    Extract the five requested statistics from a raw <pre> block.

    Returns a dict with keys:
        release_date, num_files, total_uncompressed_size, num_entries, num_bases
    Values are strings (numbers without commas) or None if not found.
    """
    return {
        "release_date": _parse_release_date(text),
        # "Number of files:   3,742" or "No. of files:   3,742"
        "num_files": _find_number(
            r"(?:number\s+of\s+files|no\.?\s+of\s+files)\s*[:\-]?\s*([\d,]+)",
            text,
        ),
        # "Total size: 123,456,789" or "Total uncompressed size: ..."
        "total_uncompressed_size": _find_number(
            r"total\s+(?:uncompressed\s+)?size\s*[:\-]?\s*([\d,]+)",
            text,
        ),
        # "Total entries:   262,866,516" or "Number of entries: ..."
        "num_entries": _find_number(
            r"(?:total\s+entries|number\s+of\s+entries|total\s+no\.?\s+of\s+entries)"
            r"\s*[:\-]?\s*([\d,]+)",
            text,
        ),
        # "Total bases:   5,764,572,183,498"
        "num_bases": _find_number(
            r"total\s+(?:number\s+of\s+)?bases\s*[:\-]?\s*([\d,]+)",
            text,
        ),
    }


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------
def save_raw_pre(text: str, output_dir: Path, url: str) -> Path:
    """
    Save *text* to a .txt file inside *output_dir*.

    The filename is derived from the URL slug so it is human-readable and
    unique within a single run.
    """
    # Build a safe filename from the URL (keep only word characters and hyphens)
    slug = re.sub(r"[^\w\-]", "_", url.rstrip("/").split("/")[-1] or "release")
    slug = slug[:80]  # guard against very long segments
    dest = output_dir / f"{slug}_pre.txt"
    # Avoid silently clobbering existing files when duplicate slugs arise
    counter = 1
    while dest.exists():
        dest = output_dir / f"{slug}_pre_{counter}.txt"
        counter += 1
    dest.write_text(text, encoding="utf-8")
    return dest


CSV_FIELDNAMES = [
    "url",
    "release_date",
    "num_files",
    "total_uncompressed_size",
    "num_entries",
    "num_bases",
    "raw_file",
    "status",
]


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------
def process_urls(
    urls: list[str],
    output_dir: Path,
    csv_path: Path,
) -> None:
    """Fetch, archive, parse, and record statistics for each URL in *urls*."""
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()

        for url in urls:
            url = url.strip()
            if not url or url.startswith("#"):
                continue

            log.info("Processing: %s", url)
            row: dict[str, str | None] = {
                "url": url,
                "release_date": None,
                "num_files": None,
                "total_uncompressed_size": None,
                "num_entries": None,
                "num_bases": None,
                "raw_file": None,
                "status": "ok",
            }

            html = fetch_page(url)
            if html is None:
                row["status"] = "fetch_error"
                writer.writerow(row)
                continue

            pre_text = extract_pre_block(html)
            if pre_text is None:
                log.warning("No <pre> block found at %s", url)
                row["status"] = "no_pre_block"
                writer.writerow(row)
                continue

            raw_path = save_raw_pre(pre_text, raw_dir, url)
            row["raw_file"] = str(raw_path)
            log.info("  Saved raw text → %s", raw_path)

            parsed = parse_pre_block(pre_text)
            row.update(parsed)

            missing = [k for k, v in parsed.items() if v is None]
            if missing:
                log.warning("  Could not parse: %s", ", ".join(missing))
                row["status"] = "partial"

            writer.writerow(row)

    log.info("Results written to %s", csv_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch GenBank release notes and save structured statistics to CSV.",
    )
    parser.add_argument(
        "--urls",
        metavar="FILE",
        default="urls.txt",
        help="Text file containing one GenBank release-note URL per line "
        "(default: urls.txt).",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default="out",
        help="Directory for raw <pre> files and the output CSV "
        "(default: out/).",
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        default=None,
        help="Path for the output CSV file "
        "(default: <output-dir>/results.csv).",
    )
    args = parser.parse_args()

    urls_file = Path(args.urls)
    if not urls_file.exists():
        log.error("URL list file not found: %s", urls_file)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = Path(args.csv) if args.csv else output_dir / "results.csv"

    urls = urls_file.read_text(encoding="utf-8").splitlines()
    process_urls(urls, output_dir, csv_path)


if __name__ == "__main__":
    main()

