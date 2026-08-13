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
python -m scraper reparse --all

# 4. Backfill comments for posts that still have none
python -m scraper enrich --limit 50

# 5. Stats + CRS resolve report (ProfstoPick acceptance sample)
python -m scraper stats
python -m scraper resolve-report --sample 100 --strategy recent \
  --crs-db ~/Antigravity/crs/data/crs.db \
  -o output/resolve_report.json

# 6. Export
python -m scraper export --format professors --crs \
  --crs-db ~/Antigravity/crs/data/crs.db \
  -o output/professors_crs.json
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
python -m scraper scrape-all --scale 0.5 --export output/professors_crs.json --crs
```

### Repair / backfill

```bash
python -m scraper reparse          # unparsed rows only
python -m scraper reparse --all    # every title
python -m scraper enrich --limit 100
```

### CRS matching & resolve report

```bash
# Unique professors vs CRS roster (prints unmatched — never silent)
python -m scraper match --crs-db ~/Antigravity/crs/data/crs.db

# Acceptance sample: 100 mentions → resolve rate + unresolved JSON
python -m scraper resolve-report --sample 100 --strategy recent \
  --crs-db ~/Antigravity/crs/data/crs.db \
  -o output/resolve_report.json
# exit 3 if resolve rate < 80%
```

### Exporting

```bash
python -m scraper export --format full -o output/full.json
python -m scraper export --format professors --crs -o output/professors_crs.json
python -m scraper export --format comments --crs -o output/comments_rupp_shaped.json
python -m scraper stats
```

### Matrix Pipeline (GitHub Actions)

`.github/workflows/matrix-scrape.yml` runs every 10 minutes: 6 parallel Arctic Shift year-windows covering the full r/RateUPProfs archive, then merge → reparse → enrich comments → CRS export. Shards **fail if they produce 0 posts**. Sequential `scrape-all` is `workflow_dispatch` only (`.github/workflows/scrape.yml`).

## ProfstoPick ingest

ProfstoPick ROADMAP acceptance for this repo:

> a sample of 100 scraped mentions resolves ≥80% to a roster professor, and every unresolved one is reported rather than silently dropped

Run `resolve-report` for that gate. Latest local numbers are in [CHANGELOG.md](./CHANGELOG.md).

### What exists today in ProfstoPick

| Path | What it reads | Compatible with this scraper? |
| --- | --- | --- |
| `script/import/rupp.ts` | Alec **numeric review** dump (`teacherId`, 0–5 criteria, `reviewId`, …) with **`MINIMUM_REVIEW = 7000`** | **No** — Reddit corpus is smaller and has no star ratings |
| Reddit / RateUPProfs importer | *(not built yet)* | — |

There is **no** `reddit`/`rateup` source key in ProfstoPick yet. Do not point `npm run import -- --source rupp=` at scraper output.

### Exports this repo emits

1. **`professors` / `professors_crs.json`** — discovery index: name, campus, courses, discussion permalinks, optional `crs_verified`. Good for debugging and CRS join QA; **not** a comment import.
2. **`comments` / `comments_rupp_shaped.json`** — one row per post body / comment, shaped like `ReviewRow` fields (`professor`, `teacherId`, `reviewId`, `subject`, `comment`, `date`, null ratings) plus `resolve_status` and `source_url`. Sidecar `*.unresolved.json` lists names that did not resolve.

### Adapter still required in ProfstoPick

A thin new importer (suggested name: `script/import/rateup.ts` or extend reviews behind a new `SourceKey`) should:

1. Read `comments_rupp_shaped.json` (array).
2. **Skip or quarantine** rows with `resolve_status != "resolved"` (unresolved must stay reported, never auto-attached to a random roster hit).
3. Map `teacherId` → CRS/`professor.external_id` or join via the same `professorSlug` path `rupp.ts` already uses.
4. Insert `comment` rows with `source` = new enum value (not `rupp`), `rating`/`criterion` null, sentiment from text heuristics or `neutral`.
5. **Do not** reuse the 7000-row alec floor; use a Reddit-appropriate minimum (or none, with an explicit allow-empty flag).

Until that lands, this repo’s deliverable for the ROADMAP row is the **resolve-report PASS** + honest unresolved lists, not a live import.

## How It Works

### Two-Phase Scrape

1. **Phase 1 — Listing fetch**: Prefer Reddit JSON / PRAW; on 403/429 fall through to Arctic Shift, then RSS.
2. **Phase 2 — Comment enrichment**: Prefer `/comments/{id}.json` / PRAW; fall through to Arctic Shift comments (~1.2s/request).

### Title Parsing

Expected format:

```text
[CAMPUS] Course Code - LASTNAME, FIRSTNAME
```

Also handles swapped order, informal no-comma names, missing-dash titles, multi-word courses, compound / hyphenated names, parentheticals, en/em dashes, section-code noise (`WFX`, `THY`, …), and honorifics. Conversational “looking for classmates” titles are rejected. Non-conforming titles return `None`.

### Architecture

```text
scraper/
├── config.py          # Constants, campus codes, progressive passes
├── parser.py          # Regex title parser with fallback patterns
├── analyzer.py        # Rule-based student signal & keyword extractor
├── crs_matcher.py     # Cross-referencer against official UP CRS db
├── resolve_report.py  # Mention sample → resolve rate + unresolved list
├── models.py          # Dataclasses: Professor, Post, Comment
├── database.py        # SQLite schema, upsert logic, queries
├── reddit_client.py   # PRAW → JSON → Arctic Shift → RSS
├── exporter.py        # SQLite → JSON with CRS & signal enrichment
├── cli.py             # scrape / scrape-all / reparse / enrich / export / stats / match / resolve-report
└── __main__.py        # python -m scraper entrypoint
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
