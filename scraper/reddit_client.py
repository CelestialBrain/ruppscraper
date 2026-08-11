"""Reddit scraper using raw JSON/RSS endpoints (no PRAW, no OAuth).

Mirrors the approach used in pingfree: hit Reddit's public .json endpoints
directly, fall back to RSS when rate-limited, rotate user-agents, and pace
requests with courtesy delays.
"""

from __future__ import annotations

import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Generator
from xml.etree import ElementTree as ET

import requests

try:
    import praw
except ImportError:
    praw = None

from scraper.config import (
    COMMENT_ENRICH_TOP_N,
    COURTESY_DELAY,
    LISTING_LIMIT,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    REQUEST_TIMEOUT,
    SUBREDDIT_NAME,
    USER_AGENTS,
)
from scraper.models import Comment, Post
from scraper.parser import ParsedTitle, parse_title


def _has_praw_credentials() -> bool:
    """Check if Reddit API OAuth credentials are available."""
    return bool(praw and REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)


# ---------------------------------------------------------------------------
# User-agent rotation
# ---------------------------------------------------------------------------

def _ua() -> str:
    """Pick a random user-agent string."""
    if REDDIT_USER_AGENT:
        return REDDIT_USER_AGENT
    return random.choice(USER_AGENTS)


# ---------------------------------------------------------------------------
# Endpoint builders
# ---------------------------------------------------------------------------

def _subreddit_urls(subreddit: str) -> dict[str, str]:
    """Build the JSON and RSS endpoint URLs for a given subreddit."""
    base = f"https://www.reddit.com/r/{subreddit}"
    return {
        "json_new": f"{base}/new.json",
        "json_top": f"{base}/top.json",
        "json_hot": f"{base}/hot.json",
        "json_rising": f"{base}/rising.json",
        "rss_new": f"{base}/new.rss",
    }


# ---------------------------------------------------------------------------
# JSON listing fetcher
# ---------------------------------------------------------------------------

