"""Unit tests for the JSON exporter."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from scraper.database import (
    get_connection,
    init_db,
    upsert_comment,
    upsert_post,
    upsert_professor,
)
from scraper.exporter import export_full, export_professors
from scraper.models import Comment, Post, Professor


@pytest.fixture
def db_with_data(tmp_path: Path) -> Path:
    """Create a temporary database populated with test data."""
    db_path = tmp_path / "test.db"
    conn = get_connection(db_path)
    init_db(conn)

    # Insert a professor
    prof = Professor(
        id="upd__redelicia__romeo_joshua",
        last_name="Redelicia",
        first_name="Romeo Joshua",
        campus="UPD",
    )
    upsert_professor(conn, prof)

    # Insert a post
    post = Post(
        reddit_id="abc123",
        title="[UPD] Speech 30 - REDELICIA, ROMEO JOSHUA",
        campus="UPD",
        course="Speech 30",
        professor_id="upd__redelicia__romeo_joshua",
        url="https://reddit.com/r/RateUPProfs/comments/abc123",
        score=42,
        num_comments=3,
        created_utc=1710489000.0,
        author="student123",
        selftext="Has anyone taken this class?",
        scraped_at=1710500000.0,
    )
    upsert_post(conn, post)

    # Insert comments
    comments = [
        Comment(
            reddit_id="c1",
            post_reddit_id="abc123",
            parent_id="t3_abc123",
            author="reviewer1",
            body="activities are engaging...",
            score=15,
            created_utc=1710492600.0,
            depth=0,
            scraped_at=1710500000.0,
        ),
        Comment(
            reddit_id="c2",
            post_reddit_id="abc123",
            parent_id="t3_abc123",
            author="reviewer2",
            body="fair in grading...",
            score=8,
            created_utc=1710496200.0,
            depth=0,
            scraped_at=1710500000.0,
        ),
        Comment(
            reddit_id="c3",
            post_reddit_id="abc123",
            parent_id="t1_c1",
            author="reviewer3",
            body="I agree, very engaging!",
            score=3,
            created_utc=1710499800.0,
            depth=1,
            scraped_at=1710500000.0,
        ),
    ]
    for c in comments:
        upsert_comment(conn, c)

    # Insert a second post (unparsed / no professor)
    post2 = Post(
        reddit_id="def456",
        title="Rate my enlistment lineup",
        campus=None,
        course=None,
        professor_id=None,
        url="https://reddit.com/r/RateUPProfs/comments/def456",
        score=5,
        num_comments=0,
        created_utc=1710400000.0,
        author="newbie",
        selftext="Here's my lineup...",
        scraped_at=1710500000.0,
    )
    upsert_post(conn, post2)

    conn.commit()
    conn.close()
    return db_path


class TestExportFull:
    """Tests for the full export format."""

    def test_exports_all_posts(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "full.json"
        count = export_full(output, db_path=db_with_data)
        assert count == 2

        data = json.loads(output.read_text())
        assert len(data) == 2

    def test_post_structure(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "full.json"
        export_full(output, db_path=db_with_data)
        data = json.loads(output.read_text())

        # Find the parsed post
        parsed_post = next(d for d in data if d["campus"] == "UPD")

        assert parsed_post["campus"] == "UPD"
        assert parsed_post["course"] == "Speech 30"
        assert parsed_post["professor"]["last_name"] == "Redelicia"
        assert parsed_post["professor"]["first_name"] == "Romeo Joshua"
        assert parsed_post["post"]["reddit_id"] == "abc123"
        assert parsed_post["post"]["score"] == 42

    def test_comments_included(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "full.json"
        export_full(output, db_path=db_with_data)
        data = json.loads(output.read_text())

        parsed_post = next(d for d in data if d["campus"] == "UPD")
        assert len(parsed_post["comments"]) == 3

        # Check comment fields
        c1 = next(c for c in parsed_post["comments"] if c["reddit_id"] == "c1")
        assert c1["author"] == "reviewer1"
        assert c1["body"] == "activities are engaging..."
        assert c1["depth"] == 0

    def test_unparsed_post_has_null_professor(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "full.json"
        export_full(output, db_path=db_with_data)
        data = json.loads(output.read_text())

        unparsed = next(d for d in data if d["campus"] is None)
        assert unparsed["professor"] is None
        assert unparsed["course"] is None

    def test_timestamps_are_iso(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "full.json"
        export_full(output, db_path=db_with_data)
        data = json.loads(output.read_text())

        parsed_post = next(d for d in data if d["campus"] == "UPD")
        posted_at = parsed_post["post"]["posted_at"]
        assert "T" in posted_at  # ISO-8601 format
        assert "+" in posted_at or "Z" in posted_at  # timezone-aware


class TestExportProfessors:
    """Tests for the professor-grouped export format."""

    def test_exports_professors(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "profs.json"
        count = export_professors(output, db_path=db_with_data)
        assert count == 1  # Only one professor in test data

    def test_professor_structure(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "profs.json"
        export_professors(output, db_path=db_with_data)
        data = json.loads(output.read_text())

        prof = data[0]
        assert prof["professor"] == "Redelicia, Romeo Joshua"
        assert prof["campus"] == "UPD"
        assert "Speech 30" in prof["courses"]
        assert prof["total_discussions"] == 1

    def test_discussion_details(self, db_with_data: Path, tmp_path: Path):
        output = tmp_path / "profs.json"
        export_professors(output, db_path=db_with_data)
        data = json.loads(output.read_text())

        discussion = data[0]["discussions"][0]
        assert discussion["course"] == "Speech 30"
        assert discussion["score"] == 42
        assert discussion["comment_count"] == 3

    def test_empty_database(self, tmp_path: Path):
        db_path = tmp_path / "empty.db"
        output = tmp_path / "empty.json"
        count = export_professors(output, db_path=db_path)
        assert count == 0

        data = json.loads(output.read_text())
        assert data == []
