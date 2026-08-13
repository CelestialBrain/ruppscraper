"""Cross-reference scraped Reddit professors against the CRS instructor database.

Matching stack (blead-inspired):
  1. Clean scraped names (honorifics, section prefixes, accent fold)
  2. Exact / first-token / reversed / particle-aware attempts
  3. Course-code overlap to break ambiguous last-name ties
  4. Skip junk professor rows that are bad title parses
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scraper.name_resolver import (
    clean_scraped_name,
    fold_key,
    is_plausible_professor_name,
    split_name_parts,
)

# ---------------------------------------------------------------------------
# Default CRS database path (sibling repo)
# ---------------------------------------------------------------------------
DEFAULT_CRS_DB_PATH = Path("/Users/angelonrevelo/Antigravity/crs/data/crs.db")


def _norm_course(code: str) -> str:
    """Normalize course codes for overlap checks: 'Math 22' → 'MATH22'."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


@dataclass(frozen=True, slots=True)
class CRSInstructor:
    """An instructor record from the CRS database."""

    instructor_id: str
    name_normalized: str  # "BUHAIN, CARMELO JOSE"
    name_display: str  # "Buhain, Carmelo Jose"


@dataclass(frozen=True, slots=True)
class CRSMatch:
    """Result of matching a scraped professor against CRS data."""

    instructor: CRSInstructor
    courses: list[str]
    university_id: str
    match_type: str
    confidence: float


