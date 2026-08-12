"""Mention-level CRS resolution report for the ProfstoPick acceptance gate.

Acceptance (profstopick ROADMAP): a sample of 100 scraped mentions resolves
≥80% to a roster professor, and every unresolved one is reported rather than
silently dropped.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scraper.config import DB_PATH
from scraper.crs_matcher import CRSLookup, DEFAULT_CRS_DB_PATH


@dataclass(frozen=True, slots=True)
class MentionRow:
    reddit_id: str
    title: str
    campus: str | None
    course: str | None
    professor_id: str
    last_name: str
    first_name: str
    created_utc: float | None


def load_mentions(
    rupp_db_path: Path | None = None,
    *,
    campus: str | None = "UPD",
) -> list[MentionRow]:
    """Load parsed professor mentions (posts with a resolved professor_id)."""
    path = rupp_db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    sql = """
        SELECT p.reddit_id, p.title, p.campus, p.course, p.professor_id,
               p.created_utc, pr.last_name, pr.first_name
        FROM posts p
        JOIN professors pr ON pr.id = p.professor_id
        WHERE p.professor_id IS NOT NULL
    """
    params: list[Any] = []
    if campus:
        sql += " AND UPPER(COALESCE(p.campus, '')) = ?"
        params.append(campus.upper())
    sql += " ORDER BY p.created_utc DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [
        MentionRow(
            reddit_id=r["reddit_id"],
            title=r["title"],
            campus=r["campus"],
            course=r["course"],
            professor_id=r["professor_id"],
            last_name=r["last_name"],
            first_name=r["first_name"],
            created_utc=r["created_utc"],
        )
        for r in rows
    ]


def classify_mention(
    lookup: CRSLookup, last_name: str, first_name: str
) -> tuple[str, dict[str, Any] | None]:
    """Return (status, detail) where status is resolved|ambiguous|unresolved."""
    best = lookup.match(last_name, first_name)
    all_matches = lookup.match_all(last_name, first_name)
    if best and best.confidence >= 0.8:
        return "resolved", {
            "crs_instructor_id": best.instructor.instructor_id,
            "crs_name": best.instructor.name_display,
            "match_type": best.match_type,
            "confidence": best.confidence,
        }
    if all_matches:
        return "ambiguous", {
            "candidates": [
                {
                    "crs_name": m.instructor.name_display,
                    "match_type": m.match_type,
                    "confidence": m.confidence,
                }
                for m in all_matches[:5]
            ]
        }
    return "unresolved", None


def build_resolve_report(
    *,
    sample_size: int = 100,
    seed: int = 42,
    campus: str | None = "UPD",
    rupp_db_path: Path | None = None,
    crs_db_path: Path | None = None,
    strategy: str = "recent",
) -> dict[str, Any]:
    """Build a mention resolution report. Unresolved entries are always listed."""
    crs_path = crs_db_path or DEFAULT_CRS_DB_PATH
    lookup = CRSLookup(crs_path)
    if not lookup.is_available:
        return {"error": "CRS database not found", "path": str(crs_path)}

    lookup.load()
    mentions = load_mentions(rupp_db_path, campus=campus)
    if not mentions:
        return {
            "error": "no parsed mentions in scraper DB",
            "sample_size": 0,
            "resolved": 0,
            "resolve_rate": 0.0,
        }

    if strategy == "random":
        rng = random.Random(seed)
        sample = rng.sample(mentions, min(sample_size, len(mentions)))
    else:
        sample = mentions[:sample_size]

    resolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for m in sample:
        status, detail = classify_mention(lookup, m.last_name, m.first_name)
        base = {
            "reddit_id": m.reddit_id,
            "title": m.title,
            "campus": m.campus,
            "course": m.course,
            "professor": f"{m.last_name}, {m.first_name}",
            "professor_id": m.professor_id,
            "permalink": f"https://reddit.com/r/RateUPProfs/comments/{m.reddit_id}",
        }
        if status == "resolved":
            resolved.append({**base, **(detail or {})})
        elif status == "ambiguous":
            ambiguous.append({**base, **(detail or {})})
        else:
            unresolved.append(base)

    total = len(sample)
    rate = len(resolved) / total if total else 0.0
    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "crs_db": str(crs_path),
        "crs_instructor_count": lookup.instructor_count,
        "mention_pool": len(mentions),
        "campus_filter": campus,
        "strategy": strategy,
        "sample_size": total,
        "seed": seed if strategy == "random" else None,
        "resolved_count": len(resolved),
        "ambiguous_count": len(ambiguous),
        "unresolved_count": len(unresolved),
        "resolve_rate": round(rate, 4),
        "resolve_rate_pct": f"{rate:.1%}",
        "acceptance_threshold": 0.80,
        "acceptance_met": rate >= 0.80,
        # Full lists — unresolved is never silently dropped.
        "resolved": resolved,
        "ambiguous": ambiguous,
        "unresolved": unresolved,
    }


def write_resolve_report(report: dict[str, Any], output_path: Path) -> Path:
    """Write the report JSON (including the unresolved list) to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return output_path
