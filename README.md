# ruppscraper

**r/RateUPProfs → Structured Data Pipeline**

A Python CLI tool that scrapes [r/RateUPProfs](https://reddit.com/r/RateUPProfs), parses structured post titles into campus/course/professor fields, collects comments, stores everything in SQLite, and exports to JSON.

**OAuth optional.** Fetch stack: **PRAW (if credentials)** → Reddit public `.json` → **Arctic Shift archive** → RSS. Public `.json` is often 403 without OAuth; Arctic Shift is the default working path today.

## Quick Start

```bash
# 1. Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Progressive scrape (sorts + subject queries, resume-safe)
python -m scraper scrape-all --scale 0.25

# 3. Apply parser upgrades to titles already in the DB
python -m scraper reparse

# 4. Backfill comments for posts that still have none
python -m scraper enrich --limit 50

# 5. Stats + export
python -m scraper stats
python -m scraper export --format professors -o output/professors.json
```

Optional OAuth (after Reddit approves a script app): copy `.env.example` → `.env`.

## Usage

### Scraping

```bash
# Single pass
python -m scraper scrape --sort new --limit 50 --resume

# Subject search (Arctic Shift)
python -m scraper scrape --query Math --limit 100 --comments 50 --resume

# Full progressive coverage (recommended while waiting on OAuth)
python -m scraper scrape-all
python -m scraper scrape-all --scale 0.5 --export output/professors.json --crs
```

### Repair / backfill

```bash
python -m scraper reparse          # unparsed rows only
python -m scraper reparse --all    # every title
python -m scraper enrich --limit 100
```

### Exporting

```bash
python -m scraper export --format full -o output/full.json
python -m scraper export --format professors --crs -o output/professors_crs.json
python -m scraper stats
```

### Matrix Pipeline (GitHub Actions)

`.github/workflows/matrix-scrape.yml` runs 10 parallel shards (`sort:*` + `query:*`), merges into SQLite, CRS-matches, and commits `output/professors_crs.json`. Shards **fail if they produce 0 posts**.

## How It Works

### Two-Phase Scrape

1. **Phase 1 — Listing fetch**: Prefer Reddit JSON / PRAW; on 403/429 fall through to Arctic Shift, then RSS.
2. **Phase 2 — Comment enrichment**: Prefer `/comments/{id}.json` / PRAW; fall through to Arctic Shift comments (~1.2s/request).

### Title Parsing

Expected format:

```text
[CAMPUS] Course Code - LASTNAME, FIRSTNAME
```

Also handles swapped order, informal no-comma names, missing-dash titles, multi-word courses, compound / hyphenated names, and parentheticals. Non-conforming titles return `None`.

### Architecture

```text
scraper/
├── config.py         # Constants, campus codes, progressive passes
├── parser.py         # Regex title parser with fallback patterns
├── analyzer.py       # Rule-based student signal & keyword extractor
├── crs_matcher.py    # Cross-referencer against official UP CRS db
├── models.py         # Dataclasses: Professor, Post, Comment
├── database.py       # SQLite schema, upsert logic, queries
├── reddit_client.py  # PRAW → JSON → Arctic Shift → RSS
├── exporter.py       # SQLite → JSON with CRS & signal enrichment
├── cli.py            # scrape / scrape-all / reparse / enrich / export / stats / match
└── __main__.py       # python -m scraper entrypoint
```

### Database

SQLite with WAL mode. Tables: `professors`, `posts`, `comments`. Writes use upserts so re-runs are idempotent.

## Running Tests

```bash
python -m pytest tests/ -v
```

## Compliance & Policy Safeguards

Aligned with [Reddit's Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy) and Philippine NPC Advisory No. 2026-01:

1. **Zero Redditor De-anonymization** — no cross-matching Reddit users to real-world identities.
2. **Discovery & Index Layer Only** — professor JSON points back to Reddit permalinks.
3. **No AI Model Training** — rule-based signals only (`analyzer.py`); no scraped content for ML training.
4. **Rate Limit Respect** — courtesy delay (~1.2s) between enrichment requests.
