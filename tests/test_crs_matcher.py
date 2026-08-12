"""Unit tests for CRS name matching helpers (no live CRS db required)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scraper.crs_matcher import CRSLookup, _fold_accents, _norm_key


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
            ('i7', 'CONCEPCION, MARY GRACE', 'Concepcion, Mary Grace');
        INSERT INTO course VALUES ('c1', 'CHEM 31');
        INSERT INTO section VALUES ('s1', 'c1', 'upd');
        INSERT INTO section_instructor VALUES ('s1', 'i1');
        """
    )
    conn.commit()
    conn.close()
    return path


class TestAccentFold:
    def test_fold(self):
        assert _fold_accents("CASTAÑEDA") == "CASTANEDA"
        assert _norm_key("Castañeda, Roann") == "CASTANEDA, ROANN"


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