class CRSLookup:
    """In-memory lookup for CRS instructors, optimized for name matching."""

    def __init__(self, crs_db_path: Path | None = None):
        self._db_path = crs_db_path or DEFAULT_CRS_DB_PATH
        self._instructors: list[CRSInstructor] = []
        self._by_normalized: dict[str, CRSInstructor] = {}
        self._by_last_first_token: dict[str, list[CRSInstructor]] = {}
        self._by_last_name: dict[str, list[CRSInstructor]] = {}
        self._by_last_head: dict[str, list[CRSInstructor]] = {}
        self._courses: dict[str, list[str]] = {}
        self._university: dict[str, str] = {}
        self._loaded = False

    @property
    def is_available(self) -> bool:
        return self._db_path.exists()

    @property
    def instructor_count(self) -> int:
        return len(self._instructors)

    def load(self) -> None:
        if self._loaded:
            return
        if not self.is_available:
            return

        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row

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

            parts = inst.name_normalized.split(",", 1)
            if len(parts) == 2:
                last = fold_key(parts[0])
                first_folded = fold_key(parts[1])
                # Keep comma so exact lookup keys match "LAST, FIRST"
                self._by_normalized[f"{last}, {first_folded}"] = inst
                first_tokens = first_folded.split()
                # Drop single-letter initials from token index
                first_tokens = [t for t in first_tokens if len(t) > 1] or first_tokens
                if first_tokens:
                    key = f"{last},{first_tokens[0]}"
                    self._by_last_first_token.setdefault(key, []).append(inst)
                self._by_last_name.setdefault(last, []).append(inst)
                last_words = last.split()
                if len(last_words) >= 2:
                    self._by_last_head.setdefault(last_words[0], []).append(inst)
                    if first_tokens:
                        self._by_last_first_token.setdefault(
                            f"{last_words[0]},{first_tokens[0]}", []
                        ).append(inst)
            else:
                self._by_normalized[fold_key(inst.name_normalized)] = inst

        for row in conn.execute(
            """
            SELECT DISTINCT si.instructor_id, c.course_code
            FROM section_instructor si
            JOIN section s ON s.section_id = si.section_id
            JOIN course c ON c.course_id = s.course_id
            """
        ):
            self._courses.setdefault(row["instructor_id"], []).append(row["course_code"])

        for row in conn.execute(
            """
            SELECT si.instructor_id, s.university_id, COUNT(*) AS cnt
            FROM section_instructor si
            JOIN section s ON s.section_id = si.section_id
            GROUP BY si.instructor_id, s.university_id
            ORDER BY cnt DESC
            """
        ):
            if row["instructor_id"] not in self._university:
                self._university[row["instructor_id"]] = row["university_id"]

        conn.close()
        self._loaded = True

    def match(
        self,
        last_name: str,
        first_name: str,
        courses: list[str] | None = None,
        university_id: str | None = None,
    ) -> CRSMatch | None:
        """Best match, optionally disambiguated by scraped course codes."""
        if not self._loaded:
            self.load()
        if not self._instructors:
            return None

        last, first = clean_scraped_name(last_name, first_name)
        if not last:
            return None

        # Prefer high-confidence direct hits
        for attempt_last, attempt_first, tag in self._name_attempts(last, first):
            hit = self._match_pair(attempt_last, attempt_first, order_tag=tag)
            if hit is not None and hit.confidence >= 0.8:
                return hit

        # Ambiguous / last-name-only: try course (+ campus) disambiguation
        candidates = self.match_all(last, first)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]

        picked = self._disambiguate(candidates, courses, university_id)
        return picked

    def match_all(self, last_name: str, first_name: str) -> list[CRSMatch]:
        if not self._loaded:
            self.load()
        if not self._instructors:
            return []

        last, first = clean_scraped_name(last_name, first_name)
        seen: set[str] = set()
        results: list[CRSMatch] = []
        for attempt_last, attempt_first, _tag in self._name_attempts(last, first):
            for hit in self._match_pair_all(attempt_last, attempt_first):
                if hit.instructor.instructor_id in seen:
                    continue
                seen.add(hit.instructor.instructor_id)
                results.append(hit)
            if results and results[0].confidence >= 0.8:
                return results
        return results

    def _name_attempts(
        self, last_name: str, first_name: str
    ) -> list[tuple[str, str, str]]:
        last, first = last_name, first_name
        attempts: list[tuple[str, str, str]] = [(last, first, "as_is")]

        if "-" in last:
            head = last.split("-", 1)[0].strip()
            if head:
                attempts.append((head, first, "hyphen_head"))

        # Plain "First Last" dumped entirely into last_name
        if last and not first:
            plain_last, plain_first = split_name_parts(last)
            if plain_first:
                attempts.append((plain_last, plain_first, "split_plain"))

        if last and first:
            attempts.append((first, last, "reversed"))
            first_parts = first.split()
            if len(first_parts) >= 2:
                attempts.append(
                    (first_parts[0], " ".join(first_parts[1:] + [last]), "rotate")
                )
            # Particle surnames: "Cruz" + "Dela" sitting in first tokens
            # already handled by clean; also try joining trailing particle
            # from first into last ("Juan", "Dela Cruz" stored wrong).
            if len(first_parts) >= 2:
                attempts.append(
                    (
                        f"{first_parts[-1]} {last}".strip()
                        if first_parts[-1].lower()
                        in {"dela", "de", "del", "delos", "san"}
                        else last,
                        " ".join(first_parts[:-1])
                        if first_parts[-1].lower()
                        in {"dela", "de", "del", "delos", "san"}
                        else first,
                        "particle_join",
                    )
                )

        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str, str]] = []
        for a_last, a_first, tag in attempts:
            key = (fold_key(a_last), fold_key(a_first))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            unique.append((a_last, a_first, tag))
        return unique

    def _match_pair(
        self, last_name: str, first_name: str, *, order_tag: str
    ) -> CRSMatch | None:
        last_key = fold_key(last_name)
        first_key = fold_key(first_name)
        # Strip single-letter initials from scraped first name for exact key
        first_words = [w for w in first_key.split() if len(w) > 1]
        first_for_exact = " ".join(first_words) if first_words else first_key
        normalized = f"{last_key}, {first_for_exact}".strip(", ")

        inst = self._by_normalized.get(normalized)
        if inst:
            label = "exact" if order_tag == "as_is" else f"exact_{order_tag}"
            return self._build_match(inst, label, 1.0)

        # Also try full first_key (with initials) exact
        if first_for_exact != first_key:
            inst = self._by_normalized.get(f"{last_key}, {first_key}")
            if inst:
                return self._build_match(inst, "exact", 1.0)

        first_token = first_words[0] if first_words else (
            first_key.split()[0] if first_key else ""
        )
        if first_token:
            key = f"{last_key},{first_token}"
            candidates = self._dedupe(self._by_last_first_token.get(key, []))
            if len(candidates) == 1:
                label = (
                    "first_token" if order_tag == "as_is" else f"first_token_{order_tag}"
                )
                return self._build_match(candidates[0], label, 0.8)

        candidates = self._dedupe(self._by_last_name.get(last_key, []))
        if len(candidates) == 1:
            return self._build_match(candidates[0], "last_name_only", 0.5)

        # Multi-word CRS surnames indexed by head token
        head_hits = self._dedupe(self._by_last_head.get(last_key, []))
        if first_token:
            narrowed = [
                c
                for c in head_hits
                if fold_key(c.name_normalized).split(",", 1)[-1].split()[:1]
                == [first_token]
            ]
            if len(narrowed) == 1:
                return self._build_match(narrowed[0], "last_head_first_token", 0.8)
        if len(head_hits) == 1:
            return self._build_match(head_hits[0], "last_head_only", 0.45)

        return None

    def _match_pair_all(self, last_name: str, first_name: str) -> list[CRSMatch]:
        last_key = fold_key(last_name)
        first_key = fold_key(first_name)
        first_words = [w for w in first_key.split() if len(w) > 1]
        first_for_exact = " ".join(first_words) if first_words else first_key

        inst = self._by_normalized.get(f"{last_key}, {first_for_exact}".strip(", "))
        if inst:
            return [self._build_match(inst, "exact", 1.0)]

        results: list[CRSMatch] = []
        first_token = first_words[0] if first_words else (
            first_key.split()[0] if first_key else ""
        )
        if first_token:
            key = f"{last_key},{first_token}"
            for inst in self._dedupe(self._by_last_first_token.get(key, [])):
                results.append(self._build_match(inst, "first_token", 0.8))
            if results:
                return results

        for inst in self._dedupe(self._by_last_name.get(last_key, [])):
            results.append(self._build_match(inst, "last_name_only", 0.5))
        if results:
            return results

        for inst in self._dedupe(self._by_last_head.get(last_key, [])):
            results.append(self._build_match(inst, "last_head_only", 0.45))
        return results

    def _disambiguate(
        self,
        candidates: list[CRSMatch],
        courses: list[str] | None,
        university_id: str | None,
    ) -> CRSMatch | None:
        scored: list[tuple[float, CRSMatch]] = []
        scraped_courses = {_norm_course(c) for c in (courses or []) if c}

        for m in candidates:
            score = m.confidence
            crs_courses = {_norm_course(c) for c in m.courses}
            overlap = scraped_courses & crs_courses
            # Soft prefix overlap: MATH22 vs MATH 22 already normalized;
            # also MATH vs MATH22 via startswith on alpha prefix+digits
            if not overlap and scraped_courses and crs_courses:
                for sc in scraped_courses:
                    for cc in crs_courses:
                        if sc and cc and (sc.startswith(cc) or cc.startswith(sc)):
                            overlap.add(sc)
                            break
            if overlap:
                score += 0.35
            if university_id and m.university_id == university_id.lower():
                score += 0.10
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        # Require a clear winner when we used course boost
        if best_score >= 0.8:
            # Rebuild with upgraded match_type if course helped
            if scraped_courses and best_score > best.confidence:
                return CRSMatch(
                    instructor=best.instructor,
                    courses=best.courses,
                    university_id=best.university_id,
                    match_type=f"{best.match_type}+course",
                    confidence=min(0.95, best_score),
                )
            return best
        if len(scored) >= 2 and best_score - scored[1][0] >= 0.25 and best_score >= 0.7:
            return CRSMatch(
                instructor=best.instructor,
                courses=best.courses,
                university_id=best.university_id,
                match_type=f"{best.match_type}+course",
                confidence=min(0.9, best_score),
            )
        return None

    @staticmethod
    def _dedupe(items: list[CRSInstructor]) -> list[CRSInstructor]:
        seen: set[str] = set()
        out: list[CRSInstructor] = []
        for inst in items:
            if inst.instructor_id in seen:
                continue
            seen.add(inst.instructor_id)
            out.append(inst)
        return out

    def _build_match(
        self, inst: CRSInstructor, match_type: str, confidence: float
    ) -> CRSMatch:
        courses = sorted(set(self._courses.get(inst.instructor_id, [])))
        university = self._university.get(inst.instructor_id, "unknown")
        return CRSMatch(
            instructor=inst,
            courses=courses,
            university_id=university,
            match_type=match_type,
            confidence=confidence,
        )


