# GenBank Release-Notes Data Retrieval Script

A Python script (managed with [`uv`](https://docs.astral.sh/uv/)) that:

1. Reads a list of [GenBank release-note](https://www.ncbi.nlm.nih.gov/genbank/release/) URLs from a text file.
2. Fetches each page and extracts the `<pre>` block (or falls back to the full page if no `<pre>` exists).
3. Saves the raw text to disk for archival.
4. Parses the text and writes structured results to a CSV file.

## Output CSV columns

| Column | Description |
|---|---|
| `url` | Source URL |
| `release_number` | GenBank release number (e.g. `271.0`) |
| `release_date` | ISO 8601 date (e.g. `2026-04-15`) |
| `num_files` | Number of files in the release |
| `total_uncompressed_size` | Total uncompressed size in bytes |
| `num_entries` | Total number of sequence entries |
| `num_bases` | Total number of bases |
| `error` | Error message (empty if successful) |

## Requirements

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/)

## Installation

```bash
uv sync
```

## Usage

```bash
# Run via the console script (after uv sync)
uv run data-retrieval urls.txt --output-dir output/ --csv output.csv

# Or run the module directly
uv run python main.py urls.txt --output-dir output/ --csv output.csv
```

## Input format (`urls.txt`)

One URL per line; blank lines and lines starting with `#` are ignored:

```
# GenBank release notes
https://www.ncbi.nlm.nih.gov/genbank/release/current/
https://www.ncbi.nlm.nih.gov/genbank/release/270/
```

## Output layout (default)

```
raw_releases/
  <sanitized_url>_<hash>.txt

genbank_releases.csv
```
