"""Data models (dataclasses) for the RateUPProfs scraper."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class Professor:
    """A professor identified from post titles."""

    id: str               # normalized key: "upd__redelicia__romeo_joshua"
    last_name: str
    first_name: str
    campus: str

    @property
    def full_name(self) -> str:
        return f"{self.last_name}, {self.first_name}"


@dataclass(slots=True)
class Post:
    """A single r/RateUPProfs submission."""

    reddit_id: str                  # Reddit's base-36 submission ID
    title: str                      # Raw title text
    campus: str | None              # Parsed campus code
    course: str | None              # Parsed course name/code
    professor_id: str | None        # FK → Professor.id
    url: str                        # Full permalink
    score: int                      # Net upvotes
    num_comments: int               # Comment count
    created_utc: float              # Unix timestamp
    author: str | None              # Reddit username (None if deleted)
    selftext: str                   # Post body / self-text
    scraped_at: float = field(      # When we scraped this
        default_factory=lambda: datetime.now(timezone.utc).timestamp()
    )

    @property
    def created_iso(self) -> str:
        """Return ISO-8601 timestamp."""
        return datetime.fromtimestamp(self.created_utc, tz=timezone.utc).isoformat()


@dataclass(slots=True)
class Comment:
    """A single comment on a r/RateUPProfs submission."""

    reddit_id: str                  # Comment's base-36 ID
    post_reddit_id: str             # FK → Post.reddit_id
    parent_id: str                  # Parent comment/post ID (for tree reconstruction)
    author: str | None              # Reddit username (None if deleted)
    body: str                       # Comment text (markdown)
    score: int                      # Net upvotes
    created_utc: float              # Unix timestamp
    depth: int                      # Nesting level (0 = top-level reply)
    scraped_at: float = field(      # When we scraped this
        default_factory=lambda: datetime.now(timezone.utc).timestamp()
    )

    @property
    def created_iso(self) -> str:
        """Return ISO-8601 timestamp."""
        return datetime.fromtimestamp(self.created_utc, tz=timezone.utc).isoformat()