def match_scraped_professors(
    rupp_db_path: Path,
    crs_db_path: Path | None = None,
    *,
    skip_junk: bool = True,
) -> dict[str, Any]:
    """Match all professors in the ruppscraper DB against CRS instructors."""
    lookup = CRSLookup(crs_db_path)
    if not lookup.is_available:
        return {"error": "CRS database not found", "path": str(lookup._db_path)}

    lookup.load()

    conn = sqlite3.connect(str(rupp_db_path))
    conn.row_factory = sqlite3.Row
    professors = conn.execute("SELECT * FROM professors").fetchall()
    course_rows = conn.execute(
        """
        SELECT professor_id, group_concat(DISTINCT course) AS courses
        FROM posts
        WHERE professor_id IS NOT NULL AND course IS NOT NULL AND course != ''
        GROUP BY professor_id
        """
    ).fetchall()
    conn.close()

    courses_by_prof = {
        r["professor_id"]: [c.strip() for c in (r["courses"] or "").split(",") if c.strip()]
        for r in course_rows
    }

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    junk: list[dict[str, Any]] = []

    campus_to_uni = {
        "UPD": "upd",
        "UPLB": "uplb",
        "UPM": "upm",
        "UPOU": "upou",
        "UPV": "upv",
        "UPMIN": "upmin",
        "UPB": "upb",
        "UPC": "upc",
        "UPT": "upt",
    }

    for prof in professors:
        last_name = prof["last_name"]
        first_name = prof["first_name"]
        campus = prof["campus"]
        prof_info = {
            "id": prof["id"],
            "name": f"{last_name}, {first_name}",
            "campus": campus,
        }

        if skip_junk and not is_plausible_professor_name(last_name, first_name):
            junk.append(prof_info)
            continue

        scraped_courses = courses_by_prof.get(prof["id"], [])
        uni = campus_to_uni.get((campus or "").upper())
        best = lookup.match(
            last_name, first_name, courses=scraped_courses, university_id=uni
        )
        all_matches = lookup.match_all(last_name, first_name)

        if best and best.confidence >= 0.8:
            matched.append(
                {
                    **prof_info,
                    "crs_instructor_id": best.instructor.instructor_id,
                    "crs_name": best.instructor.name_display,
                    "crs_university": best.university_id,
                    "crs_courses": best.courses,
                    "match_type": best.match_type,
                    "confidence": best.confidence,
                    "scraped_courses": scraped_courses,
                }
            )
        elif all_matches:
            ambiguous.append(
                {
                    **prof_info,
                    "candidates": [
                        {
                            "crs_name": m.instructor.name_display,
                            "crs_courses": m.courses,
                            "match_type": m.match_type,
                            "confidence": m.confidence,
                        }
                        for m in all_matches[:8]
                    ],
                    "scraped_courses": scraped_courses,
                }
            )
        else:
            unmatched.append({**prof_info, "scraped_courses": scraped_courses})

    considered = len(professors) - len(junk)
    return {
        "crs_instructor_count": lookup.instructor_count,
        "scraped_professor_count": len(professors),
        "considered_count": considered,
        "junk_count": len(junk),
        "matched": matched,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "junk": junk,
        "match_rate": f"{len(matched)}/{considered}" if considered else "0/0",
    }


