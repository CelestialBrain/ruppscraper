# ruppscraper

**r/RateUPProfs → Structured Data Pipeline**

A Python CLI tool that scrapes the [r/RateUPProfs](https://reddit.com/r/RateUPProfs) subreddit, parses structured post titles into campus/course/professor fields, collects comments, stores everything in SQLite, and exports to JSON.

**No Reddit API keys or OAuth needed.** Uses Reddit's public `.json` endpoints with RSS fallback — same approach as [pingfree](https://github.com/CelestialBrain/pingfree).

## Quick Start

```bash
# 1. Setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Scrape (no credentials needed!)
python -m scraper scrape --sort new --limit 50

# 3. Check stats
python -m scraper stats

# 4. Export
python -m scraper export --format professors -o output/professors.json
```

## Usage

### Scraping

```bash
# Scrape the 50 newest posts
python -m scraper scrape --sort new --limit 50

# Scrape top posts of all time
python -m scraper scrape --sort top --limit 200

# Resume a previous scrape (skips already-scraped posts)
python -m scraper scrape --sort new --resume

# Control comment enrichment (default: all scraped posts)
python -m scraper scrape --limit 100 --comments 20
```

### Exporting

```bash
# Full export (every post with comments)
python -m scraper export --format full -o output/full.json

# Professor-grouped export (for ProfstoPick ingestion)
python -m scraper export --format professors -o output/professors.json

# Matrix Pipeline (Parallel Sharded Scraping)

The repo includes a 3-stage GitHub Actions matrix pipeline in `.github/workflows/matrix-scrape.yml`:

```
 ┌─────────────────────────────────────────────────────────────┐
 │                Stage 1: Parallel Shards                     │
 │          (10 Parallel Runner VMs on GitHub)                  │
 │                                                             │
 │  • Shard 0: sort:new         • Shard 5: query:Eng           │
 │  • Shard 1: sort:top         • Shard 6: query:Fil           │
 │  • Shard 2: sort:hot         • Shard 7: query:Bio           │
 │  • Shard 3: sort:rising      • Shard 8: query:Chem          │
 │  • Shard 4: query:Math       • Shard 9: query:Econ          │
 └──────────────────────────────┬──────────────────────────────┘
                                │ (Uploads Shard Artifacts)
                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                 Stage 2: Merge & CRS Match                  │
 │                                                             │
 │  • Merges all 10 shard JSON artifacts into main DB          │
 │  • Runs `crs_matcher` against official UP faculty catalog   │
 │  • Runs review signal extractor for student ratings          │
 │  • Commits updated `output/professors_crs.json`              │
 └─────────────────────────────────────────────────────────────┘
```

Benefits of the Matrix Architecture:
1. **IP Isolation**: Each parallel runner VM gets a different public IP address from GitHub's pool, bypassing per-IP rate limits.
2. **10x Scraping Yield**: Pulls hundreds of posts across all sorts and UP subject categories simultaneously.

### Statistics

```bash
python -m scraper stats
```

## How It Works

### Two-Phase Scrape

1. **Phase 1 — Listing fetch**: Hits `reddit.com/r/RateUPProfs/new.json` with pagination (`after` cursors). Falls back to RSS if JSON is rate-limited.
2. **Phase 2 — Comment enrichment**: For each post, hits `reddit.com/comments/{id}.json` to fetch the comment tree. Rate-paced at ~1.2s/request.

### Title Parsing

The subreddit enforces structured titles:
```
[CAMPUS] Course Code - LASTNAME, FIRSTNAME
```

The parser handles:
- All UP campus codes (UPD, UPLB, UPM, UPOU, UPV, UPMin, UPB, UPC, UPT)
- Multi-word courses (`Speech Comm 11`, `Nat Sci 2`)
- ALL-CAPS professor names → title-cased
- Hyphenated last names (`Santos-Reyes`)
- Compound names (`De La Cruz`)
- Name abbreviations (`Ma.`, `Jr.`, `III`)
- Parenthetical annotations (`(AY 2024-2025)`)
- Non-conforming titles → gracefully returns `None`

### Architecture

```
scraper/
├── config.py         # Constants, user-agents, campus codes
├── parser.py         # Regex title parser with fallback patterns
├── analyzer.py       # Rule-based student signal & keyword extractor
├── crs_matcher.py    # Cross-referencer against official UP CRS db
├── models.py         # Dataclasses: Professor, Post, Comment
├── database.py       # SQLite schema, upsert logic, queries
├── reddit_client.py  # Raw requests to .json endpoints + RSS fallback (or PRAW if authed)
├── exporter.py       # SQLite → JSON with CRS & signal enrichment
├── cli.py            # argparse CLI with rich progress bars
└── __main__.py       # python -m scraper entrypoint
```

### Database

SQLite with WAL mode for crash safety. Three tables:
- `professors` — unique professor records keyed on normalized ID
- `posts` — Reddit submissions with parsed metadata
- `comments` — flattened comment trees with `parent_id` for reconstruction

All writes use `INSERT OR REPLACE` so re-runs are idempotent.

## Running Tests

```bash
python -m pytest tests/ -v
```

## Compliance & Policy Safeguards

This tool is designed in alignment with [Reddit's Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/Responsible-Builder-Policy) and Philippine NPC Advisory No. 2026-01:

1. **Zero Redditor De-anonymization**:
   - The pipeline strictly decouples Reddit usernames from real-world identities.
   - No attempt is made to re-identify, track, or cross-match Reddit users with off-platform student accounts.

2. **Discovery & Index Layer Only**:
   - The primary export format (`professors` JSON) treats Reddit as an external index ("18 discussions on r/RateUPProfs → View on Reddit").
   - It outputs permalinks (`url`), scores, and comment counts to direct traffic back to original Reddit discussions rather than hosting permanent raw comment mirrors.

3. **No AI Model Training**:
   - Student sentiment signals are extracted using lightweight, deterministic rule-based regex matching ([analyzer.py](file:///Users/angelonrevelo/Antigravity/ruppscraper/scraper/analyzer.py)).
   - No scraped Reddit content is sold, commercialized, or used for machine learning or LLM model training.

4. **Rate Limit & Network Respect**:
   - Requests enforce a courtesy delay (`1.2s` between requests) to prevent network or API disruption.

