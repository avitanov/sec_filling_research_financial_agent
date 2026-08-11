# Auditable SEC Filing Research

## SEC data collection

The downloader requires only two CLI inputs:

- `--companies`: one or more SEC-listed ticker symbols;
- `--year`: the requested fiscal/report year.

It resolves each ticker through SEC's company ticker mapping, finds the matching `10-K` in the SEC submissions data, and downloads the primary HTML filing, submissions JSON, and Company Facts JSON. The `--year` value matches the filing's `reportDate` year, not its filing date.

The user does not provide a CIK, company name, accession number, filing date, or primary document filename. SEC supplies those values automatically, and the discovered metadata is saved in `data/manifest.csv`.

Set the required SEC user-agent in the shell:

```bash
export SEC_USER_AGENT="Your Name your-email@example.com"
```

Download the 2025 statements for the three project companies:

```bash
uv run python scripts/download_data.py \
  --companies GOOGL KO AMZN \
  --year 2025
```

Other supported selections use the same interface:

```bash
uv run python scripts/download_data.py --companies GOOGL --year 2025
uv run python scripts/download_data.py --companies KO AMZN --year 2025
```

The ticker mapping is cached at `data/company_tickers.json` and reused on later runs. Each company has one folder under `data/raw/` containing exactly three downloaded source files.
