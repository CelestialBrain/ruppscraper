"""Unit tests for Reddit client helpers (no live network)."""

from __future__ import annotations

from scraper.reddit_client import _post_from_raw, wrap_arctic_as_listing


def test_post_from_raw_builds_permalink_and_parse():
    post, parsed = _post_from_raw(
        {
            "id": "abc123",
            "title": "[UPD] Math 22 - Neri, Marrick",
            "permalink": "/r/RateUPProfs/comments/abc123/math_22/",
            "score": 3,
            "num_comments": 2,
            "created_utc": 1700000000,
            "author": "student1",
            "selftext": "how is he?",
        },
        scraped_at=1700000100,
    )
    assert post.reddit_id == "abc123"
    assert post.url.startswith("https://reddit.com/r/RateUPProfs/")
    assert post.score == 3
    assert parsed is not None
    assert parsed.last_name == "Neri"
    assert post.professor_id == parsed.professor_id


def test_post_from_raw_unparsed_meta_title():
    post, parsed = _post_from_raw(
        {
            "id": "zzz",
            "title": "[UPD] Department of European Languages Room Assignment",
            "permalink": "/r/RateUPProfs/comments/zzz/room/",
            "score": 1,
            "num_comments": 0,
            "created_utc": 1700000000,
        },
        scraped_at=1700000100,
    )
    assert parsed is None
    assert post.campus is None
    assert post.professor_id is None


def test_wrap_arctic_as_listing_shape():
    listing = wrap_arctic_as_listing(
        [
            {"id": "abc123", "title": "[UPD] Math 22 - Neri, Marrick"},
            {"id": "def456", "title": "meta post"},
        ]
    )
    children = listing["data"]["children"]
    assert len(children) == 2
    assert children[0]["kind"] == "t3"
    assert children[0]["data"]["id"] == "abc123"
