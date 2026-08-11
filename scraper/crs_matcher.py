"""Cross-reference scraped Reddit professors against the CRS instructor database.

The CRS database (from /Users/angelonrevelo/Antigravity/crs) contains ~8,000
instructors with normalized names and the courses they teach. This module:

  1. Loads the CRS instructor table as a fuzzy-match lookup.
  2. Matches Reddit-parsed professor names against CRS instructors.
  3. Enriches scraped posts with verified instructor IDs + course listings.
  4. Provides a CLI command to run the matching and report results.

Name matching strategy (ordered by confidence):
  - Exact match on name_normalized (e.g. "BUHAIN, CARMELO JOSE")
  - Last-name + first-token match (handles "NERI, MARRICK" vs "NERI, MARRICK S.")
  - Last-name only match (returns candidates, not auto-linked)
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default CRS database path (sibling repo)
# ---------------------------------------------------------------------------
DEFAULT_CRS_DB_PATH = Path("/Users/angelonrevelo/Antigravity/crs/data/crs.db")


@dataclass(frozen=True, slots=True)
class CRSInstructor:
    """An instructor record from the CRS database."""
    instructor_id: str
    name_normalized: str    # "BUHAIN, CARMELO JOSE"
    name_display: str       # "Buhain, Carmelo Jose"


@dataclass(frozen=True, slots=True)
class CRSMatch:
    """Result of matching a scraped professor against CRS data."""
    instructor: CRSInstructor
    courses: list[str]          # course codes from CRS sections
    university_id: str          # "upd", "admu", etc.
    match_type: str             # "exact", "first_token", "last_name_only"
    confidence: float           # 1.0 = exact, 0.8 = first_token, 0.5 = last_name


# ---------------------------------------------------------------------------
# CRS Lookup
# ---------------------------------------------------------------------------

class CRSLookup:
    """In-memory lookup for CRS instructors, optimized for name matching."""

    def __init__(self, crs_db_path: Path | None = None):
        self._db_path = crs_db_path or DEFAULT_CRS_DB_PATH
        self._instructors: list[CRSInstructor] = []
        # Indexes for fast lookup
        self._by_normalized: dict[str, CRSInstructor] = {}          # exact match
        self._by_last_first_token: dict[str, list[CRSInstructor]] = {}  # "LAST, FIRST"
        self._by_last_name: dict[str, list[CRSInstructor]] = {}    # last name only
        # Course lookup: instructor_id → [course_codes]
        self._courses: dict[str, list[str]] = {}
        # University lookup: instructor_id → university_id
        self._university: dict[str, str] = {}
        self._loaded = False

    @property
    def is_available(self) -> bool:
        """Check if the CRS database file exists."""
        return self._db_path.exists()

    @property
    def instructor_count(self) -> int:
        return len(self._instructors)

    def load(self) -> None:
        """Load all instructors from the CRS database into memory."""
        if self._loaded:
            return
        if not self.is_available:
            return

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row

        # Load instructors
        rows = conn.execute(
            "SELECT instructor_id, name_normalized, name_display FROM instructor"
        ).fetchall()

        for row in rows:
            inst = CRSInstructor(
                instructor_id=row["instructor_id"],
                name_normalized=row["name_normalized"],
                name_display=row["name_display"],
            )
            self._instructors.append(inst)

            # Index: exact normalized name
            self._by_normalized[inst.name_normalized.upper()] = inst

            # Index: last_name + first token of first name
            parts = inst.name_normalized.split(",", 1)
            if len(parts) == 2:
                last = parts[0].strip().upper()
                first_tokens = parts[1].strip().upper().split()
                if first_tokens:
                    key = f"{last},{first_tokens[0]}"
                    self._by_last_first_token.setdefault(key, []).append(inst)
                # Index: last name only
                self._by_last_name.setdefault(last, []).append(inst)

        # Load instructor → courses mapping
        course_rows = conn.execute("""
            SELECT DISTINCT si.instructor_id, c.course_code
            FROM section_instructor si
            JOIN section s ON s.section_id = si.section_id
            JOIN course c ON c.course_id = s.course_id
        """).fetchall()

        for row in course_rows:
            iid = row["instructor_id"]
            self._courses.setdefault(iid, []).append(row["course_code"])

        # Load instructor → university mapping (take most common)
        uni_rows = conn.execute("""
            SELECT si.instructor_id, s.university_id, COUNT(*) AS cnt
            FROM section_instructor si
            JOIN section s ON s.section_id = si.section_id
            GROUP BY si.instructor_id, s.university_id
            ORDER BY cnt DESC
        """).fetchall()

        for row in uni_rows:
            iid = row["instructor_id"]
            if iid not in self._university:
                self._university[iid] = row["university_id"]

        conn.close()
        self._loaded = True

    def match(self, last_name: str, first_name: str) -> CRSMatch | None:
        """Try to match a professor name against CRS instructors.

        Returns the best match or None.
        """
        if not self._loaded:
            self.load()
        if not self._instructors:
            return None

        last_upper = last_name.strip().upper()
        first_upper = first_name.strip().upper()
        normalized = f"{last_upper}, {first_upper}"

        # 1. Exact match on full normalized name
        inst = self._by_normalized.get(normalized)
        if inst:
            return self._build_match(inst, "exact", 1.0)

        # 2. Last name + first token of first name
        first_token = first_upper.split()[0] if first_upper else ""
        if first_token:
            key = f"{last_upper},{first_token}"
            candidates = self._by_last_first_token.get(key, [])
            if len(candidates) == 1:
                return self._build_match(candidates[0], "first_token", 0.8)

        # 3. Last name only (return best candidate if unambiguous)
        candidates = self._by_last_name.get(last_upper, [])
        if len(candidates) == 1:
            return self._build_match(candidates[0], "last_name_only", 0.5)

        return None

    def match_all(self, last_name: str, first_name: str) -> list[CRSMatch]:
        """Return all possible matches (for ambiguous cases)."""
        if not self._loaded:
            self.load()
        if not self._instructors:
            return []

        last_upper = last_name.strip().upper()
        first_upper = first_name.strip().upper()
        normalized = f"{last_upper}, {first_upper}"
        results: list[CRSMatch] = []

        # Exact
        inst = self._by_normalized.get(normalized)
        if inst:
            results.append(self._build_match(inst, "exact", 1.0))
            return results

        # First token
        first_token = first_upper.split()[0] if first_upper else ""
        if first_token:
            key = f"{last_upper},{first_token}"
            for inst in self._by_last_first_token.get(key, []):
                results.append(self._build_match(inst, "first_token", 0.8))
            if results:
                return results

        # Last name
        for inst in self._by_last_name.get(last_upper, []):
            results.append(self._build_match(inst, "last_name_only", 0.5))

        return results

    def _build_match(self, inst: CRSInstructor, match_type: str, confidence: float) -> CRSMatch:
        courses = sorted(set(self._courses.get(inst.instructor_id, [])))
        university = self._university.get(inst.instructor_id, "unknown")
        return CRSMatch(
            instructor=inst,
            courses=courses,
            university_id=university,
            match_type=match_type,
            confidence=confidence,
        )


# ---------------------------------------------------------------------------
# Batch matching against ruppscraper's database
# ---------------------------------------------------------------------------

def match_scraped_professors(
    rupp_db_path: Path,
    crs_db_path: Path | None = None,
) -> dict[str, Any]:
    """Match all professors in the ruppscraper DB against CRS instructors.

    Returns a summary dict with match statistics and details.
    """
    lookup = CRSLookup(crs_db_path)
    if not lookup.is_available:
        return {"error": "CRS database not found", "path": str(lookup._db_path)}

    lookup.load()

    # Load professors from ruppscraper DB
    conn = sqlite3.connect(str(rupp_db_path))
    conn.row_factory = sqlite3.Row
    professors = conn.execute("SELECT * FROM professors").fetchall()
    conn.close()

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    for prof in professors:
        last_name = prof["last_name"]
        first_name = prof["first_name"]
        campus = prof["campus"]

        best = lookup.match(last_name, first_name)
        all_matches = lookup.match_all(last_name, first_name)

        prof_info = {
            "id": prof["id"],
            "name": f"{last_name}, {first_name}",
            "campus": campus,
        }

        if best and best.confidence >= 0.8:
            matched.append({
                **prof_info,
                "crs_instructor_id": best.instructor.instructor_id,
                "crs_name": best.instructor.name_display,
                "crs_university": best.university_id,
                "crs_courses": best.courses,
                "match_type": best.match_type,
                "confidence": best.confidence,
            })
        elif all_matches:
            ambiguous.append({
                **prof_info,
                "candidates": [
                    {
                        "crs_name": m.instructor.name_display,
                        "crs_courses": m.courses,
                        "match_type": m.match_type,
                        "confidence": m.confidence,
                    }
                    for m in all_matches
                ],
            })
        else:
            unmatched.append(prof_info)

    return {
        "crs_instructor_count": lookup.instructor_count,
        "scraped_professor_count": len(professors),
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "match_rate": f"{len(matched)}/{len(professors)}" if professors else "0/0",
    }
