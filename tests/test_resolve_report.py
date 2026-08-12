"""Tests for mention resolve-report (acceptance gate helper)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scraper.database import get_connection, init_db, upsert_post_with_comments
from scraper.models import Post, Professor
from scraper.resolve_report import build_resolve_report, write_resolve_report


def _seed_scraper_db(path: Path) -> None:
    conn = get_connection(path)
    init_db(conn)
    upsert_post_with_comments(
        conn,
        Post(
            reddit_id="p1",
            title="[UPD] Span 10 - Cruel, Jevic",
            campus="UPD",
            course="Span 10",
            professor_id="upd__cruel__jevic",
            url="https://reddit.com/r/RateUPProfs/comments/p1",
            score=1,
            num_comments=0,
            created_utc=1710000000.0,
            author="tester",
            selftext="",
            scraped_at=1710000000.0,
        ),
        [],
        Professor(id="upd__cruel__jevic", last_name="Cruel", first_name="Jevic", campus="UPD"),
    )
    upsert_post_with_comments(
        conn,
        Post(
            reddit_id="p2",
            title="[UPD] Math 1 - Zzznobody, Fake",
            campus="UPD",
            course="Math 1",
            professor_id="upd__nobody__known",
            url="https://reddit.com/r/RateUPProfs/comments/p2",
            score=1,
            num_comments=0,
            created_utc=1710000001.0,
            author="tester",
            selftext="",
            scraped_at=1710000001.0,
        ),
        [],
        Professor(
            id="upd__nobody__known",
            last_name="Zzznobody",
            first_name="Fake",
            campus="UPD",
        ),
    )
    conn.close()


def _mini_crs(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE instructor (
            instructor_id TEXT PRIMARY KEY,
            name_normalized TEXT NOT NULL,
            name_display TEXT NOT NULL
        );
        CREATE TABLE course (course_id TEXT PRIMARY KEY, course_code TEXT NOT NULL);
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
            ('i1', 'CRUEL, JEVIC ANJIN', 'Cruel, Jevic Anjin');
        """
    )
    conn.commit()
    conn.close()


def test_resolve_report_lists_unresolved(tmp_path: Path):
    rupp = tmp_path / "rupp.db"
    crs = tmp_path / "crs.db"
    _seed_scraper_db(rupp)
    _mini_crs(crs)

    report = build_resolve_report(
        sample_size=100,
        rupp_db_path=rupp,
        crs_db_path=crs,
        strategy="recent",
    )
    assert report["sample_size"] == 2
    assert report["resolved_count"] == 1
    assert report["unresolved_count"] == 1
    assert report["unresolved"][0]["professor"] == "Zzznobody, Fake"

    out = tmp_path / "report.json"
    write_resolve_report(report, out)
    saved = json.loads(out.read_text())
    assert saved["unresolved_count"] == 1
    assert len(saved["unresolved"]) == 1
