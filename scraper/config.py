"""Configuration and constants for the RateUPProfs scraper."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Project root & env loading
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT: str = os.getenv("REDDIT_USER_AGENT", "")

# ---------------------------------------------------------------------------
# Subreddit target
# ---------------------------------------------------------------------------
SUBREDDIT_NAME: str = "RateUPProfs"

# ---------------------------------------------------------------------------
# Reddit endpoints (built dynamically per subreddit, but defaults here)
# ---------------------------------------------------------------------------
BASE_REDDIT_URL: str = f"https://www.reddit.com/r/{SUBREDDIT_NAME}"

# ---------------------------------------------------------------------------
# Rotating user-agents (same pattern as pingfree)
# Reddit blocks default UAs and rate-limits datacenter IPs. Rotating
# between a handful of identifiers spreads the bucket.
# ---------------------------------------------------------------------------
USER_AGENTS: list[str] = [
    "ruppscraper:rateuprofs-indexer:1.0 (by /u/ruppscraper)",
    "ruppscraper-bot/1.0 (UP professor discovery; +https://github.com/ruppscraper)",
    "Mozilla/5.0 (compatible; ruppscraper/1.0)",
]

# ---------------------------------------------------------------------------
# Known UP campus codes
# ---------------------------------------------------------------------------
CAMPUS_CODES: set[str] = {
    "UPD",      # UP Diliman
    "UPLB",     # UP Los Baños
    "UPM",      # UP Manila
    "UPOU",     # UP Open University
    "UPV",      # UP Visayas
    "UPMin",    # UP Mindanao
    "UPB",      # UP Baguio
    "UPC",      # UP Cebu
    "UPT",      # UP Tacloban (Leyte)
    "UPP",      # UP Pampanga
}

# Normalized lookup (case-insensitive) → canonical form
CAMPUS_LOOKUP: dict[str, str] = {code.upper(): code for code in CAMPUS_CODES}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH: Path = PROJECT_ROOT / "ruppscraper.db"

# ---------------------------------------------------------------------------
# Scraper defaults
# ---------------------------------------------------------------------------
DEFAULT_SORT: str = "new"
REQUEST_TIMEOUT: int = 30
COURTESY_DELAY: float = 1.2            # seconds between comment enrichment calls
COMMENT_ENRICH_TOP_N: int = 10          # how many posts to enrich with comments
LISTING_LIMIT: int = 100                # max posts per JSON listing page
