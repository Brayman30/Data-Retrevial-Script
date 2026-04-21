import argparse
import csv
import os
import re
import urllib.request
from datetime import datetime
from urllib.error import HTTPError, URLError


def parse_date(date_str):
    """Attempt to parse common date formats into ISO 8601 (YYYY-MM-DD)."""
    date_str = date_str.strip()

    # Common NCBI/GenBank date formats
    formats = [
        "%b %d %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%Y-%m-%d",
        "%m/%d/%Y",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def clean_number(num_str):
    """Remove commas and convert string to integer."""
    if not num_str:
        return None
    try:
        return int(num_str.replace(",", "").strip())
    except ValueError:
        return None


def word_to_number(text):
    """Convert written numbers like 'forty-eight' to integers."""
    if not text:
        return None
    if text.isdigit():
        return int(text)

    numwords = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
        "thirty": 30,
        "forty": 40,
        "fifty": 50,
        "sixty": 60,
        "seventy": 70,
        "eighty": 80,
        "ninety": 90,
    }

    text = text.lower().replace("-", " ")
    words = text.split()
    total = 0
    for word in words:
        if word in numwords:
            total += numwords[word]

    return total if total > 0 else None


def extract_safe_filename(url):
    """Extract the last meaningful part of the URL for the filename."""
    parts = [p for p in url.split("/") if p]
    if parts:
        return f"release_{parts[-1]}.txt"
    return "release_unknown.txt"


