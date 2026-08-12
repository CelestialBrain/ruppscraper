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
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Default CRS database path (sibling repo)
# ---------------------------------------------------------------------------
DEFAULT_CRS_DB_PATH = Path("/Users/angelonrevelo/Antigravity/crs/data/crs.db")

_HONORIFIC_RE = re.compile(
    r"^(?:sir|ma'?am|mam|ms\.?|mr\.?|mrs\.?|mx\.?|prof\.?|professor|teacher|doc\.?|dr\.?)$",
    re.IGNORECASE,
)


def _fold_accents(text: str) -> str:
    """NFKD accent-fold so CASTAÑEDA matches CASTANEDA."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _norm_key(text: str) -> str:
    return _fold_accents(text).strip().upper()


def _strip_honorific_tokens(text: str) -> str:
    kept = [tok for tok in text.split() if not _HONORIFIC_RE.match(tok.strip(".,"))]
    return " ".join(kept).strip()


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
        # First word of a multi-word surname ("INAZUNTA" from "INAZUNTA MARCA")
        self._by_last_head: dict[str, list[CRSInstructor]] = {}
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

            # Index: exact normalized name (accent-folded)
            self._by_normalized[_norm_key(inst.name_normalized)] = inst

            # Index: last_name + first token of first name
            parts = inst.name_normalized.split(",", 1)
            if len(parts) == 2:
                last = _norm_key(parts[0])
                first_tokens = _norm_key(parts[1]).split()
                if first_tokens:
                    key = f"{last},{first_tokens[0]}"
                    self._by_last_first_token.setdefault(key, []).append(inst)
                # Index: last name only
                self._by_last_name.setdefault(last, []).append(inst)
                last_words = last.split()
                if len(last_words) >= 2:
                    self._by_last_head.setdefault(last_words[0], []).append(inst)
                    if first_tokens:
                        self._by_last_first_token.setdefault(
                            f"{last_words[0]},{first_tokens[0]}", []
                        ).append(inst)

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

        for attempt_last, attempt_first, tag in self._name_attempts(last_name, first_name):
            hit = self._match_pair(attempt_last, attempt_first, order_tag=tag)
            if hit is not None:
                return hit
        return None

    def match_all(self, last_name: str, first_name: str) -> list[CRSMatch]:
        """Return all possible matches (for ambiguous cases)."""
        if not self._loaded:
            self.load()
        if not self._instructors:
            return []

        seen: set[str] = set()
        results: list[CRSMatch] = []
        for attempt_last, attempt_first, _tag in self._name_attempts(last_name, first_name):
            for hit in self._match_pair_all(attempt_last, attempt_first):
                if hit.instructor.instructor_id in seen:
                    continue
                seen.add(hit.instructor.instructor_id)
                results.append(hit)
            # Prefer exact/first_token over dumping every last-name candidate.
            if results and results[0].confidence >= 0.8:
                return results
        return results

    def _name_attempts(
        self, last_name: str, first_name: str
    ) -> list[tuple[str, str, str]]:
        """Generate (last, first, tag) variants to try, in preference order."""
        last = _strip_honorific_tokens(last_name)
        first = _strip_honorific_tokens(first_name)
        attempts: list[tuple[str, str, str]] = [(last, first, "as_is")]
        # Hyphen drift: "Orozco-Bautista, Zenith" vs CRS "Orozco, Zenith Gaye".
        if "-" in last:
            head = last.split("-", 1)[0].strip()
            if head:
                attempts.append((head, first, "hyphen_head"))
        # Reddit sometimes posts "GIVEN SURNAME" without a comma; the parser then
        # stores last=SurnameToken, first=Given… — also try the reverse.
        if last and first:
            attempts.append((first, last, "reversed"))
            # "Conception Mary Grace" parsed as last=Grace, first=Conception Mary
            # → try moving the first token of first_name into last.
            first_parts = first.split()
            if len(first_parts) >= 2:
                attempts.append(
                    (first_parts[0], " ".join(first_parts[1:] + [last]), "rotate")
                )
        # Deduplicate while preserving order
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str, str]] = []
        for a_last, a_first, tag in attempts:
            key = (_norm_key(a_last), _norm_key(a_first))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            unique.append((a_last, a_first, tag))
        return unique

    def _match_pair(
        self, last_name: str, first_name: str, *, order_tag: str
    ) -> CRSMatch | None:
        last_key = _norm_key(last_name)
        first_key = _norm_key(first_name)
        normalized = f"{last_key}, {first_key}"

        inst = self._by_normalized.get(normalized)
        if inst:
            label = "exact" if order_tag == "as_is" else f"exact_{order_tag}"
            return self._build_match(inst, label, 1.0)

        first_token = first_key.split()[0] if first_key else ""
        if first_token:
            key = f"{last_key},{first_token}"
            candidates = self._dedupe_instructors(self._by_last_first_token.get(key, []))
            if len(candidates) == 1:
                label = (
                    "first_token" if order_tag == "as_is" else f"first_token_{order_tag}"
                )
                return self._build_match(candidates[0], label, 0.8)

        candidates = self._dedupe_instructors(self._by_last_name.get(last_key, []))
        if len(candidates) == 1:
            return self._build_match(candidates[0], "last_name_only", 0.5)

        return None

    def _match_pair_all(self, last_name: str, first_name: str) -> list[CRSMatch]:
        last_key = _norm_key(last_name)
        first_key = _norm_key(first_name)
        normalized = f"{last_key}, {first_key}"
        results: list[CRSMatch] = []

        inst = self._by_normalized.get(normalized)
        if inst:
            return [self._build_match(inst, "exact", 1.0)]

        first_token = first_key.split()[0] if first_key else ""
        if first_token:
            key = f"{last_key},{first_token}"
            for inst in self._dedupe_instructors(self._by_last_first_token.get(key, [])):
                results.append(self._build_match(inst, "first_token", 0.8))
            if results:
                return results

        for inst in self._dedupe_instructors(self._by_last_name.get(last_key, [])):
            results.append(self._build_match(inst, "last_name_only", 0.5))
        return results

    @staticmethod
    def _dedupe_instructors(items: list[CRSInstructor]) -> list[CRSInstructor]:
        seen: set[str] = set()
        out: list[CRSInstructor] = []
        for inst in items:
            if inst.instructor_id in seen:
                continue
            seen.add(inst.instructor_id)
            out.append(inst)
        return out

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
