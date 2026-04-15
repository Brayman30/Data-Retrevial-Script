"""
GenBank Release Notes Archiver

Reads a text file of GenBank release-note URLs (one per line), fetches each
page, extracts the raw <pre> block, saves it to disk for archival, then parses
the text to extract structured data and writes the results to a CSV file.

Usage:
    uv run python main.py urls.txt [--raw-dir raw] [--output results.csv]

CSV columns:
    url, release_date (ISO 8601), num_files,
    total_uncompressed_size, num_entries, num_bases
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

def fetch_page(url: str, timeout: int = 30) -> str | None:
    """Fetch *url* and return the response body as text, or None on failure."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"  WARNING: Failed to fetch {url!r}: {exc}", file=sys.stderr)
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
# Persistence
# ---------------------------------------------------------------------------

def save_raw_text(text: str, path: Path) -> None:
    """Write *text* to *path*, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _strip_commas(value: str) -> str:
    """Remove comma thousands-separators from a numeric string."""
    return value.replace(",", "").strip()


def _parse_date(text: str) -> str:
    """
    Search *text* for a recognisable release date and return it in ISO 8601
    (YYYY-MM-DD) format.  Returns an empty string when no date is found.
    """
    # Patterns ordered from most to least specific
    candidate_patterns = [
        # "Release 260.0, February 15, 2023"
        r"[Rr]elease\s+[\d.]+,\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        # "release date February 15, 2023"
        r"release\s+date\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        # "Date: February 15, 2023"
        r"[Dd]ate\s*:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        # ISO date already present
        r"(\d{4}-\d{2}-\d{2})",
    ]
    date_formats = ["%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"]

    for pattern in candidate_patterns:
        match = re.search(pattern, text)
        if match:
            date_str = match.group(1).strip()
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_str, fmt).date().isoformat()
                except ValueError:
                    continue
    return ""


def _parse_field(text: str, patterns: list[str]) -> str:
    """
    Try each regex *pattern* in order; return the first captured numeric group
    with commas stripped, or an empty string when nothing matches.
    """
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _strip_commas(match.group(1))
    return ""


def parse_pre_text(text: str) -> dict[str, str]:
    """
    Extract the five required fields from a GenBank release-notes <pre> block.

    Returns a dict with keys:
        release_date, num_files, total_uncompressed_size, num_entries, num_bases
    Values are empty strings for fields that could not be found.
    """
    release_date = _parse_date(text)

    num_files = _parse_field(text, [
        r"[Nn]umber\s+of\s+files\s*[:\-]\s*([\d,]+)",
        r"\bfiles\s*[:\-]\s*([\d,]+)",
    ])

    total_uncompressed_size = _parse_field(text, [
        r"[Tt]otal\s+[Uu]ncompressed\s*[:\-]\s*([\d,]+)",
        r"[Tt]otal\s+[Uu]ncompressed\s+[Ss]ize\s*[:\-]\s*([\d,]+)",
        r"[Uu]ncompressed\s*[:\-]\s*([\d,]+)",
    ])

    num_entries = _parse_field(text, [
        r"[Nn]umber\s+of\s+(?:loci|entries|records|sequences)\s*[:\-]\s*([\d,]+)",
        r"\b(?:loci|entries|records|sequences)\s*[:\-]\s*([\d,]+)",
    ])

    num_bases = _parse_field(text, [
        r"[Nn]umber\s+of\s+bases\s*[:\-]\s*([\d,]+)",
        r"\bbases\s*[:\-]\s*([\d,]+)",
    ])

    return {
        "release_date": release_date,
        "num_files": num_files,
        "total_uncompressed_size": total_uncompressed_size,
        "num_entries": num_entries,
        "num_bases": num_bases,
    }


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _slug_from_url(url: str) -> str:
    """Derive a safe filename stem from a URL."""
    # Drop trailing slash, take the last two non-empty path segments
    parts = [p for p in url.rstrip("/").split("/") if p]
    slug = "_".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "unknown")
    # Replace any remaining characters that are unsafe in filenames
    return re.sub(r"[^\w\-.]", "_", slug)


# ---------------------------------------------------------------------------
# Empty row factory
# ---------------------------------------------------------------------------

_EMPTY_ROW_FIELDS = ("release_date", "num_files", "total_uncompressed_size", "num_entries", "num_bases")

def _empty_row(url: str) -> dict[str, str]:
    return {"url": url, **{k: "" for k in _EMPTY_ROW_FIELDS}}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

CSV_FIELDNAMES = ["url", "release_date", "num_files", "total_uncompressed_size", "num_entries", "num_bases"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch GenBank release-note pages, archive the raw <pre> block, "
            "and write structured data to a CSV file."
        )
    )
    parser.add_argument(
        "urls_file",
        help="Path to a text file containing one GenBank release-note URL per line.",
    )
    parser.add_argument(
        "--raw-dir",
        default="raw",
        metavar="DIR",
        help="Directory where raw <pre> text files are saved (default: raw).",
    )
    parser.add_argument(
        "--output",
        default="results.csv",
        metavar="FILE",
        help="Output CSV file path (default: results.csv).",
    )
    args = parser.parse_args()

    urls_path = Path(args.urls_file)
    if not urls_path.exists():
        print(f"ERROR: URL file not found: {urls_path}", file=sys.stderr)
        sys.exit(1)

    urls = [
        stripped
        for line in urls_path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]

    if not urls:
        print("ERROR: No URLs found in the input file.", file=sys.stderr)
        sys.exit(1)

    raw_dir = Path(args.raw_dir)
    output_path = Path(args.output)
    rows: list[dict[str, str]] = []

    for url in urls:
        print(f"Fetching: {url}")

        html = fetch_page(url)
        if html is None:
            print(f"  Skipping — fetch failed.", file=sys.stderr)
            rows.append(_empty_row(url))
            continue

        pre_text = extract_pre_block(html)
        if pre_text is None:
            print(f"  WARNING: No <pre> block found at {url!r}", file=sys.stderr)
            rows.append(_empty_row(url))
            continue

        # Archive the raw text
        raw_path = raw_dir / f"{_slug_from_url(url)}.txt"
        save_raw_text(pre_text, raw_path)
        print(f"  Archived raw text → {raw_path}")

        # Parse structured fields
        parsed = parse_pre_text(pre_text)
        rows.append({"url": url, **parsed})

        missing = [k for k, v in parsed.items() if not v]
        if missing:
            print(f"  WARNING: Could not parse: {', '.join(missing)}")

    # Write CSV output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. {len(rows)} record(s) written to {output_path}")


if __name__ == "__main__":
    main()

