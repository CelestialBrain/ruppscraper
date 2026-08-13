"""SQLite database layer for the RateUPProfs scraper.

All writes use INSERT OR REPLACE (upsert) keyed on Reddit IDs so re-runs
are safe and idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from scraper.config import DB_PATH
from scraper.models import Comment, Post, Professor

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS professors (
    id          TEXT PRIMARY KEY,
    last_name   TEXT NOT NULL,
    first_name  TEXT NOT NULL,
    campus      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    reddit_id     TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    campus        TEXT,
    course        TEXT,
    professor_id  TEXT,
    url           TEXT NOT NULL,
    score         INTEGER NOT NULL DEFAULT 0,
    num_comments  INTEGER NOT NULL DEFAULT 0,
    created_utc   REAL NOT NULL,
    author        TEXT,
    selftext      TEXT NOT NULL DEFAULT '',
    scraped_at    REAL NOT NULL,
    FOREIGN KEY (professor_id) REFERENCES professors(id)
);

CREATE TABLE IF NOT EXISTS comments (
    reddit_id        TEXT PRIMARY KEY,
    post_reddit_id   TEXT NOT NULL,
    parent_id        TEXT NOT NULL,
    author           TEXT,
    body             TEXT NOT NULL,
    score            INTEGER NOT NULL DEFAULT 0,
    created_utc      REAL NOT NULL,
    depth            INTEGER NOT NULL DEFAULT 0,
    scraped_at       REAL NOT NULL,
    FOREIGN KEY (post_reddit_id) REFERENCES posts(reddit_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_campus ON posts(campus);
CREATE INDEX IF NOT EXISTS idx_posts_professor ON posts(professor_id);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_reddit_id);
"""


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open (or create) the SQLite database and return a connection."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create tables and indexes if they don't exist."""
    conn.executescript(_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------


def upsert_professor(conn: sqlite3.Connection, prof: Professor) -> None:
    """Insert a professor, ignoring if already exists."""
    conn.execute(
        """
        INSERT OR IGNORE INTO professors (id, last_name, first_name, campus)
        VALUES (?, ?, ?, ?)
        """,
        (prof.id, prof.last_name, prof.first_name, prof.campus),
    )


def upsert_post(conn: sqlite3.Connection, post: Post) -> None:
    """Insert or replace a post (keyed on reddit_id)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO posts
            (reddit_id, title, campus, course, professor_id, url,
             score, num_comments, created_utc, author, selftext, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post.reddit_id,
            post.title,
            post.campus,
            post.course,
            post.professor_id,
            post.url,
            post.score,
            post.num_comments,
            post.created_utc,
            post.author,
            post.selftext,
            post.scraped_at,
        ),
    )


def upsert_comment(conn: sqlite3.Connection, comment: Comment) -> None:
    """Insert or replace a comment (keyed on reddit_id)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO comments
            (reddit_id, post_reddit_id, parent_id, author, body,
             score, created_utc, depth, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            comment.reddit_id,
            comment.post_reddit_id,
            comment.parent_id,
            comment.author,
            comment.body,
            comment.score,
            comment.created_utc,
            comment.depth,
            comment.scraped_at,
        ),
    )


def upsert_post_with_comments(
    conn: sqlite3.Connection,
    post: Post,
    comments: list[Comment],
    professor: Professor | None = None,
) -> None:
    """Atomically upsert a post, its professor, and all its comments."""
    with conn:
        if professor is not None:
            upsert_professor(conn, professor)
        upsert_post(conn, post)
        for comment in comments:
            upsert_comment(conn, comment)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def get_scraped_post_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of Reddit post IDs already in the database."""
    cursor = conn.execute("SELECT reddit_id FROM posts")
    return {row["reddit_id"] for row in cursor}


def get_unparsed_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return posts that have no professor_id (title parse failed or never tried)."""
    return list(
        conn.execute(
            """
            SELECT reddit_id, title, url, score, num_comments, created_utc,
                   author, selftext, scraped_at, campus, course, professor_id
            FROM posts
            WHERE professor_id IS NULL
            ORDER BY created_utc DESC
            """
        )
    )


