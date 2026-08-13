"""Unit tests for CRS name matching helpers (no live CRS db required)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scraper.crs_matcher import (
    CRSLookup,
    match_scraped_professors,
    merge_duplicate_professors,
    purge_junk_professors,
)
from scraper.name_resolver import fold_key


@pytest.fixture
def crs_db(tmp_path: Path) -> Path:
    path = tmp_path / "crs.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE instructor (
            instructor_id TEXT PRIMARY KEY,
            name_normalized TEXT NOT NULL,
            name_display TEXT NOT NULL
        );
        CREATE TABLE course (
            course_id TEXT PRIMARY KEY,
            course_code TEXT NOT NULL
        );
        CREATE TABLE section (
            section_id TEXT PRIMARY KEY,
            course_id TEXT NOT NULL,
            university_id TEXT NOT NULL
        );
        CREATE TABLE section_instructor (
            section_id TEXT NOT NULL,
            instructor_id TEXT NOT NULL
        );
        INSERT INTO instructor VALUES
            ('i1', 'PADERES, MONISSA', 'Paderes, Monissa'),
            ('i2', 'CAUYAN, JACLYN MARIE', 'Cauyan, Jaclyn Marie'),
            ('i3', 'OROZCO, ZENITH GAYE', 'Orozco, Zenith Gaye'),
            ('i4', 'CASTAÑEDA, ROANN KRISTIAN', 'Castañeda, Roann Kristian'),
            ('i5', 'INAZUNTA MARCA, CATALINA', 'Inazunta Marca, Catalina'),
            ('i6', 'CRUEL, JEVIC ANJIN', 'Cruel, Jevic Anjin'),
            ('i7', 'CONCEPCION, MARY GRACE', 'Concepcion, Mary Grace'),
            ('i8', 'SANTOS, ANA MARIA', 'Santos, Ana Maria'),
            ('i9', 'SANTOS, JUAN CARLOS', 'Santos, Juan Carlos');
        INSERT INTO course VALUES
            ('c1', 'CHEM 31'),
            ('c2', 'MATH 21'),
            ('c3', 'ENG 10');
        INSERT INTO section VALUES
            ('s1', 'c1', 'upd'),
            ('s2', 'c2', 'upd'),
            ('s3', 'c3', 'upd');
        INSERT INTO section_instructor VALUES
            ('s1', 'i1'),
            ('s2', 'i8'),
            ('s3', 'i9');
        """
    )
    conn.commit()
    conn.close()
    return path


class TestAccentFold:
    def test_fold(self):
        assert fold_key("CASTAÑEDA") == "CASTANEDA"
        assert fold_key("Castañeda") == "CASTANEDA"