def _fetch_json_listing(
    url: str,
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch a single page of Reddit listing JSON. Returns raw post dicts."""
    headers = {"User-Agent": _ua(), "Accept": "application/json"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        children = data.get("data", {}).get("children", [])
        return [child.get("data", {}) for child in children if child.get("data")]
    except (requests.RequestException, ValueError, KeyError):
        return []


def _fetch_json_paginated(
    url: str,
    limit: int | None = None,
    extra_params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch multiple pages of a Reddit listing, following 'after' cursors.

    Reddit caps each page at 100 posts and listing endpoints at ~1000 total.
    """
    all_posts: list[dict[str, Any]] = []
    after: str | None = None
    page_size = min(LISTING_LIMIT, limit) if limit else LISTING_LIMIT
    remaining = limit

    while True:
        params: dict[str, str] = {"limit": str(page_size)}
        if after:
            params["after"] = after
        if extra_params:
            params.update(extra_params)

        headers = {"User-Agent": _ua(), "Accept": "application/json"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                break
            data = resp.json()
        except Exception:
            break

        children = data.get("data", {}).get("children", [])
        if not children:
            break

        for child in children:
            post_data = child.get("data", {})
            if post_data:
                all_posts.append(post_data)

        # Check if we've hit the limit
        if remaining is not None:
            remaining -= len(children)
            if remaining <= 0:
                break

        # Next page cursor
        after = data.get("data", {}).get("after")
        if not after:
            break

        # Courtesy delay between pages
        time.sleep(COURTESY_DELAY)

    return all_posts[:limit] if limit else all_posts


# ---------------------------------------------------------------------------
# RSS fallback fetcher
# ---------------------------------------------------------------------------

def _fetch_rss(url: str) -> list[dict[str, Any]]:
    """Fallback: fetch RSS feed and parse into pseudo-post dicts.

    Returns dicts shaped like the JSON API's post data so the rest of the
    pipeline doesn't need to branch.
    """
    headers = {"User-Agent": _ua(), "Accept": "application/rss+xml, text/xml"}
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    posts: list[dict[str, Any]] = []

    try:
        root = ET.fromstring(resp.text)
        for entry in root.findall(".//atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            link_el = entry.find("atom:link[@href]", ns)
            id_el = entry.find("atom:id", ns)
            author_el = entry.find("atom:author/atom:name", ns)
            published_el = entry.find("atom:published", ns)
            updated_el = entry.find("atom:updated", ns)
            content_el = entry.find("atom:content", ns)

            if title_el is None or not title_el.text:
                continue

            title = title_el.text.strip()
            permalink = link_el.get("href", "") if link_el is not None else ""
            post_id = id_el.text if id_el is not None else permalink
            author = author_el.text if author_el is not None else None

            # Extract created_utc from published date
            created_utc = 0.0
            pub_el = published_el if published_el is not None else updated_el
            if pub_el is not None and pub_el.text:
                try:
                    dt = datetime.fromisoformat(pub_el.text.replace("Z", "+00:00"))
                    created_utc = dt.timestamp()
                except ValueError:
                    pass

            # Extract selftext from content HTML
            selftext = ""
            if content_el is not None and content_el.text:
                # Strip HTML tags for plain text
                selftext = re.sub(r"<[^>]+>", " ", content_el.text)
                selftext = re.sub(r"\s+", " ", selftext).strip()

            # Extract the reddit post ID from the permalink or id field
            reddit_id = ""
            if post_id:
                # ID format is usually like "t3_abc123" or a full URL
                id_match = re.search(r"t3_(\w+)", str(post_id))
                if id_match:
                    reddit_id = id_match.group(1)
                else:
                    # Try extracting from permalink URL
                    parts = permalink.rstrip("/").split("/")
                    if len(parts) >= 2:
                        reddit_id = parts[-2] if parts[-1] != "" else parts[-1]

            posts.append({
                "id": reddit_id,
                "title": title,
                "author": author.replace("/u/", "") if author else None,
                "permalink": permalink,
                "url": permalink,
                "selftext": selftext,
                "score": 0,       # Not available from RSS
                "num_comments": 0,  # Not available from RSS
                "created_utc": created_utc,
                "_source": "rss",
            })
    except ET.ParseError:
        pass

    return posts


# ---------------------------------------------------------------------------
# Comment fetcher (per-post enrichment via /comments/{id}.json)
# ---------------------------------------------------------------------------

def _fetch_post_comments(
    post_id: str,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Fetch comments for a single post via /comments/{id}.json.

    Returns (comments_list, fresh_post_data_or_None).

    Reddit returns a 2-element array:
      [0] = listing with the post itself (freshest version)
      [1] = listing of comments
    """
    if not post_id or len(post_id) > 12:
        return [], None

    url = f"https://www.reddit.com/comments/{post_id}.json"
    headers = {"User-Agent": _ua(), "Accept": "application/json"}
    params = {"limit": str(limit), "sort": "top", "depth": "2"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return [], None
        payload = resp.json()
    except Exception:
        return [], None

    if not isinstance(payload, list) or len(payload) < 2:
        return [], None

    # Extract fresh post data
    fresh_post: dict[str, Any] | None = None
    try:
        fresh_post = payload[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        pass

    # Extract comments
    raw_comments: list[dict[str, Any]] = []
    try:
        children = payload[1]["data"]["children"]
    except (KeyError, IndexError, TypeError):
        children = []

    def _walk_comments(nodes: list[dict], depth: int = 0) -> None:
        """Recursively walk the comment tree."""
        for node in nodes:
            if node.get("kind") != "t1":
                continue
            cdata = node.get("data", {})
            body = cdata.get("body", "")
            if body in ("[deleted]", "[removed]", ""):
                continue

            raw_comments.append({
                "id": cdata.get("id", ""),
                "parent_id": cdata.get("parent_id", ""),
                "author": cdata.get("author"),
                "body": body,
                "score": cdata.get("score", 0),
                "created_utc": cdata.get("created_utc", 0),
                "depth": depth,
            })

            # Recurse into replies
            replies = cdata.get("replies")
            if isinstance(replies, dict):
                reply_children = replies.get("data", {}).get("children", [])
                _walk_comments(reply_children, depth + 1)

    _walk_comments(children)
    return raw_comments, fresh_post


# ---------------------------------------------------------------------------
# Public API: main fetch pipeline
# ---------------------------------------------------------------------------

def fetch_posts(
    sort: str = "new",
    limit: int | None = None,
    skip_ids: set[str] | None = None,
    subreddit: str = SUBREDDIT_NAME,
) -> Generator[tuple[Post, ParsedTitle | None, list[Comment]], None, None]:
    """Fetch posts from r/RateUPProfs and yield (Post, ParsedTitle, [Comment]).

    Uses PRAW if OAuth credentials exist in .env, otherwise falls back to public RSS/JSON.
    """
    skip = skip_ids or set()

    if _has_praw_credentials():
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=_ua(),
        )
        reddit.read_only = True
        sub = reddit.subreddit(subreddit)
        if sort == "new":
            listing = sub.new(limit=limit)
        elif sort == "hot":
            listing = sub.hot(limit=limit)
        elif sort == "top":
            listing = sub.top(time_filter="all", limit=limit)
        elif sort == "rising":
            listing = sub.rising(limit=limit)
        else:
            listing = sub.new(limit=limit)

        now = datetime.now(timezone.utc).timestamp()
        for submission in listing:
            if submission.id in skip:
                continue
            if getattr(submission, "stickied", False):
                continue
            title = submission.title or ""
            parsed = parse_title(title)
            author = str(submission.author) if submission.author else None
            post = Post(
                reddit_id=submission.id,
                title=title,
                campus=parsed.campus if parsed else None,
                course=parsed.course if parsed else None,
                professor_id=parsed.professor_id if parsed else None,
                url=f"https://reddit.com{submission.permalink}",
                score=submission.score,
                num_comments=submission.num_comments,
                created_utc=submission.created_utc,
                author=author if author and author != "[deleted]" else None,
                selftext=submission.selftext or "",
                scraped_at=now,
            )
            yield post, parsed, []
        return

    urls = _subreddit_urls(subreddit)

    # Select endpoint by sort
    sort_key = f"json_{sort}"
    json_url = urls.get(sort_key, urls["json_new"])

    extra_params: dict[str, str] = {}
    if sort == "top":
        extra_params["t"] = "all"  # time_filter=all

    # Try JSON first (richer data)
    raw_posts = _fetch_json_paginated(json_url, limit=limit, extra_params=extra_params)

    # Fallback to RSS if JSON failed
    if not raw_posts:
        raw_posts = _fetch_rss(urls["rss_new"])
        if limit:
            raw_posts = raw_posts[:limit]

    # Process each post
    now = datetime.now(timezone.utc).timestamp()

    for post_data in raw_posts:
        post_id = post_data.get("id", "")

        # Skip already-scraped
        if post_id in skip:
            continue

        # Skip removed / meta / stickied posts
        if post_data.get("removed_by_category") or post_data.get("stickied"):
            continue

        # Parse the title
        title = post_data.get("title", "")
        parsed = parse_title(title)

        # Build the Post model
        author = post_data.get("author")
        permalink = post_data.get("permalink", "")
        post_url = f"https://reddit.com{permalink}" if permalink.startswith("/") else permalink

        post = Post(
            reddit_id=post_id,
            title=title,
            campus=parsed.campus if parsed else None,
            course=parsed.course if parsed else None,
            professor_id=parsed.professor_id if parsed else None,
            url=post_url,
            score=post_data.get("score", 0),
            num_comments=post_data.get("num_comments", 0),
            created_utc=post_data.get("created_utc", 0),
            author=author if author and author != "[deleted]" else None,
            selftext=post_data.get("selftext", ""),
            scraped_at=now,
        )

        # Comments will be empty initially — enriched separately
        yield post, parsed, []


def enrich_with_comments(
    posts: list[Post],
    top_n: int = COMMENT_ENRICH_TOP_N,
) -> dict[str, list[Comment]]:
    """Fetch comments for the top N posts.

    Returns a dict mapping post reddit_id → list of Comment models.
    """
    now = datetime.now(timezone.utc).timestamp()
    result: dict[str, list[Comment]] = {}

    if _has_praw_credentials():
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=_ua(),
        )
        reddit.read_only = True
        for post in posts[:top_n]:
            if not post.reddit_id or len(post.reddit_id) > 12:
                continue
            try:
                submission = reddit.submission(id=post.reddit_id)
                submission.comments.replace_more(limit=0)
                comments: list[Comment] = []
                for c in submission.comments.list():
                    if not hasattr(c, "body") or c.body in ("[deleted]", "[removed]", ""):
                        continue
                    author = str(c.author) if c.author else None
                    comments.append(
                        Comment(
                            reddit_id=c.id,
                            post_reddit_id=post.reddit_id,
                            parent_id=c.parent_id,
                            author=author if author != "[deleted]" else None,
                            body=c.body,
                            score=c.score,
                            created_utc=c.created_utc,
                            depth=c.depth,
                            scraped_at=now,
                        )
                    )
                result[post.reddit_id] = comments
            except Exception:
                pass
            time.sleep(COURTESY_DELAY)
        return result

    for post in posts[:top_n]:
        if not post.reddit_id or len(post.reddit_id) > 12:
            continue

        raw_comments, fresh_post = _fetch_post_comments(post.reddit_id)

        # Update post score/num_comments from fresh data if available
        if fresh_post and isinstance(fresh_post, dict):
            post.score = fresh_post.get("score", post.score)
            post.num_comments = fresh_post.get("num_comments", post.num_comments)

        comments: list[Comment] = []
        for c in raw_comments:
            comments.append(
                Comment(
                    reddit_id=c["id"],
                    post_reddit_id=post.reddit_id,
                    parent_id=c["parent_id"],
                    author=c["author"] if c["author"] != "[deleted]" else None,
                    body=c["body"],
                    score=c["score"],
                    created_utc=c["created_utc"],
                    depth=c["depth"],
                    scraped_at=now,
                )
            )

        result[post.reddit_id] = comments
        time.sleep(COURTESY_DELAY)

    return result
