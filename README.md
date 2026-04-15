# GenBank Release-Notes Data Retrieval Script

A Python script (managed with [`uv`](https://github.com/astral-sh/uv)) that:

1. Reads a list of [GenBank release-note](https://www.ncbi.nlm.nih.gov/genbank/release/) URLs from a text file.
2. Fetches each page and extracts the `<pre>` block exactly as it appears.
3. Saves the raw `<pre>` text to disk for archival.
4. Parses the extracted text and writes structured results to a CSV file.

## Output CSV columns

| Column | Description |
|---|---|
| `url` | Source URL |
| `release_date` | ISO 8601 date (e.g. `2024-06-15`) |
| `num_files` | Number of files in the release |
| `total_uncompressed_size` | Total uncompressed size in bytes |
| `num_entries` | Total number of sequence entries |
| `num_bases` | Total number of bases |
| `raw_file` | Path to the saved raw `<pre>` text |
| `status` | `ok`, `partial`, `fetch_error`, or `no_pre_block` |

## Requirements

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/)

## Installation

```bash
uv sync
```

## Usage

```bash
# Use default urls.txt and write output to out/
uv run main.py

# Specify a custom URL list and output directory
uv run main.py --urls my_urls.txt --output-dir results/

# Also customize the CSV path
uv run main.py --urls my_urls.txt --output-dir results/ --csv results/stats.csv
```

## Input format (`urls.txt`)

One URL per line; blank lines and lines starting with `#` are ignored:

```
# GenBank release notes
https://www.ncbi.nlm.nih.gov/genbank/release/261/
https://www.ncbi.nlm.nih.gov/genbank/release/260/
```

## Output layout

```
out/
├── raw/
│   ├── 261_pre.txt
│   └── 260_pre.txt
└── results.csv
```