def main():
    parser = argparse.ArgumentParser(description="GenBank Release Notes Archiver")
    parser.add_argument("urls_file", help="Path to text file containing URLs")
    parser.add_argument("--raw-dir", default="raw", help="Directory for raw text files")
    parser.add_argument(
        "--output", default="results.csv", help="Path of output CSV file"
    )

    args = parser.parse_args()

    # Ensure raw directory exists
    os.makedirs(args.raw_dir, exist_ok=True)

    # Read URLs
    urls = []
    try:
        with open(args.urls_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    except FileNotFoundError:
        print(f"Error: Could not find URL file '{args.urls_file}'")
        return

    # Define CSV structure
    fieldnames = [
        "url",
        "release_number",
        "release_date",
        "num_files",
        "total_uncompressed_size",
        "num_entries",
        "num_bases",
        "error",
    ]

    with open(args.output, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for url in urls:
            row = {fn: "" for fn in fieldnames}
            row["url"] = url

            filename = extract_safe_filename(url)
            cache_path = os.path.join(args.raw_dir, filename)

            pre_text = None

            # 1 & 2. Fetch or Read Cached File
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as cf:
                        pre_text = cf.read()
                except Exception:
                    row["error"] = "fetch_failed"
            else:
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "Mozilla/5.0"}
                    )
                    with urllib.request.urlopen(req, timeout=15) as response:
                        html_content = response.read().decode("utf-8", errors="replace")

                        match = re.search(
                            r"<pre\b[^>]*>(.*?)</pre>",
                            html_content,
                            re.IGNORECASE | re.DOTALL,
                        )
                        if match:
                            pre_text = match.group(1)
                            # 3. Save to disk for archival
                            with open(cache_path, "w", encoding="utf-8") as cf:
                                cf.write(pre_text)
                        else:
                            row["error"] = "no_pre_block"
                except (URLError, HTTPError, TimeoutError):
                    row["error"] = "fetch_failed"

            # 4. Parse the Text
            if pre_text is not None and not row["error"]:
                # -- Release Number --
                rel_match = re.search(r"(?i)GenBank\s+Release\s+([\d\.]+)", pre_text)
                if rel_match:
                    row["release_number"] = rel_match.group(1).strip()
                else:
                    url_match = re.search(r"release/([\d\.]+)", url)
                    row["release_number"] = url_match.group(1) if url_match else ""

                # -- Release Date --
                date_match = re.search(r"(?i)Release\s+Date:\s*(.+)", pre_text)
                if not date_match:
                    date_match = re.search(
                        r"Genetic Sequence Data Bank\s*\n\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})",
                        pre_text,
                    )
                if not date_match:
                    date_match = re.search(
                        r"Genetic Sequence Data Bank\s*\n\s+([0-9]{1,2}\s+[A-Za-z]+\s+[0-9]{4})",
                        pre_text,
                    )

                if date_match:
                    parsed_date = parse_date(date_match.group(1))
                    row["release_date"] = parsed_date if parsed_date else ""

                # -- Number of Files --
                files_match = re.search(
                    r"(?i)(?:number of sequence files|sequence files[^\d\n]*):\s*([\d,]+)",
                    pre_text,
                )
                if not files_match:
                    files_match = re.search(
                        r"([\d,]+)\s+sequence files", pre_text, re.IGNORECASE
                    )
                if not files_match:
                    files_match = re.search(
                        r"(?i)release consists of\s+([\d,]+)\s+files", pre_text
                    )
                if not files_match:
                    files_match = re.search(
                        r"(?i)release consists of\s+([a-z\-]+)\s+files", pre_text
                    )
                    if files_match:
                        val = word_to_number(files_match.group(1))
                        if val:
                            row["num_files"] = val

                if files_match and not row.get("num_files"):
                    row["num_files"] = clean_number(files_match.group(1))

                # -- Total Uncompressed Size --
                size_val = ""
                size_match = re.search(
                    r"(?i)(?:uncompressed size|size|uncompressed disk space)[^\d\n]*([\d,]+)\s*bytes",
                    pre_text,
                )
                if size_match:
                    size_val = clean_number(size_match.group(1))
                else:
                    size_match = re.search(r"([\d,]+)\s+bytes", pre_text, re.IGNORECASE)
                    if size_match:
                        size_val = clean_number(size_match.group(1))
                    else:
                        size_match = re.search(
                            r"(?i)require roughly\s+([\d,\.]+)\s*(GB|MB|KB|TB|bytes)",
                            pre_text,
                        )
                        if size_match:
                            val = float(size_match.group(1).replace(",", ""))
                            unit = size_match.group(2).upper()
                            if unit == "TB":
                                val *= 1024**4
                            elif unit == "GB":
                                val *= 1024**3
                            elif unit == "MB":
                                val *= 1024**2
                            elif unit == "KB":
                                val *= 1024
                            size_val = int(val)

                row["total_uncompressed_size"] = size_val

                # Fallback: Sum up the files from the "File Sizes" table manually (for older releases)
                if not row["total_uncompressed_size"]:
                    table_match = re.search(
                        r"File Size\s+File Name\s*\n+(.*?)(?:\n\s*\n|\n\d+\.)",
                        pre_text,
                        re.IGNORECASE | re.DOTALL,
                    )
                    if table_match:
                        table_text = table_match.group(1)
                        total_calculated_size = 0
                        for line in table_text.split("\n"):
                            parts = line.strip().split()
                            # Check if line looks like "92091214  gbacc.idx"
                            if len(parts) >= 2 and parts[0].isdigit():
                                total_calculated_size += int(parts[0])

                        if total_calculated_size > 0:
                            row["total_uncompressed_size"] = total_calculated_size

                # -- Number of Entries & Bases --
                entries_match = re.search(
                    r"(?i)number of entries:\s*([\d,]+)", pre_text
                )
                if not entries_match:
                    entries_match = re.search(
                        r"([\d,]+)\s+(?:entries|records)", pre_text, re.IGNORECASE
                    )
                if not entries_match:
                    entries_match = re.search(
                        r"(?i)^\s*([\d,]+)\s+sequences,\s+[\d,]+\s+bases,",
                        pre_text,
                        re.MULTILINE,
                    )
                row["num_entries"] = (
                    clean_number(entries_match.group(1)) if entries_match else ""
                )

                bases_match = re.search(r"(?i)number of bases:\s*([\d,]+)", pre_text)
                if not bases_match:
                    bases_match = re.search(
                        r"([\d,]+)\s+bases", pre_text, re.IGNORECASE
                    )
                if not bases_match:
                    bases_match = re.search(
                        r"(?i)^\s*[\d,]+\s+sequences,\s+([\d,]+)\s+bases,",
                        pre_text,
                        re.MULTILINE,
                    )
                row["num_bases"] = (
                    clean_number(bases_match.group(1)) if bases_match else ""
                )

                if not row["num_entries"] or not row["num_bases"]:
                    legacy_match = re.search(
                        r"(?i)^\s*([\d,]+)\s+loci,\s*([\d,]+)\s+bases,\s*from\s*([\d,]+)\s+reported",
                        pre_text,
                        re.MULTILINE,
                    )
                    if legacy_match:
                        if not row["num_entries"]:
                            row["num_entries"] = clean_number(legacy_match.group(3))
                        if not row["num_bases"]:
                            row["num_bases"] = clean_number(legacy_match.group(2))

                # -- Verify missing fields --
                missing = []
                data_fields = [
                    "release_number",
                    "release_date",
                    "num_files",
                    "total_uncompressed_size",
                    "num_entries",
                    "num_bases",
                ]
                for field in data_fields:
                    if row[field] == "" or row[field] is None:
                        missing.append(field)

                if missing:
                    row["error"] = "missing: " + ",".join(missing)

            # 5. Write row to CSV
            writer.writerow(row)


if __name__ == "__main__":
    main()