class TestCRSLookup:
    def test_exact(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        m = lookup.match("Cauyan", "Jaclyn Marie")
        assert m is not None
        assert m.confidence == 1.0

    def test_reversed_name(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        m = lookup.match("Monissa", "Paderes")
        assert m is not None
        assert m.confidence >= 0.8
        assert "Paderes" in m.instructor.name_display

    def test_accent_fold_match(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        m = lookup.match("Castaneda", "Roann Kristian")
        assert m is not None
        assert m.confidence == 1.0

    def test_hyphen_head(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        m = lookup.match("Orozco-Bautista", "Zenith")
        assert m is not None
        assert m.confidence >= 0.8

    def test_multiword_surname_head(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        m = lookup.match("Inazunta", "Catalina")
        assert m is not None
        assert m.confidence >= 0.8

    def test_first_token(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        m = lookup.match("Cruel", "Jevic")
        assert m is not None
        assert m.match_type == "first_token"

    def test_section_prefix_cleaned(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        m = lookup.match("1 Cauyan", "Jaclyn Marie")
        assert m is not None
        assert m.confidence == 1.0

    def test_course_disambiguates_last_name(self, crs_db: Path):
        lookup = CRSLookup(crs_db)
        lookup.load()
        # Last name only → ambiguous Santos pair
        assert lookup.match("Santos", "") is None
        m = lookup.match("Santos", "", courses=["MATH 21"])
        assert m is not None
        assert m.instructor.instructor_id == "i8"
        assert "course" in m.match_type


class TestMatchScraped:
    def test_skips_junk_and_matches(self, crs_db: Path, tmp_path: Path):
        rupp = tmp_path / "rupp.db"
        conn = sqlite3.connect(rupp)
        conn.executescript(
            """
            CREATE TABLE professors (
                id INTEGER PRIMARY KEY,
                last_name TEXT,
                first_name TEXT,
                campus TEXT
            );
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY,
                professor_id INTEGER,
                course TEXT
            );
            INSERT INTO professors VALUES
                (1, 'Cauyan', 'Jaclyn Marie', 'UPD'),
                (2, 'Prerogative', '11', 'UPD'),
                (3, 'Santos', 'Ana', 'UPD');
            INSERT INTO posts VALUES
                (1, 1, 'CHEM 31'),
                (2, 2, 'MATH 21'),
                (3, 3, 'MATH 21');
            """
        )
        conn.commit()
        conn.close()

        results = match_scraped_professors(rupp, crs_db)
        assert results["junk_count"] == 1
        assert results["match_rate"] == "2/2"
        names = {m["crs_name"] for m in results["matched"]}
        assert "Cauyan, Jaclyn Marie" in names
        assert "Santos, Ana Maria" in names

    def test_purge_junk(self, tmp_path: Path):
        rupp = tmp_path / "rupp.db"
        conn = sqlite3.connect(rupp)
        conn.executescript(
            """
            CREATE TABLE professors (
                id INTEGER PRIMARY KEY,
                last_name TEXT,
                first_name TEXT,
                campus TEXT
            );
            CREATE TABLE posts (
                id INTEGER PRIMARY KEY,
                professor_id INTEGER,
                course TEXT
            );
            INSERT INTO professors VALUES
                (1, 'Garcia', 'Mark', 'UPD'),
                (2, 'Prerogative', '11', 'UPD');
            INSERT INTO posts VALUES (1, 1, 'MATH 21'), (2, 2, 'ENG 10');
            """
        )
        conn.commit()
        conn.close()

        result = purge_junk_professors(rupp)
        assert result["junk_professors_removed"] == 1
        conn = sqlite3.connect(rupp)
        assert conn.execute("SELECT COUNT(*) FROM professors").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT professor_id FROM posts WHERE id = 2"
            ).fetchone()[0]
            is None
        )
        conn.close()


def _rupp_db(tmp_path: Path, professors: list[tuple], posts: list[tuple]) -> Path:
    path = tmp_path / "rupp.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE professors (
            id TEXT PRIMARY KEY,
            last_name TEXT,
            first_name TEXT,
            campus TEXT
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY,
            professor_id TEXT,
            course TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO professors VALUES (?, ?, ?, ?)", professors
    )
    conn.executemany("INSERT INTO posts VALUES (?, ?, ?)", posts)
    conn.commit()
    conn.close()
    return path


class TestMergeDuplicateProfessors:
    def test_keeps_row_with_most_posts(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("upd__garcia__mark", "Garcia", "Mark", "UPD"),
                ("upd__garcia__mark_lester", "Garcia", "Mark Lester", "UPD"),
            ],
            [
                (1, "upd__garcia__mark", "MATH 21"),
                (2, "upd__garcia__mark_lester", "MATH 21"),
                (3, "upd__garcia__mark_lester", "MATH 22"),
                (4, "upd__garcia__mark_lester", "MATH 23"),
            ],
        )
        result = merge_duplicate_professors(rupp)
        assert result["duplicate_groups_merged"] == 1
        assert result["duplicate_professors_removed"] == 1
        assert result["posts_relinked"] == 1
        conn = sqlite3.connect(rupp)
        ids = {r[0] for r in conn.execute("SELECT id FROM professors")}
        assert ids == {"upd__garcia__mark_lester"}
        linked = {
            r[0]
            for r in conn.execute("SELECT professor_id FROM posts")
        }
        assert linked == {"upd__garcia__mark_lester"}
        conn.close()

    def test_tie_prefers_stored_upd_campus(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("g", "Garcia", "Mark", "Diliman"),
                ("upd__garcia__mark_long", "Garcia", "Mark", "UPD"),
            ],
            [
                (1, "g", "MATH 21"),
                (2, "upd__garcia__mark_long", "MATH 22"),
            ],
        )
        result = merge_duplicate_professors(rupp)
        assert result["duplicate_groups_merged"] == 1
        assert result["duplicate_professors_removed"] == 1
        conn = sqlite3.connect(rupp)
        remaining = conn.execute("SELECT id FROM professors").fetchone()[0]
        assert remaining == "upd__garcia__mark_long"
        assert (
            conn.execute("SELECT COUNT(*) FROM posts WHERE professor_id = ?",
                         ("upd__garcia__mark_long",)).fetchone()[0]
            == 2
        )
        conn.close()

    def test_tie_prefers_shortest_id(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("upd__garcia__mark_lester", "Garcia", "Mark Lester", "UPD"),
                ("upd__garcia__mark", "Garcia", "Mark", "UPD"),
            ],
            [
                (1, "upd__garcia__mark_lester", "MATH 21"),
                (2, "upd__garcia__mark", "MATH 22"),
            ],
        )
        result = merge_duplicate_professors(rupp)
        assert result["duplicate_groups_merged"] == 1
        conn = sqlite3.connect(rupp)
        remaining = conn.execute("SELECT id FROM professors").fetchone()[0]
        assert remaining == "upd__garcia__mark"
        conn.close()

    def test_does_not_merge_across_campuses(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("upd__garcia__mark", "Garcia", "Mark", "UPD"),
                ("uplb__garcia__mark", "Garcia", "Mark", "UPLB"),
            ],
            [
                (1, "upd__garcia__mark", "MATH 21"),
                (2, "uplb__garcia__mark", "MATH 21"),
            ],
        )
        result = merge_duplicate_professors(rupp)
        assert result["duplicate_groups_merged"] == 0
        assert result["duplicate_professors_removed"] == 0
        conn = sqlite3.connect(rupp)
        assert conn.execute("SELECT COUNT(*) FROM professors").fetchone()[0] == 2
        conn.close()

    def test_does_not_merge_different_first_tokens(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("upd__santos__ana", "Santos", "Ana", "UPD"),
                ("upd__santos__juan", "Santos", "Juan", "UPD"),
            ],
            [
                (1, "upd__santos__ana", "ENG 10"),
                (2, "upd__santos__juan", "ENG 10"),
            ],
        )
        result = merge_duplicate_professors(rupp)
        assert result["duplicate_groups_merged"] == 0
        conn = sqlite3.connect(rupp)
        assert conn.execute("SELECT COUNT(*) FROM professors").fetchone()[0] == 2
        conn.close()

    def test_accent_fold_and_section_prefix_group(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("upd__castaneda__roann", "Castañeda", "Roann", "UPD"),
                ("upd__castaneda__roann_k", "1 Castaneda", "Roann Kristian", "UPD"),
            ],
            [
                (1, "upd__castaneda__roann", "CHEM 31"),
                (2, "upd__castaneda__roann_k", "CHEM 31"),
            ],
        )
        result = merge_duplicate_professors(rupp)
        assert result["duplicate_groups_merged"] == 1
        assert result["duplicate_professors_removed"] == 1
        conn = sqlite3.connect(rupp)
        assert conn.execute("SELECT COUNT(*) FROM professors").fetchone()[0] == 1
        conn.close()

    def test_skips_implausible_names(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("upd__math__john", "Math", "John", "UPD"),
                ("upd__math__john_paul", "Math", "John Paul", "UPD"),
            ],
            [
                (1, "upd__math__john", "MATH 21"),
                (2, "upd__math__john_paul", "MATH 21"),
            ],
        )
        result = merge_duplicate_professors(rupp)
        assert result["duplicate_groups_merged"] == 0
        conn = sqlite3.connect(rupp)
        assert conn.execute("SELECT COUNT(*) FROM professors").fetchone()[0] == 2
        conn.close()

    def test_purge_junk_merges_after_junk_delete(self, tmp_path: Path):
        rupp = _rupp_db(
            tmp_path,
            [
                ("upd__garcia__mark", "Garcia", "Mark", "UPD"),
                ("upd__garcia__mark_lester", "Garcia", "Mark Lester", "UPD"),
                ("upd__prerogative__11", "Prerogative", "11", "UPD"),
            ],
            [
                (1, "upd__garcia__mark", "MATH 21"),
                (2, "upd__garcia__mark_lester", "MATH 22"),
                (3, "upd__garcia__mark_lester", "MATH 23"),
                (4, "upd__prerogative__11", "ENG 10"),
            ],
        )
        result = purge_junk_professors(rupp)
        assert result["junk_professors_removed"] == 1
        assert result["duplicate_groups_merged"] == 1
        assert result["duplicate_professors_removed"] == 1
        conn = sqlite3.connect(rupp)
        ids = {r[0] for r in conn.execute("SELECT id FROM professors")}
        assert ids == {"upd__garcia__mark_lester"}
        assert (
            conn.execute(
                "SELECT professor_id FROM posts WHERE id = 4"
            ).fetchone()[0]
            is None
        )
        conn.close()