def _professor_merge_key(
    last_name: str, first_name: str, campus: str | None
) -> tuple[str, str, str] | None:
    """Group key: (canonical campus, folded last, folded first-name first token)."""
    from scraper.config import canonical_campus

    if not is_plausible_professor_name(last_name, first_name):
        return None
    canon = canonical_campus(campus)
    if not canon:
        return None
    last, first = clean_scraped_name(last_name, first_name)
    tokens = first.split()
    if not last or not tokens:
        return None
    return (canon, fold_key(last), fold_key(tokens[0]))


def _keeper_sort_key(prof_id: Any, campus: str | None, post_count: int) -> tuple:
    """Most linked posts, then stored campus UPD, then shortest id."""
    stored = (campus or "").strip().upper()
    id_str = str(prof_id)
    return (-post_count, 0 if stored == "UPD" else 1, len(id_str), id_str)


def merge_duplicate_professors(rupp_db_path: Path) -> dict[str, int]:
    """Collapse near-duplicate professor rows onto one keeper per identity.

    Groups by (canonical campus, cleaned last name, cleaned first-name first
    token). Keeper is the row with the most linked posts; ties prefer stored
    campus UPD, then the shortest id. Posts pointing at losers are relinked
    onto the keeper, then the duplicate professor rows are deleted.
    """
    conn = sqlite3.connect(str(rupp_db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT p.id, p.last_name, p.first_name, p.campus,
               COUNT(posts.professor_id) AS post_count
        FROM professors p
        LEFT JOIN posts ON posts.professor_id = p.id
        GROUP BY p.id
        """
    ).fetchall()

    groups: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    for row in rows:
        key = _professor_merge_key(row["last_name"], row["first_name"], row["campus"])
        if key is None:
            continue
        groups.setdefault(key, []).append(row)

    groups_merged = 0
    professors_removed = 0
    posts_relinked = 0
    with conn:
        for members in groups.values():
            if len(members) < 2:
                continue
            members_sorted = sorted(
                members,
                key=lambda r: _keeper_sort_key(r["id"], r["campus"], r["post_count"]),
            )
            keeper = members_sorted[0]
            others = [m["id"] for m in members_sorted[1:]]
            placeholders = ",".join("?" * len(others))
            cur = conn.execute(
                f"UPDATE posts SET professor_id = ? WHERE professor_id IN ({placeholders})",
                (keeper["id"], *others),
            )
            posts_relinked += cur.rowcount
            conn.execute(
                f"DELETE FROM professors WHERE id IN ({placeholders})",
                others,
            )
            groups_merged += 1
            professors_removed += len(others)
    conn.close()
    return {
        "duplicate_groups_merged": groups_merged,
        "duplicate_professors_removed": professors_removed,
        "posts_relinked": posts_relinked,
    }


def purge_junk_professors(rupp_db_path: Path) -> dict[str, int]:
    """Detach posts from junk/unknown-campus professor rows and drop orphans."""
    from scraper.config import CAMPUS_LOOKUP

    conn = sqlite3.connect(str(rupp_db_path))
    conn.row_factory = sqlite3.Row
    professors = conn.execute("SELECT * FROM professors").fetchall()
    removed = 0
    detached = 0
    with conn:
        for prof in professors:
            campus_ok = (prof["campus"] or "").upper() in CAMPUS_LOOKUP
            if is_plausible_professor_name(prof["last_name"], prof["first_name"]) and campus_ok:
                continue
            cur = conn.execute(
                "UPDATE posts SET professor_id = NULL WHERE professor_id = ?",
                (prof["id"],),
            )
            detached += cur.rowcount
            conn.execute("DELETE FROM professors WHERE id = ?", (prof["id"],))
            removed += 1
        orphan = conn.execute(
            """
            DELETE FROM professors
            WHERE id NOT IN (
                SELECT DISTINCT professor_id FROM posts
                WHERE professor_id IS NOT NULL
            )
            """
        )
        orphans = orphan.rowcount
    conn.close()
    merge_stats = merge_duplicate_professors(rupp_db_path)
    return {
        "junk_professors_removed": removed,
        "posts_unlinked": detached,
        "orphan_professors_removed": orphans,
        **merge_stats,
    }
