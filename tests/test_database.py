"""Database helper tests (temp SQLite file)."""

from __future__ import annotations

from pathlib import Path

from scraper.database import (
    get_connection,
    get_posts_missing_comments,
    get_unparsed_posts,
    init_db,
    update_post_parse,
    upsert_post_with_comments,
    upsert_professor,
)
from scraper.models import Comment, Post, Professor


def _seed(db_path: Path) -> None:
    conn = get_connection(db_path)
    init_db(conn)
    prof = Professor(
        id="upd__neri__marrick",
        last_name="Neri",
        first_name="Marrick",
        campus="UPD",
    )
    upsert_professor(conn, prof)
    parsed = Post(
        reddit_id="p1",
        title="[UPD] Math 22 - Neri, Marrick",
        campus="UPD",
        course="Math 22",
        professor_id=prof.id,
        url="https://reddit.com/r/RateUPProfs/comments/p1/",
        score=1,
        num_comments=2,
        created_utc=1700000000,
        author="a",
        selftext="",
    )
    unparsed = Post(
        reddit_id="p2",
        title="[UPD] Math 22 - Arvin Lamando",
        campus=None,
        course=None,
        professor_id=None,
        url="https://reddit.com/r/RateUPProfs/comments/p2/",
        score=1,
        num_comments=1,
        created_utc=1700000001,
        author="b",
        selftext="",
    )
    upsert_post_with_comments(conn, parsed, [], prof)
    upsert_post_with_comments(conn, unparsed, [])
    conn.close()


def test_unparsed_and_missing_comments(tmp_path: Path):
    db = tmp_path / "t.db"
    _seed(db)
    conn = get_connection(db)
    unparsed = get_unparsed_posts(conn)
    assert len(unparsed) == 1
    assert unparsed[0]["reddit_id"] == "p2"

    missing = get_posts_missing_comments(conn)
    assert {r["reddit_id"] for r in missing} == {"p1", "p2"}

    upsert_professor(
        conn,
        Professor(
            id="upd__lamando__arvin",
            last_name="Lamando",
            first_name="Arvin",
            campus="UPD",
        ),
    )
    update_post_parse(conn, "p2", "UPD", "Math 22", "upd__lamando__arvin")
    conn.commit()
    assert get_unparsed_posts(conn) == []

    # One stored comment vs claimed 2 → still underfilled
    upsert_post_with_comments(
        conn,
        Post(
            reddit_id="p1",
            title="[UPD] Math 22 - Neri, Marrick",
            campus="UPD",
            course="Math 22",
            professor_id="upd__neri__marrick",
            url="https://reddit.com/r/RateUPProfs/comments/p1/",
            score=1,
            num_comments=2,
            created_utc=1700000000,
            author="a",
            selftext="",
        ),
        [
            Comment(
                reddit_id="c1",
                post_reddit_id="p1",
                parent_id="t3_p1",
                author="x",
                body="unoable",
                score=1,
                created_utc=1700000002,
                depth=0,
            )
        ],
        Professor(
            id="upd__neri__marrick",
            last_name="Neri",
            first_name="Marrick",
            campus="UPD",
        ),
    )
    missing2 = get_posts_missing_comments(conn)
    assert {r["reddit_id"] for r in missing2} == {"p1", "p2"}

    # Fill the claimed gap on p1 — it drops out, p2 (zero comments) remains
    upsert_post_with_comments(
        conn,
        Post(
            reddit_id="p1",
            title="[UPD] Math 22 - Neri, Marrick",
            campus="UPD",
            course="Math 22",
            professor_id="upd__neri__marrick",
            url="https://reddit.com/r/RateUPProfs/comments/p1/",
            score=1,
            num_comments=2,
            created_utc=1700000000,
            author="a",
            selftext="",
        ),
        [
            Comment(
                reddit_id="c1",
                post_reddit_id="p1",
                parent_id="t3_p1",
                author="x",
                body="unoable",
                score=1,
                created_utc=1700000002,
                depth=0,
            ),
            Comment(
                reddit_id="c2",
                post_reddit_id="p1",
                parent_id="t3_p1",
                author="y",
                body="take him",
                score=1,
                created_utc=1700000003,
                depth=0,
            ),
        ],
        Professor(
            id="upd__neri__marrick",
            last_name="Neri",
            first_name="Marrick",
            campus="UPD",
        ),
    )
    missing3 = get_posts_missing_comments(conn)
    assert [r["reddit_id"] for r in missing3] == ["p2"]

    # A thread that claims zero comments is not a refill target.
    upsert_post_with_comments(
        conn,
        Post(
            reddit_id="p3",
            title="meta",
            campus=None,
            course=None,
            professor_id=None,
            url="https://reddit.com/r/RateUPProfs/comments/p3/",
            score=0,
            num_comments=0,
            created_utc=1700000004,
            author="c",
            selftext="",
        ),
        [],
    )
    assert [r["reddit_id"] for r in get_posts_missing_comments(conn)] == ["p2"]
    conn.close()
