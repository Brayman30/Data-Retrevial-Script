"""
GenBank Release Notes Archiver

Reads a text file of GenBank release-note URLs (one per line), fetches each
page (or reuses a previously-downloaded raw file), extracts the raw <pre>
block, saves it to disk for archival, then parses the text to extract
structured data and writes the results to a CSV file.

Usage:
    uv run python main.py urls.txt [--raw-dir raw] [--output results.csv]

CSV columns:
    url, release_number, release_date (ISO 8601), num_files,
    total_uncompressed_size, num_entries, num_bases, error
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
    candidate_patterns = [
        # "Release 260.0, February 15, 2023", "Release 260.0 February 15, 2023",
        # or "Release 261.0 / April 15, 2024"
        r"[Rr]elease\s+[\d.]+[,\s/]+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        # "Released February 15, 2023"
        r"[Rr]eleased\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        # "release date February 15, 2023" / "Date: February 15, 2023"
        r"(?:release\s+date|[Dd]ate\s*:)\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        # Standalone date line with comma: "February 15, 2023"
        r"^\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})\s*$",
        # Standalone date line without comma: "December 15 2023" (GBREL.TXT FTP format)
        r"^\s+([A-Za-z]+\s+\d{1,2}\s+\d{4})\s*$",
        # ISO date already present
        r"(\d{4}-\d{2}-\d{2})",
    ]
    date_formats = ["%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%Y-%m-%d"]

    for pattern in candidate_patterns:
        flags = re.MULTILINE if pattern.startswith("^") else 0
        match = re.search(pattern, text, flags)
        if match:
            date_str = match.group(1).strip()
            for fmt in date_formats:
                try:
                    return datetime.strptime(date_str, fmt).date().isoformat()
                except ValueError:
                    continue
    return ""


def _parse_release_number(text: str, url: str = "") -> str:
    """
    Extract the GenBank release number string from *text* (e.g. ``"261.0"``).

    Tries the following in order:
    1. ``"GenBank Release 261.0"`` – explicit version in the pre-block text.
    2. ``"Release 261.0"`` – release header without the "GenBank" prefix.
    3. URL path segment ``/release/261/`` → ``"261"`` as a last resort.

    Returns an empty string when nothing matches.
    """
    for pattern in (
        r"[Gg]en[Bb]ank\s+[Rr]elease\s+([\d]+(?:\.\d+)?)",
        r"[Rr]elease\s+([\d]+(?:\.\d+)?)",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    # Fallback: derive from the URL path (e.g. /genbank/release/261/)
    if url:
        url_match = re.search(r"/release/(\d+)", url)
        if url_match:
            return url_match.group(1)

    return ""


def _parse_table_format(text: str, result: dict) -> None:
    """
    Parse NCBI's tabular release-note format, filling *result* in-place.

    NCBI release notes use tables whose "Total" row summarises the release:

      Table 1 – Division Statistics (columns: Files  Entries  Bases)
        Total  3,594  241,595,478  899,777,346,718

      Table 2 – File Size Statistics (column: Uncompressed (bytes))
        Total  2,345,678,901

    Uses a state-machine so that each table's header establishes the column
    context that is active for its own Total row, preventing cross-table
    contamination from a look-back window.
    """
    lines = text.splitlines()
    current_context: str | None = None  # "division_stats" | "size" | None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        # A new "Table N." header resets the current table context
        if re.match(r"Table\s+\d+", stripped):
            current_context = None

        # Detect column-header lines and set context
        has_files = bool(re.search(r"\bfiles?\b", lower))
        has_entries = bool(re.search(r"\b(entries|loci|sequences|records)\b", lower))
        has_bases = bool(re.search(r"\bbases?\b", lower))
        has_uncompressed = bool(re.search(r"\buncompressed\b", lower))

        if has_files and has_entries and has_bases:
            current_context = "division_stats"
        elif has_uncompressed and not (has_files and has_entries and has_bases):
            current_context = "size"

        # Parse a "Total  N  [M  [O]]" summary row according to active context
        if not re.match(r"Total\s+[\d,]", stripped, re.IGNORECASE):
            continue

        nums = [_strip_commas(n) for n in re.findall(r"[\d,]+", stripped)]

        if current_context == "division_stats" and len(nums) >= 3:
            if not result["num_files"]:
                result["num_files"] = nums[0]
            if not result["num_entries"]:
                result["num_entries"] = nums[1]
            if not result["num_bases"]:
                result["num_bases"] = nums[2]
        elif current_context == "size" and len(nums) >= 1:
            if not result["total_uncompressed_size"]:
                result["total_uncompressed_size"] = nums[0]


def _parse_field(text: str, patterns: list[str]) -> str:
    """
    Try each regex *pattern* in order; return the first captured numeric group
    with commas stripped, or an empty string when nothing matches.

    Patterns are matched with both ``re.IGNORECASE`` and ``re.MULTILINE`` so
    that ``^``-anchored patterns (used to restrict matches to line starts) work
    correctly across multi-line release-note text.
    """
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _strip_commas(match.group(1))
    return ""


def _parse_gbrel_format(text: str, result: dict) -> None:
    """
    Parse the GBREL.TXT FTP distribution format used by GenBank releases
    259+, filling *result* in-place.

    This format differs from the HTML summary pages: it contains the full
    release document with prose descriptions, individual per-file size lines,
    and per-division tables without a "Total" summary row.

    Key patterns extracted:

    - num_entries / num_bases: header sentence
        "249,060,436 sequences,  2,570,711,588,044 bases, for traditional GenBank records"
    - num_files: prose sentence
        "This GenBank flat file release consists of 8832 files."
    - total_uncompressed_size: summed from the "File Size  File Name" table
        whose rows have the form ``<bytes>     <filename>``.
    """
    # num_entries and num_bases from the header summary line
    m = re.search(
        r"([\d,]+)\s+sequences,\s+([\d,]+)\s+bases,\s+for traditional GenBank records",
        text,
    )
    if m:
        if not result["num_entries"]:
            result["num_entries"] = _strip_commas(m.group(1))
        if not result["num_bases"]:
            result["num_bases"] = _strip_commas(m.group(2))

    # num_files from the prose sentence
    m = re.search(
        r"flat file release consists of\s+([\d,]+)\s+files",
        text,
        re.IGNORECASE,
    )
    if m and not result["num_files"]:
        result["num_files"] = _strip_commas(m.group(1))

    # total_uncompressed_size: sum every byte-count entry in the File Sizes table.
    # The table starts after "File Size      File Name" and ends before section 2.2.6.
    if not result["total_uncompressed_size"]:
        m = re.search(
            r"File Size\s+File Name\s*\n(.*?)(?=\n\d+\.\d+\.)",
            text,
            re.DOTALL,
        )
        if m:
            sizes = [int(x) for x in re.findall(r"^\s*(\d+)\s+\w", m.group(1), re.MULTILINE)]
            if sizes:
                result["total_uncompressed_size"] = str(sum(sizes))


def parse_pre_text(text: str, url: str = "") -> dict[str, str]:
    """
    Extract the six required fields from a GenBank release-notes <pre> block.

    Handles two main formats used across NCBI releases:

    1. Modern tabular format – "Total" rows in division-stats and file-size tables.
    2. Key-value / sentence format – "Number of loci: N", "Files: N", etc.

    Returns a dict with keys:
        release_number, release_date, num_files, total_uncompressed_size,
        num_entries, num_bases
    All values are strings; empty string means the field was not found.
    """
    result: dict[str, str] = {
        "release_number": "",
        "release_date": "",
        "num_files": "",
        "total_uncompressed_size": "",
        "num_entries": "",
        "num_bases": "",
    }

    result["release_number"] = _parse_release_number(text, url)
    result["release_date"] = _parse_date(text)

    # Pass 1 – tabular format (most modern releases)
    _parse_table_format(text, result)

    # Pass 2 – key-value / sentence fallbacks for any still-missing fields

    if not result["num_files"]:
        result["num_files"] = _parse_field(text, [
            # "Number of (flat) files: N" or dotted variant
            r"[Nn]umber\s+of\s+(?:flat\s+)?files\s*[.:\-\s]+\s*([\d,]+)",
            # "Files: N" key-value
            r"^\s*[Ff]iles?\s*[:\-]\s*([\d,]+)",
            # "contains N (flat|sequence|...) files" in a sentence
            r"contains\s+([\d,]+)\s+(?:\w+\s+)*files?",
        ])

    if not result["total_uncompressed_size"]:
        result["total_uncompressed_size"] = _parse_field(text, [
            # "Total uncompressed (file) size: N bytes"
            r"[Tt]otal\s+uncompressed\s+(?:file\s+)?size\s*[.:\-\s]+\s*([\d,]+)",
            # "Total uncompressed: N" / "Total Uncompressed Size: N"
            r"[Tt]otal\s+[Uu]ncompressed\s+(?:[Ss]ize\s*)?[.:\-\s]+\s*([\d,]+)",
            # "Uncompressed (bytes): N"  or  "Uncompressed: N"
            r"[Uu]ncompressed\s*(?:\(bytes?\))?\s*[:\-]\s*([\d,]+)",
            # "Total Size (bytes): N"
            r"[Tt]otal\s+[Ss]ize\s*(?:\(bytes?\))?\s*[:\-]\s*([\d,]+)",
        ])

    if not result["num_entries"]:
        result["num_entries"] = _parse_field(text, [
            # "Number of loci ......  N"  or  "Number of entries: N"
            r"[Nn]umber\s+of\s+(?:sequence\s+)?(?:loci|entries|records|sequences)\s*[.:\-\s]+\s*([\d,]+)",
            # "Loci: N" / "Entries: N"
            r"^\s*(?:[Ll]oci|[Ee]ntries|[Rr]ecords|[Ss]equences)\s*[:\-]\s*([\d,]+)",
            # Dotted form without leading "Number of"
            r"(?:[Ll]oci|[Ee]ntries|[Rr]ecords|[Ss]equences)\s*\.{2,}\s*([\d,]+)",
        ])

    if not result["num_bases"]:
        result["num_bases"] = _parse_field(text, [
            # "Number of bases ......  N"
            r"[Nn]umber\s+of\s+bases\s*[.:\-\s]+\s*([\d,]+)",
            # "Bases: N"
            r"^\s*[Bb]ases?\s*[:\-]\s*([\d,]+)",
            # Dotted form
            r"[Bb]ases?\s*\.{2,}\s*([\d,]+)",
        ])

    return result


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _slug_from_url(url: str) -> str:
    """Derive a safe filename stem from a URL."""
    parts = [p for p in url.rstrip("/").split("/") if p]
    slug = "_".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else "unknown")
    return re.sub(r"[^\w\-.]", "_", slug)


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------

_DATA_FIELDS = (
    "release_number",
    "release_date",
    "num_files",
    "total_uncompressed_size",
    "num_entries",
    "num_bases",
)

CSV_FIELDNAMES = ["url", *_DATA_FIELDS, "error"]


def _make_row(url: str, parsed: dict[str, str] | None = None, error: str = "") -> dict[str, str]:
    """Build a CSV row dict, merging parsed data with the error field."""
    data = parsed or {k: "" for k in _DATA_FIELDS}
    return {"url": url, **{k: data.get(k, "") for k in _DATA_FIELDS}, "error": error}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch GenBank release-note pages, archive the raw <pre> block, "
            "and write structured data to a CSV file.  If a raw file for a URL "
            "already exists in --raw-dir, it is reused and the page is not "
            "re-fetched."
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
        help="Directory where raw <pre> text files are saved/read (default: raw).",
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
        raw_path = raw_dir / f"{_slug_from_url(url)}.txt"

        # ---- Obtain the raw <pre> text (cache-first) ----------------------
        if raw_path.exists():
            print(f"Using cached: {raw_path}  ({url})")
            pre_text = raw_path.read_text(encoding="utf-8")
        else:
            print(f"Fetching: {url}")
            html = fetch_page(url)
            if html is None:
                rows.append(_make_row(url, error="fetch_failed"))
                continue

            pre_text = extract_pre_block(html)
            if pre_text is None:
                print(f"  WARNING: No <pre> block found at {url!r}", file=sys.stderr)
                rows.append(_make_row(url, error="no_pre_block"))
                continue

            save_raw_text(pre_text, raw_path)
            print(f"  Archived raw text → {raw_path}")

        # ---- Parse structured fields --------------------------------------
        parsed = parse_pre_text(pre_text, url)
        missing = [k for k in _DATA_FIELDS if not parsed.get(k)]
        error_str = f"missing: {','.join(missing)}" if missing else ""
        if missing:
            print(f"  WARNING: Could not parse: {', '.join(missing)}", file=sys.stderr)

        rows.append(_make_row(url, parsed=parsed, error=error_str))

    # ---- Write CSV output -------------------------------------------------
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. {len(rows)} record(s) written to {output_path}")


if __name__ == "__main__":
    main()

