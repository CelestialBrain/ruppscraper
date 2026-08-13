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

# Bracket tokens that are not campuses (title slang / flair).
CAMPUS_ALIASES: dict[str, str] = {
    "DILIMAN": "UPD",
    "UPDILIMAN": "UPD",
    "UP-DILIMAN": "UPD",
}
FAKE_CAMPUS_CODES: set[str] = {
    "REVIEW",
    "PREROG",
    "EMAIL",
    "HELP",
    "QUESTION",
    "ART",
    "UPX",
}


def canonical_campus(raw: str | None) -> str | None:
    """Map a title campus token to a real UP code, or None if unknown."""
    if not raw:
        return None
    key = raw.strip().upper()
    if key in CAMPUS_LOOKUP:
        return CAMPUS_LOOKUP[key]
    if key in CAMPUS_ALIASES:
        return CAMPUS_ALIASES[key]
    if key in FAKE_CAMPUS_CODES:
        return "UPD"
    return None

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH: Path = PROJECT_ROOT / "ruppscraper.db"

# ---------------------------------------------------------------------------
# Scraper defaults
# ---------------------------------------------------------------------------
DEFAULT_SORT: str = "new"
REQUEST_TIMEOUT: int = 30
COURTESY_DELAY: float = float(os.getenv("RUPP_COURTESY_DELAY", "1.2"))
COMMENT_WORKERS: int = max(1, int(os.getenv("RUPP_COMMENT_WORKERS", "1")))
ARCTIC_PAGE_DELAY: float = float(os.getenv("RUPP_ARCTIC_PAGE_DELAY", "0.35"))
PREFER_ARCTIC: bool = os.getenv("RUPP_PREFER_ARCTIC", "").lower() in {
    "1",
    "true",
    "yes",
}
COMMENT_ENRICH_TOP_N: int = 10          # how many posts to enrich with comments
LISTING_LIMIT: int = 100                # max posts per JSON listing page

# Arctic Shift archive API — used when Reddit's public .json endpoints
# return 403/429 (common from datacenter / residential IPs without OAuth).
ARCTIC_SHIFT_BASE: str = os.getenv(
    "ARCTIC_SHIFT_BASE",
    "https://arctic-shift.photon-reddit.com",
)

# Progressive scrape-all passes: (sort, query|None, post_limit, comment_limit)
# Tuned for Arctic Shift / polite pacing — widen via CLI flags when needed.
PROGRESSIVE_PASSES: list[tuple[str, str | None, int, int]] = [
    ("new", None, 100, 50),
    ("hot", None, 100, 50),
    ("top", None, 200, 100),
    ("rising", None, 50, 25),
    ("new", "Math", 100, 50),
    ("new", "Eng", 100, 50),
    ("new", "Fil", 100, 50),
    ("new", "Bio", 80, 40),
    ("new", "Chem", 80, 40),
    ("new", "Econ", 80, 40),
    ("new", "Speech", 80, 40),
    ("new", "KAS", 80, 40),
    ("new", "Psych", 80, 40),
]
