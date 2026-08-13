"""Keep only text that evaluates a professor; quarantine the rest.

A body is a review when a student judges teaching (grading, workload,
pedagogy, take/avoid). It is not a review when it is a reaction, classmate
hunt, prerog/email/notes request, or too short to evaluate anyone.

``email`` / ``prerog`` in a long evaluation stay — those are policy, not
requests. Requests without review language drop.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

from scraper.config import DB_PATH
from scraper.database import get_connection, init_db

_DELETED = frozenset({"", "[deleted]", "[removed]"})

_REVIEW_RE = re.compile(
    r"\b(?:unoable|vouch|goated|workload|grading|attendance|mabait|terror|"
    r"lecture|lectures|exam|exams|quiz|quizzes|recit|recitation|"
    r"take\s+(?:him|her|them|this)|took\s+(?:him|her|them)|avoid|recommended?|"
    r"strict|lenient|reqs|requirements|singko|pasado|bagsak|"
    r"magturo|nagtuturo|open\s+notes|gclass|google\s+class|canvas|uvle|"
    r"long\s+exam|finals|hands[\s-]?on|nagaadjust|deadline|"
    r"1\.0|1\.25|1\.5|10/10|chill\s+(?:prof|class|lang)|"
    r"super\s+vouch|super\s+bait|best\s+profs?|fave\s+prof|"
    r"got\s+an?\s+uno|dont\s+take|don't\s+take|"
    r"light\s+workload|heavy\s+workload|magaling\s+magturo)\b",
    re.IGNORECASE,
)

_REACTION_RE = re.compile(
    r"^(?:up|\+1|same|ditto|agree|following|lol|lmao|haha|hahaha|"
    r"f|w|rip|ok|okay|thanks|ty|tysm|noted|following this)\.?$",
    re.IGNORECASE,
)

_COURSE_CODE_ONLY_RE = re.compile(
    r"^[A-Za-z]{2,4}\s*\d{1,3}(?:\.\d+)?\s*$",
)

_REQUEST_RE = re.compile(
    r"\b(?:\blf\b|looking for|classmates?|kaklase|groupmate|"
    r"penge|pasend|discord|\bgc\b|group chat|"
    r"can i (?:have|get)|pm me|dm me|message me|"
    r"e-?mails?|"
    r"how (?:can|do) i prerog|accepts?\s+prerogs?|prerog\s+din|"
    r"need units|share (?:the )?(?:notes|reviewer)|"
    r"nasa crs|meron na sa crs|waitlist(?:ing|ed)?|"
    r"(?:can|may) i (?:also )?have (?:them|it)|"
    r"hi classmate)\b",
    re.IGNORECASE,
)

_ASK_ONLY_RE = re.compile(
    r"\b(?:any (?:comments?|thoughts?|reviews?)|"
    r"how (?:is|was|po) (?:he|she|this|the|ur|your)|"
    r"has anyone taken|thoughts on (?:this|him|her)|"
    r"can anyone review|experience with|"
    r"is (?:he|she) unoable|musta (?:po )?(?:grade|sya|siya))\b",
    re.IGNORECASE,
)

_THANKS_LOGISTICS_RE = re.compile(
    r"^(?:thank|thanks|tysm|noted|following|ok(?:ay)?|hello|hi)[\s!,.]*|"
    r"\b(?:papasok|pending pa|demand slot|see u sa class|"
    r"thank you po|thank u sm)\b",
    re.IGNORECASE,
)


def classify_review_text(
    text: str | None, *, is_post_body: bool = False
) -> tuple[bool, str]:
    """Return (is_review, drop_reason). drop_reason is empty when kept."""
    body = (text or "").strip()
    if body.lower() in _DELETED:
        return False, "empty"
    if _REACTION_RE.match(body):
        return False, "reaction"
    if _COURSE_CODE_ONLY_RE.match(body):
        return False, "course_code"

    has_review = bool(_REVIEW_RE.search(body))
    is_question = body.rstrip().endswith("?")

    if _ASK_ONLY_RE.search(body) and len(body) <= 100:
        return False, "ask"
    if is_question and len(body) <= 100 and has_review:
        return False, "ask"
    if is_post_body and body.count("?") >= 2 and len(body) < 400:
        return False, "ask"

    if has_review:
        return True, ""

    if _REQUEST_RE.search(body) and (is_post_body or len(body) <= 160):
        return False, "request"
    if _ASK_ONLY_RE.search(body) and (is_post_body or len(body) <= 160):
        return False, "ask"
    if _THANKS_LOGISTICS_RE.search(body) and len(body) <= 80:
        return False, "thanks"
    if len(body) < 80:
        return False, "too_short"
    if is_post_body and len(body) < 150:
        return False, "thin"
    return True, ""


def apply_review_triage(rupp_db_path: Path | None = None) -> dict[str, Any]:
    """Flag every post selftext and comment body. Does not delete rows."""
    path = rupp_db_path or DB_PATH
    conn = get_connection(path)
    init_db(conn)

    comment_reason: Counter[str] = Counter()
    post_reason: Counter[str] = Counter()
    comment_kept = 0
    comment_dropped = 0
    post_kept = 0
    post_dropped = 0

    comments = conn.execute("SELECT reddit_id, body FROM comments").fetchall()
    with conn:
        for row in comments:
            keep, reason = classify_review_text(row["body"])
            conn.execute(
                "UPDATE comments SET is_review = ? WHERE reddit_id = ?",
                (1 if keep else 0, row["reddit_id"]),
            )
            if keep:
                comment_kept += 1
            else:
                comment_dropped += 1
                comment_reason[reason] += 1

        posts = conn.execute("SELECT reddit_id, selftext FROM posts").fetchall()
        for row in posts:
            keep, reason = classify_review_text(row["selftext"], is_post_body=True)
            conn.execute(
                "UPDATE posts SET is_review = ? WHERE reddit_id = ?",
                (1 if keep else 0, row["reddit_id"]),
            )
            if keep:
                post_kept += 1
            else:
                post_dropped += 1
                post_reason[reason] += 1

    conn.close()
    return {
        "comment_kept": comment_kept,
        "comment_dropped": comment_dropped,
        "comment_drop_reason": dict(comment_reason),
        "post_kept": post_kept,
        "post_dropped": post_dropped,
        "post_drop_reason": dict(post_reason),
        "comment_total": comment_kept + comment_dropped,
        "post_total": post_kept + post_dropped,
    }
