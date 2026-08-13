"""Tests for mention resolve-report (acceptance gate helper)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scraper.crs_matcher import CRSLookup
from scraper.database import get_connection, init_db, upsert_post_with_comments
from scraper.models import Post, Professor
from scraper.resolve_report import (
    CAMPUS_TO_UNIVERSITY,
    build_resolve_report,
    classify_mention,
    load_mentions,
    write_resolve_report,
)


def _add_post(
    conn: sqlite3.Connection,
    *,
    reddit_id: str,
    title: str,
    campus: str,
    course: str,
    professor_id: str,
    last_name: str,
    first_name: str,
    created_utc: float,
) -> None:
    upsert_post_with_comments(
        conn,
        Post(
            reddit_id=reddit_id,
            title=title,
            campus=campus,
            course=course,
            professor_id=professor_id,
            url=f"https://reddit.com/r/RateUPProfs/comments/{reddit_id}",
            score=1,
            num_comments=0,
            created_utc=created_utc,
            author="tester",
            selftext="",
            scraped_at=created_utc,
        ),
        [],
        Professor(
            id=professor_id,
            last_name=last_name,
            first_name=first_name,
            campus=campus,
        ),
    )


def _seed_scraper_db(path: Path) -> None:
    conn = get_connection(path)
    init_db(conn)
    _add_post(
        conn,
        reddit_id="p1",
        title="[UPD] Span 10 - Cruel, Jevic",
        campus="UPD",
        course="Span 10",
        professor_id="upd__cruel__jevic",
        last_name="Cruel",
        first_name="Jevic",
        created_utc=1710000000.0,
    )
    _add_post(
        conn,
        reddit_id="p2",
        title="[UPD] Math 1 - Zzznobody, Fake",
        campus="UPD",
        course="Math 1",
        professor_id="upd__nobody__known",
        last_name="Zzznobody",
        first_name="Fake",
        created_utc=1710000001.0,
    )
    _add_post(
        conn,
        reddit_id="p-junk",
        title="[UPD] Math 22 - Prerogative, 11",
        campus="UPD",
        course="Math 22",
        professor_id="upd__prerogative__11",
        last_name="Prerogative",
        first_name="11",
        created_utc=1710000002.0,
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
            ('i1', 'CRUEL, JEVIC ANJIN', 'Cruel, Jevic Anjin'),
            ('i-uplb', 'SANTOS, JUAN CARLOS', 'Santos, Juan Carlos'),
            ('i-upd', 'SANTOS, ANA MARIA', 'Santos, Ana Maria');
        INSERT INTO course VALUES
            ('c-math', 'MATH 21'),
            ('c-eng', 'ENG 10');
        INSERT INTO section VALUES
            ('s-uplb', 'c-math', 'uplb'),
            ('s-upd', 'c-math', 'upd');
        INSERT INTO section_instructor VALUES
            ('s-uplb', 'i-uplb'),
            ('s-upd', 'i-upd');
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
    assert report["mention_pool"] == 2
    assert report["resolved_count"] == 1
    assert report["unresolved_count"] == 1
    assert report["unresolved"][0]["professor"] == "Zzznobody, Fake"
    assert report["acceptance_threshold"] == 0.80
    professors = {r["professor"] for r in report["resolved"] + report["unresolved"]}
    assert "Prerogative, 11" not in professors

    out = tmp_path / "report.json"
    write_resolve_report(report, out)
    saved = json.loads(out.read_text())
    assert saved["unresolved_count"] == 1
    assert len(saved["unresolved"]) == 1
    assert saved["acceptance_threshold"] == 0.80
    assert "resolved" in saved and "ambiguous" in saved and "unresolved" in saved


def test_load_mentions_skips_implausible_names(tmp_path: Path):
    rupp = tmp_path / "rupp.db"
    _seed_scraper_db(rupp)
    mentions = load_mentions(rupp, campus="UPD")
    names = {(m.last_name, m.first_name) for m in mentions}
    assert ("Prerogative", "11") not in names
    assert ("Cruel", "Jevic") in names
    assert ("Zzznobody", "Fake") in names


def test_classify_mention_skips_implausible_names(tmp_path: Path):
    crs = tmp_path / "crs.db"
    _mini_crs(crs)
    lookup = CRSLookup(crs)
    lookup.load()
    status, detail = classify_mention(lookup, "Prerogative", "11")
    assert status == "skipped"
    assert detail is None


def test_classify_mention_passes_course_and_university_id(tmp_path: Path):
    crs = tmp_path / "crs.db"
    _mini_crs(crs)
    lookup = CRSLookup(crs)
    lookup.load()

    status, detail = classify_mention(lookup, "Santos", "Unknown")
    assert status == "ambiguous"

    status, detail = classify_mention(
        lookup,
        "Santos",
        "Unknown",
        course="MATH 21",
        university_id="upd",
    )
    assert status == "resolved"
    assert detail is not None
    assert detail["crs_instructor_id"] == "i-upd"
    assert "course" in detail["match_type"]

    status, detail = classify_mention(
        lookup,
        "Santos",
        "Unknown",
        course="MATH 21",
        university_id="uplb",
    )
    assert status == "resolved"
    assert detail is not None
    assert detail["crs_instructor_id"] == "i-uplb"


def test_resolve_report_maps_campus_to_university_id(tmp_path: Path):
    assert CAMPUS_TO_UNIVERSITY["UPD"] == "upd"
    assert CAMPUS_TO_UNIVERSITY["UPLB"] == "uplb"

    rupp = tmp_path / "rupp.db"
    crs = tmp_path / "crs.db"
    conn = get_connection(rupp)
    init_db(conn)
    _add_post(
        conn,
        reddit_id="p-lb",
        title="[UPLB] MATH 21 - Santos, Unknown",
        campus="UPLB",
        course="MATH 21",
        professor_id="uplb__santos__unknown",
        last_name="Santos",
        first_name="Unknown",
        created_utc=1710000000.0,
    )
    conn.close()
    _mini_crs(crs)

    report = build_resolve_report(
        sample_size=100,
        campus="UPLB",
        rupp_db_path=rupp,
        crs_db_path=crs,
        strategy="recent",
    )
    assert report["sample_size"] == 1
    assert report["resolved_count"] == 1
    assert report["resolved"][0]["crs_instructor_id"] == "i-uplb"
    assert report["acceptance_threshold"] == 0.80