def get_posts_missing_comments(
    conn: sqlite3.Connection,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """Return posts with no stored comments, or fewer than Reddit claimed.

    Prefers the largest remaining gap so truncated Arctic pages get refilled.
    """
    sql = """
        SELECT p.reddit_id, p.title, p.url, p.score, p.num_comments, p.created_utc,
               p.author, p.selftext, p.scraped_at, p.campus, p.course, p.professor_id,
               COUNT(c.reddit_id) AS stored_comment_count
        FROM posts p
        LEFT JOIN comments c ON c.post_reddit_id = p.reddit_id
        GROUP BY p.reddit_id
        HAVING stored_comment_count = 0
            OR stored_comment_count < p.num_comments
        ORDER BY (p.num_comments - stored_comment_count) DESC, p.created_utc DESC
    """
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return list(conn.execute(sql))


def update_post_parse(
    conn: sqlite3.Connection,
    reddit_id: str,
    campus: str | None,
    course: str | None,
    professor_id: str | None,
) -> None:
    """Update parsed fields on an existing post row."""
    conn.execute(
        """
        UPDATE posts
        SET campus = ?, course = ?, professor_id = ?
        WHERE reddit_id = ?
        """,
        (campus, course, professor_id, reddit_id),
    )


def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return summary statistics about the database contents."""
    stats: dict[str, Any] = {}

    for table, key in [("posts", "total_posts"), ("comments", "total_comments"),
                       ("professors", "total_professors")]:
        row = conn.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
        stats[key] = row["cnt"]

    # Posts with successful parse
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM posts WHERE professor_id IS NOT NULL"
    ).fetchone()
    stats["parsed_posts"] = row["cnt"]
    stats["unparsed_posts"] = stats["total_posts"] - stats["parsed_posts"]

    if stats["total_posts"] > 0:
        stats["parse_rate"] = f"{stats['parsed_posts'] / stats['total_posts']:.1%}"
    else:
        stats["parse_rate"] = "N/A"

    # Campus breakdown
    rows = conn.execute(
        "SELECT campus, COUNT(*) AS cnt FROM posts "
        "WHERE campus IS NOT NULL GROUP BY campus ORDER BY cnt DESC"
    ).fetchall()
    stats["campuses"] = {row["campus"]: row["cnt"] for row in rows}

    return stats


def get_all_posts_with_comments(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Return all posts joined with their comments and professor info."""
    posts = conn.execute(
        """
        SELECT p.*, pr.last_name AS prof_last, pr.first_name AS prof_first
        FROM posts p
        LEFT JOIN professors pr ON p.professor_id = pr.id
        ORDER BY p.created_utc DESC
        """
    ).fetchall()

    results: list[dict[str, Any]] = []
    for post in posts:
        comments = conn.execute(
            """
            SELECT * FROM comments
            WHERE post_reddit_id = ?
            ORDER BY created_utc ASC
            """,
            (post["reddit_id"],),
        ).fetchall()

        results.append({
            "post": dict(post),
            "comments": [dict(c) for c in comments],
        })

    return results


def get_professors_grouped(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return professor data grouped with their discussion threads."""
    professors = conn.execute(
        "SELECT * FROM professors ORDER BY campus, last_name, first_name"
    ).fetchall()

    results: list[dict[str, Any]] = []
    for prof in professors:
        posts = conn.execute(
            """
            SELECT reddit_id, title, course, url, score, num_comments, created_utc
            FROM posts
            WHERE professor_id = ?
            ORDER BY created_utc DESC
            """,
            (prof["id"],),
        ).fetchall()

        courses = sorted({p["course"] for p in posts if p["course"]})

        results.append({
            "professor": dict(prof),
            "courses": courses,
            "total_discussions": len(posts),
            "discussions": [dict(p) for p in posts],
        })

    return results
