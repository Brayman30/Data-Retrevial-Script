# GenBank Release Notes Archiver

A Python script (managed with [`uv`](https://github.com/astral-sh/uv)) that:

1. Reads a plain-text file of GenBank release-note URLs (one per line).
2. Fetches each page and extracts the raw `<pre>` block.
3. Saves the raw `<pre>` text to disk for archival purposes.
4. Parses the text to extract structured data.
5. Writes the structured results to a CSV file.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) package manager

## Setup

```bash
uv sync
```

## Usage

```bash
uv run python main.py <urls_file> [--raw-dir DIR] [--output FILE]
```

### Arguments

| Argument | Default | Description |
|---|---|---|
| `urls_file` | *(required)* | Path to a text file containing one GenBank release-note URL per line. Lines starting with `#` are treated as comments and ignored. |
| `--raw-dir DIR` | `raw` | Directory where raw `<pre>` text files are saved for archival. |
| `--output FILE` | `results.csv` | Path of the output CSV file. |

### Example

Create a `urls.txt` file:

```
# GenBank release note URLs
https://www.ncbi.nlm.nih.gov/genbank/release/260/
https://www.ncbi.nlm.nih.gov/genbank/release/261/
```

Run the script:

```bash
uv run python main.py urls.txt --raw-dir archive --output genbank_releases.csv
```

This will:
- Save the raw `<pre>` text from each page under `archive/`.
- Write `genbank_releases.csv` with the following columns:

| Column | Description |
|---|---|
| `url` | The source URL. |
| `release_date` | Release date in ISO 8601 format (`YYYY-MM-DD`). |
| `num_files` | Number of sequence files in the release. |
| `total_uncompressed_size` | Total uncompressed size (bytes). |
| `num_entries` | Number of sequence entries/records. |
| `num_bases` | Number of base pairs. |

Fields that cannot be parsed are left empty. Pages that cannot be fetched or that contain no `<pre>` block are recorded with all fields empty, so the run always completes.

## Project Structure

```
.
├── main.py          # Main script
├── pyproject.toml   # Project metadata and dependencies
├── README.md
└── urls.txt         # (user-supplied) list of URLs to process
```
