"""Fetch and parse GenBank release notes from a list of URLs.

Usage:
    python main.py <urls_file> [--output-dir <dir>] [--csv <file>]

Arguments:
    urls_file       Text file containing one GenBank release-note URL per line.
    --output-dir    Directory to save raw <pre> block text (default: raw_releases).
    --csv           Output CSV file path (default: genbank_releases.csv).
"""

import argparse
import csv
import hashlib
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


class PreBlockExtractor(HTMLParser):
    """Extract the text content of the first <pre> block from an HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_pre: bool = False
        self._depth: int = 0
        self._buf: list[str] = []
        self.pre_content: str | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "pre":
            if not self._in_pre:
                self._in_pre = True
                self._depth = 1
                self._buf = []
            else:
                self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "pre" and self._in_pre:
            self._depth -= 1
            if self._depth == 0:
                self._in_pre = False
                if self.pre_content is None:
                    self.pre_content = "".join(self._buf)

    def handle_data(self, data: str) -> None:
        if self._in_pre:
            self._buf.append(data)


def fetch_url(url: str, timeout: int = 30) -> str:
    """Fetch *url* and return the response body decoded as text."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "GenBankReleaseNoteParser/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = "utf-8"
        content_type = resp.headers.get("Content-Type", "")
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].strip()
        return resp.read().decode(charset, errors="replace")


def extract_pre_block(html: str) -> str | None:
    """Return the content of the first ``<pre>`` block, or *None* if absent."""
    parser = PreBlockExtractor()
    parser.feed(html)
    return parser.pre_content


def save_raw_text(text: str, url: str, output_dir: Path) -> Path:
    """Write *text* to a file inside *output_dir* named after *url*.

    A short SHA-256 hash of the full URL is appended to avoid filename
    collisions between URLs that reduce to the same safe name.
    """
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
    safe_name = (
        re.sub(r"[^\w.-]", "_", url.split("//", 1)[-1]).strip("_") + f"_{url_hash}.txt"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / safe_name
    dest.write_text(text, encoding="utf-8")
    return dest


def _strip_commas(value: str) -> str:
    return value.replace(",", "")


def parse_release_number(text: str) -> str | None:
    """Return the GenBank release number (e.g. ``'258.0'``) from *text*."""
    match = re.search(r"GenBank\s+Release\s+(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    return match.group(1) if match else None


def parse_release_date(text: str) -> str | None:
    """Return the release date in ISO 8601 format (``YYYY-MM-DD``), or *None*."""
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    match = re.search(
        r"(January|February|March|April|May|June|July|August"
        r"|September|October|November|December)"
        r"\s+(\d{1,2}),?\s+(\d{4})",
        text,
        re.IGNORECASE,
    )
    if match:
        month = months.get(match.group(1).lower())
        if month:
            try:
                return datetime(
                    int(match.group(3)), month, int(match.group(2))
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return None


def parse_num_files(text: str) -> str | None:
    """Return the total number of flat files as a plain integer string."""
    for pattern in (
        r"(?:total\s+)?(?:number\s+of\s+)?(?:flat\s+)?files[:\s]+([0-9,]+)",
        r"([0-9,]+)\s+files",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _strip_commas(match.group(1))
    return None


def parse_total_uncompressed_size(text: str) -> str | None:
    """Return the total uncompressed size in bytes as a plain integer string."""
    for pattern in (
        r"total\s+\(uncompressed\)[:\s]+([0-9,]+)",
        r"uncompressed[:\s]+([0-9,]+)",
        r"([0-9,]+)\s+bytes",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _strip_commas(match.group(1))
    return None


def parse_num_entries(text: str) -> str | None:
    """Return the total number of database entries as a plain integer string."""
    for pattern in (
        r"(?:total\s+)?(?:number\s+of\s+)?entries[:\s]+([0-9,]+)",
        r"total\s+sequences[:\s]+([0-9,]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _strip_commas(match.group(1))
    return None


def parse_num_bases(text: str) -> str | None:
    """Return the total number of bases as a plain integer string."""
    for pattern in (
        r"(?:total\s+)?(?:number\s+of\s+)?bases[:\s]+([0-9,]+)",
        r"(?:total\s+)?base\s+pairs[:\s]+([0-9,]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return _strip_commas(match.group(1))
    return None


def parse_release_note(text: str) -> dict:
    """Return a dict of parsed fields from a release-note text block."""
    return {
        "release_number": parse_release_number(text),
        "release_date": parse_release_date(text),
        "num_files": parse_num_files(text),
        "total_uncompressed_size": parse_total_uncompressed_size(text),
        "num_entries": parse_num_entries(text),
        "num_bases": parse_num_bases(text),
    }


CSV_FIELDS = [
    "url",
    "release_number",
    "release_date",
    "num_files",
    "total_uncompressed_size",
    "num_entries",
    "num_bases",
    "error",
]


def process_url(url: str, output_dir: Path) -> dict:
    """Fetch, archive raw text, and parse one release-note URL.

    Returns a record dict suitable for writing to the CSV output.  Any
    network or parse errors are captured in the ``error`` field so that
    the rest of the batch can continue.
    """
    record: dict = {field: "" for field in CSV_FIELDS}
    record["url"] = url
    try:
        html = fetch_url(url)
        pre_text = extract_pre_block(html)
        # Fall back to the full page when no <pre> block is present (e.g.
        # plain-text URLs served without an HTML wrapper).
        if pre_text is None:
            pre_text = html
        save_raw_text(pre_text, url, output_dir)
        parsed = parse_release_note(pre_text)
        record.update({k: (v or "") for k, v in parsed.items()})
    except urllib.error.URLError as exc:
        record["error"] = f"URLError: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("urls_file", help="Text file with one URL per line")
    parser.add_argument(
        "--output-dir",
        default="raw_releases",
        help="Directory to save raw <pre> text (default: raw_releases)",
    )
    parser.add_argument(
        "--csv",
        default="genbank_releases.csv",
        help="Output CSV file (default: genbank_releases.csv)",
    )
    args = parser.parse_args(argv)

    urls_file = Path(args.urls_file)
    if not urls_file.is_file():
        print(f"Error: URLs file not found: {urls_file}", file=sys.stderr)
        return 1

    urls = [
        line.strip()
        for line in urls_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not urls:
        print("No URLs found in input file.", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    records: list[dict] = []
    for url in urls:
        print(f"Processing: {url}", file=sys.stderr)
        record = process_url(url, output_dir)
        records.append(record)
        if record["error"]:
            print(f"  WARNING: {record['error']}", file=sys.stderr)

    csv_path = Path(args.csv)
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)

    print(f"Wrote {len(records)} records to {csv_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
