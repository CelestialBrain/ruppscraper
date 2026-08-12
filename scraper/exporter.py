"""JSON exporter for scraped r/RateUPProfs data.

Two export formats:
  - "full"       — Every post with its comments and professor info.
  - "professors" — Grouped by professor with aggregated discussion metadata.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scraper.analyzer import analyze_text
from scraper.database import (
    get_all_posts_with_comments,
    get_connection,
    get_professors_grouped,
    init_db,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_full(output_path: Path, db_path: Path | None = None) -> int:
    """Export all posts + comments as a flat JSON list.

    Returns the number of posts exported.
    """
    conn = get_connection(db_path)
    init_db(conn)
    data = get_all_posts_with_comments(conn)
    conn.close()

    export: list[dict[str, Any]] = []
    for entry in data:
        post = entry["post"]
        export.append({
            "campus": post.get("campus"),
            "course": post.get("course"),
            "professor": {
                "last_name": post.get("prof_last"),
                "first_name": post.get("prof_first"),
            } if post.get("prof_last") else None,
            "post": {
                "reddit_id": post["reddit_id"],
                "title": post["title"],
                "url": post["url"],
                "score": post["score"],
                "num_comments": post["num_comments"],
                "posted_at": _ts_to_iso(post["created_utc"]),
                "author": post.get("author"),
                "body": post.get("selftext", ""),
            },
            "comments": [
                {
                    "reddit_id": c["reddit_id"],
                    "parent_id": c.get("parent_id"),
                    "author": c.get("author"),
                    "body": c["body"],
                    "score": c["score"],
                    "depth": c["depth"],
                    "posted_at": _ts_to_iso(c["created_utc"]),
                }
                for c in entry["comments"]
            ],
        })

    _write_json(export, output_path)
    return len(export)


def export_professors(
    output_path: Path,
    db_path: Path | None = None,
    with_crs: bool = False,
    crs_db_path: Path | None = None,
) -> int:
    """Export professor-grouped data as JSON.

    If ``with_crs`` is True, enriches each professor with matched CRS instructor details.
    Returns the number of professors exported.
    """
    from scraper.crs_matcher import CRSLookup

    crs_lookup = None
    if with_crs:
        crs_lookup = CRSLookup(crs_db_path)
        if crs_lookup.is_available:
            crs_lookup.load()

    conn = get_connection(db_path)
    init_db(conn)
    data = get_professors_grouped(conn)
    conn.close()

    export: list[dict[str, Any]] = []
    for entry in data:
        prof = entry["professor"]
        last_name = prof["last_name"]
        first_name = prof["first_name"]

        # Combine discussion bodies for signal extraction
        all_text_blobs = [d.get("selftext", "") for d in entry["discussions"]]

        # Also fetch comments for these discussions if conn is available
        conn_sub = get_connection(db_path)
        for d in entry["discussions"]:
            comments_rows = conn_sub.execute(
                "SELECT body FROM comments WHERE post_reddit_id = ?", (d["reddit_id"],)
            ).fetchall()
            all_text_blobs.extend(c["body"] for c in comments_rows)
        conn_sub.close()

        combined_text = "\n".join(all_text_blobs)
        signals = analyze_text(combined_text)

        prof_dict: dict[str, Any] = {
            "professor": f"{last_name}, {first_name}",
            "campus": prof["campus"],
            "courses": entry["courses"],
            "total_discussions": entry["total_discussions"],
            "signals_summary": signals.to_dict(),
            "discussions": [
                {
                    "course": d.get("course"),
                    "url": f"https://reddit.com/r/RateUPProfs/comments/{d['reddit_id']}",
                    "score": d["score"],
                    "comment_count": d["num_comments"],
                    "posted_at": _ts_to_iso(d["created_utc"]),
                }
                for d in entry["discussions"]
            ],
        }

        if crs_lookup and crs_lookup.is_available:
            match = crs_lookup.match(last_name, first_name)
            if match:
                prof_dict["crs_verified"] = {
                    "instructor_id": match.instructor.instructor_id,
                    "official_name": match.instructor.name_display,
                    "university_id": match.university_id,
                    "match_type": match.match_type,
                    "confidence": match.confidence,
                    "official_courses": match.courses,
                }
            else:
                prof_dict["crs_verified"] = None

        export.append(prof_dict)

    _write_json(export, output_path)
    return len(export)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts_to_iso(ts: float | None) -> str | None:
    """Convert a Unix timestamp to ISO-8601 string."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _write_json(data: Any, path: Path) -> None:
    """Write data as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
