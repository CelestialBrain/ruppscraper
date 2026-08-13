"""Tests for professor-review vs junk-text triage."""

from __future__ import annotations

from pathlib import Path

from scraper.database import get_connection, init_db, upsert_comment, upsert_post
from scraper.models import Comment, Post
from scraper.review_triage import apply_review_triage, classify_review_text


class TestClassifyReviewText:
    def test_keeps_real_reviews(self):
        keep, reason = classify_review_text("mabait at unoable nagaadjust sa deadline")
        assert keep and reason == ""
        keep, _ = classify_review_text(
            "Very responsive din, mapa-email o gclass. Reqs for this sem: 3 essays."
        )
        assert keep
        keep, _ = classify_review_text(
            "BASTA PLS DONT WAITLIST OR DO PREROG FOR SIR DENNIS — don't take him"
        )
        assert keep
        keep, _ = classify_review_text("vouch")
        assert keep
        keep, _ = classify_review_text("1.25!!")
        assert keep
        keep, _ = classify_review_text("sir Gavin goated")
        assert keep
        keep, _ = classify_review_text(
            "Took him from 1st sem pa and ganyang katagal si sir na magbigay ng grade."
        )
        assert keep

    def test_drops_junk(self):
        assert classify_review_text("up")[0] is False
        assert classify_review_text("+1")[0] is False
        assert classify_review_text("lf classmates huhu")[0] is False
        assert classify_review_text("email pls")[0] is False
        assert classify_review_text("how can i prerog po?")[0] is False
        assert classify_review_text("PA 101")[0] is False
        assert classify_review_text("[deleted]")[0] is False
        assert classify_review_text("Has anyone taken this class? How is he?")[0] is False
        assert classify_review_text("may i also ask for their emails, please?")[0] is False
        assert classify_review_text("i saw it na, thank u sm!!")[0] is False
        assert classify_review_text("Hi! Unoable po ba?")[0] is False
        assert classify_review_text("is he unoable")[0] is False
        assert classify_review_text(
            "Thoughts po sa kanila? Workload? Pedagogy? Unoable? "
            "Marami akong majors this sem so goods sana kung di ganun kabigat.",
            is_post_body=True,
        )[0] is False


def test_apply_triage_flags_rows(tmp_path: Path):
    db = tmp_path / "t.db"
    conn = get_connection(db)
    init_db(conn)
    upsert_post(
        conn,
        Post(
            reddit_id="p1",
            title="[UPD] Math 22 - Neri, Marrick",
            campus="UPD",
            course="Math 22",
            professor_id=None,
            url="https://reddit.com/r/RateUPProfs/comments/p1/",
            score=1,
            num_comments=2,
            created_utc=1.0,
            author="a",
            selftext="Any comments about this prof?",
        ),
    )
    upsert_comment(
        conn,
        Comment(
            reddit_id="c1",
            post_reddit_id="p1",
            parent_id="t3_p1",
            author="x",
            body="super vouch, unoable and chill lectures",
            score=1,
            created_utc=2.0,
            depth=0,
        ),
    )
    upsert_comment(
        conn,
        Comment(
            reddit_id="c2",
            post_reddit_id="p1",
            parent_id="t3_p1",
            author="y",
            body="email pls",
            score=1,
            created_utc=3.0,
            depth=0,
        ),
    )
    conn.commit()
    conn.close()

    result = apply_review_triage(db)
    assert result["comment_kept"] == 1
    assert result["comment_dropped"] == 1
    assert result["post_dropped"] == 1

    conn = get_connection(db)
    flags = {
        r["reddit_id"]: r["is_review"]
        for r in conn.execute("SELECT reddit_id, is_review FROM comments")
    }
    assert flags["c1"] == 1
    assert flags["c2"] == 0
    conn.close()
