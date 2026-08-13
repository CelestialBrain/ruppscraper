"""Reddit scraper with a resilient fetch stack.

Order of preference:
  1. PRAW (OAuth) when REDDIT_CLIENT_ID/SECRET are set
  2. Reddit public .json endpoints
  3. Arctic Shift archive API (when Reddit blocks unauthenticated JSON)
  4. Subreddit RSS (listings only — no scores/comments)
"""

from __future__ import annotations

import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Generator
from xml.etree import ElementTree as ET

import requests

try:
    import praw
except ImportError:
    praw = None

from scraper.config import (
    ARCTIC_PAGE_DELAY,
    ARCTIC_SHIFT_BASE,
    COMMENT_ENRICH_TOP_N,
    COMMENT_WORKERS,
    COURTESY_DELAY,
    LISTING_LIMIT,
    PREFER_ARCTIC,
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    REQUEST_TIMEOUT,
    SUBREDDIT_NAME,
    USER_AGENTS,
)
from scraper.models import Comment, Post
from scraper.parser import ParsedTitle, parse_title

logger = logging.getLogger(__name__)

# Last backend used by fetch_posts / enrich_with_comments (for CLI messaging).
_last_listing_backend: str = "none"
_last_comment_backend: str = "none"


def get_last_backends() -> tuple[str, str]:
    """Return (listing_backend, comment_backend) from the most recent scrape."""
    return _last_listing_backend, _last_comment_backend


def _has_praw_credentials() -> bool:
    """Check if Reddit API OAuth credentials are available."""
    return bool(praw and REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)


def has_praw_credentials() -> bool:
    """Public alias for CLI / callers."""
    return _has_praw_credentials()


# ---------------------------------------------------------------------------
# User-agent rotation
# ---------------------------------------------------------------------------

def _ua() -> str:
    """Pick a random user-agent string."""
    if REDDIT_USER_AGENT:
        return REDDIT_USER_AGENT
    return random.choice(USER_AGENTS)


def _session_headers(accept: str = "application/json") -> dict[str, str]:
    return {"User-Agent": _ua(), "Accept": accept}


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

def _fetch_json_paginated(
    url: str,
    limit: int | None = None,
    extra_params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch multiple pages of a Reddit listing, following 'after' cursors.

    Reddit caps each page at 100 posts and listing endpoints at ~1000 total.
    Returns [] on hard blocks (403/429) so callers can fall back.
    """
    all_posts: list[dict[str, Any]] = []
    after: str | None = None
    page_size = min(LISTING_LIMIT, limit) if limit else LISTING_LIMIT
    remaining = limit

    while True:
        params: dict[str, str] = {"limit": str(page_size), "raw_json": "1"}
        if after:
            params["after"] = after
        if extra_params:
            params.update(extra_params)

        try:
            resp = requests.get(
                url,
                headers=_session_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code in (403, 429):
                logger.warning(
                    "Reddit JSON blocked (%s) for %s — will try fallbacks",
                    resp.status_code,
                    url,
                )
                break
            if resp.status_code != 200:
                logger.warning("Reddit JSON HTTP %s for %s", resp.status_code, url)
                break
            # Guard against HTML interstitial bodies
            ctype = resp.headers.get("content-type", "")
            if "json" not in ctype:
                logger.warning("Reddit JSON returned non-JSON content-type: %s", ctype)
                break
            data = resp.json()
        except Exception as exc:
            logger.warning("Reddit JSON fetch failed: %s", exc)
            break

        children = data.get("data", {}).get("children", [])
        if not children:
            break

        for child in children:
            post_data = child.get("data", {})
            if post_data:
                all_posts.append(post_data)

        if remaining is not None:
            remaining -= len(children)
            if remaining <= 0:
                break

        after = data.get("data", {}).get("after")
        if not after:
            break

        time.sleep(COURTESY_DELAY)

    return all_posts[:limit] if limit else all_posts


# ---------------------------------------------------------------------------
# Arctic Shift archive fallback
# ---------------------------------------------------------------------------

def wrap_arctic_as_listing(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap Arctic Shift post dicts into Reddit listing JSON shape."""
    return {
        "data": {
            "children": [{"kind": "t3", "data": p} for p in posts if p],
        }
    }


def _fetch_arctic_posts(
    subreddit: str,
    limit: int | None = None,
    query: str | None = None,
    sort: str = "new",
    after: float | None = None,
    before: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch submissions from the Arctic Shift archive API.

    ``limit=None`` pages until the window is exhausted (capped at 50k).
    ``after`` / ``before`` are unix timestamps (inclusive after, exclusive before).
    """
    target = limit if limit is not None else 50_000
    collected: list[dict[str, Any]] = []
    cursor = before

    # Query searches are picky: sort_type=created_utc often 422s. Prefer bare
    # query params; only attach sort_type for non-query listing passes.
    sort_type = "score" if sort == "top" else "created_utc"
    sort_dir = "desc"

    while len(collected) < target:
        page_size = min(100, target - len(collected))
        params: dict[str, str] = {
            "subreddit": subreddit,
            "limit": str(page_size),
        }
        if query:
            params["query"] = query
        else:
            params["sort"] = sort_dir
            params["sort_type"] = sort_type
        if cursor is not None:
            params["before"] = str(int(cursor))

        url = f"{ARCTIC_SHIFT_BASE}/api/posts/search"
        try:
            resp = requests.get(
                url,
                headers=_session_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            # Retry variants Arctic sometimes accepts when the first combo 422s.
            if resp.status_code == 422:
                logger.warning("Arctic Shift 422 — slowing down and retrying")
                time.sleep(max(2.0, ARCTIC_PAGE_DELAY * 8))
                retries: list[dict[str, str]] = [dict(params)]
                if query:
                    retries.append({
                        "subreddit": subreddit,
                        "limit": str(page_size),
                        "query": f'"{query}"',
                    })
                    retries.append({
                        "subreddit": subreddit,
                        "limit": str(page_size),
                        "query": query,
                    })
                else:
                    retries.append({
                        "subreddit": subreddit,
                        "limit": str(page_size),
                    })
                for retry in retries:
                    if cursor is not None:
                        retry["before"] = str(int(cursor))
                    resp = requests.get(
                        url,
                        headers=_session_headers(),
                        params=retry,
                        timeout=REQUEST_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        break
                    time.sleep(2.0)
            if resp.status_code == 429:
                logger.warning("Arctic Shift rate-limited (429) — backing off")
                time.sleep(2.0)
                resp = requests.get(
                    url,
                    headers=_session_headers(),
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
            if resp.status_code != 200:
                logger.warning("Arctic Shift posts HTTP %s", resp.status_code)
                break
            payload = resp.json()
        except Exception as exc:
            logger.warning("Arctic Shift posts failed: %s", exc)
            break

        batch = payload.get("data") or []
        if not batch:
            break

        if after is not None:
            in_window = [
                p for p in batch if float(p.get("created_utc") or 0) >= after
            ]
            collected.extend(in_window)
            if len(in_window) < len(batch):
                break
        else:
            collected.extend(batch)

        oldest = batch[-1].get("created_utc")
        if oldest is None:
            break
        # Page using exclusive upper bound on created_utc
        cursor = float(oldest)
        if after is not None and cursor < after:
            break
        if len(batch) < page_size:
            break
        time.sleep(ARCTIC_PAGE_DELAY)

    return collected[:target]


def _fetch_arctic_comments(
    post_id: str, limit: int | None = None
) -> list[dict[str, Any]]:
    """Fetch comments for a post from Arctic Shift.

    ``limit=None`` pages until the thread is exhausted (capped at 5000).
    """
    if not post_id:
        return []

    target = limit if limit is not None else 5000
    collected: list[dict[str, Any]] = []
    cursor: float | None = None
    seen_id: set[str] = set()
    url = f"{ARCTIC_SHIFT_BASE}/api/comments/search"

    while len(collected) < target:
        page_size = min(100, target - len(collected))
        params: dict[str, str] = {
            "link_id": post_id,
            "limit": str(page_size),
            "sort": "desc",
        }
        if cursor is not None:
            params["before"] = str(int(cursor))

        try:
            resp = requests.get(
                url,
                headers=_session_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            # Retry variants Arctic sometimes accepts when the first combo 422s.
            if resp.status_code == 422:
                logger.warning("Arctic Shift comments 422 — slowing down and retrying")
                time.sleep(max(2.0, ARCTIC_PAGE_DELAY * 8))
                retries: list[dict[str, str]] = [
                    dict(params),
                    {"link_id": post_id, "limit": str(page_size)},
                ]
                for retry in retries:
                    if cursor is not None:
                        retry["before"] = str(int(cursor))
                    resp = requests.get(
                        url,
                        headers=_session_headers(),
                        params=retry,
                        timeout=REQUEST_TIMEOUT,
                    )
                    if resp.status_code == 200:
                        break
                    time.sleep(2.0)
            if resp.status_code == 429:
                logger.warning("Arctic Shift comments rate-limited (429) — backing off")
                time.sleep(2.0)
                resp = requests.get(
                    url,
                    headers=_session_headers(),
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )
            if resp.status_code != 200:
                logger.warning("Arctic Shift comments HTTP %s", resp.status_code)
                break
            payload = resp.json()
        except Exception as exc:
            logger.warning("Arctic Shift comments failed: %s", exc)
            break

        batch = payload.get("data") or []
        if not batch:
            break

        new_row: list[dict[str, Any]] = []
        for row in batch:
            comment_id = str(row.get("id") or "")
            if comment_id and comment_id in seen_id:
                continue
            if comment_id:
                seen_id.add(comment_id)
            new_row.append(row)
        if not new_row:
            break
        collected.extend(new_row)

        timestamps = [
            float(c.get("created_utc") or 0)
            for c in new_row
            if c.get("created_utc") is not None
        ]
        oldest = min(timestamps) if timestamps else new_row[-1].get("created_utc")
        if oldest is None:
            break
        # Page using exclusive upper bound on created_utc
        cursor = float(oldest)
        if len(batch) < page_size:
            break
        time.sleep(ARCTIC_PAGE_DELAY)

    comments: list[dict[str, Any]] = []
    for cdata in collected[:target]:
        body = cdata.get("body", "")
        if body in ("[deleted]", "[removed]", ""):
            continue
        parent_id = cdata.get("parent_id") or f"t3_{post_id}"
        # Approximate depth from parent prefix (t3 = top-level).
        depth = 0 if str(parent_id).startswith("t3_") else 1
        comments.append({
            "id": cdata.get("id", ""),
            "parent_id": parent_id,
            "author": cdata.get("author"),
            "body": body,
            "score": cdata.get("score", 0) or 0,
            "created_utc": cdata.get("created_utc", 0) or 0,
            "depth": depth,
        })
    return comments


# ---------------------------------------------------------------------------
# RSS fallback fetcher
# ---------------------------------------------------------------------------

def _fetch_rss(url: str) -> list[dict[str, Any]]:
    """Fallback: fetch RSS feed and parse into pseudo-post dicts.

    Returns dicts shaped like the JSON API's post data so the rest of the
    pipeline doesn't need to branch.
    """
    headers = _session_headers("application/rss+xml, text/xml, */*")
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code in (403, 429):
            logger.warning("Reddit RSS blocked (%s)", resp.status_code)
            return []
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Reddit RSS fetch failed: %s", exc)
        return []

    if not resp.text.strip():
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    posts: list[dict[str, Any]] = []

    try:
        root = ET.fromstring(resp.text)
        entries = root.findall(".//atom:entry", ns)
        if not entries:
            # Some feeds use a default xmlns without prefixes in findall edge cases
            entries = [e for e in root.iter() if str(e.tag).endswith("entry")]

        for entry in entries:
            title_el = entry.find("atom:title", ns)
            if title_el is None:
                title_el = next((c for c in entry if str(c.tag).endswith("title")), None)
            link_el = entry.find("atom:link[@href]", ns)
            if link_el is None:
                link_el = next(
                    (
                        c
                        for c in entry
                        if str(c.tag).endswith("link") and c.get("href")
                    ),
                    None,
                )
            id_el = entry.find("atom:id", ns)
            if id_el is None:
                id_el = next((c for c in entry if str(c.tag).endswith("id")), None)
            author_el = entry.find("atom:author/atom:name", ns)
            published_el = entry.find("atom:published", ns)
            updated_el = entry.find("atom:updated", ns)
            content_el = entry.find("atom:content", ns)

            if title_el is None or not (title_el.text or "").strip():
                continue

            title = title_el.text.strip()
            permalink = link_el.get("href", "") if link_el is not None else ""
            post_id_raw = id_el.text if id_el is not None else permalink
            author = author_el.text if author_el is not None else None

            created_utc = 0.0
            pub_el = published_el if published_el is not None else updated_el
            if pub_el is not None and pub_el.text:
                try:
                    dt = datetime.fromisoformat(pub_el.text.replace("Z", "+00:00"))
                    created_utc = dt.timestamp()
                except ValueError:
                    pass

            selftext = ""
            if content_el is not None and content_el.text:
                selftext = re.sub(r"<[^>]+>", " ", content_el.text)
                selftext = re.sub(r"\s+", " ", selftext).strip()

            reddit_id = ""
            if post_id_raw:
                id_match = re.search(r"t3_(\w+)", str(post_id_raw))
                if id_match:
                    reddit_id = id_match.group(1)
                else:
                    parts = permalink.rstrip("/").split("/")
                    # .../comments/<id>/<slug>/
                    if "comments" in parts:
                        idx = parts.index("comments")
                        if idx + 1 < len(parts):
                            reddit_id = parts[idx + 1]

            posts.append({
                "id": reddit_id,
                "title": title,
                "author": author.replace("/u/", "") if author else None,
                "permalink": permalink.replace("https://www.reddit.com", "")
                if permalink.startswith("https://")
                else permalink,
                "url": permalink,
                "selftext": selftext,
                "score": 0,
                "num_comments": 0,
                "created_utc": created_utc,
                "_source": "rss",
            })
    except ET.ParseError as exc:
        logger.warning("RSS parse error: %s", exc)

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
    """
    if not post_id or len(post_id) > 12:
        return [], None

    url = f"https://www.reddit.com/comments/{post_id}.json"
    params = {"limit": str(limit), "sort": "top", "depth": "2", "raw_json": "1"}

    try:
        resp = requests.get(
            url,
            headers=_session_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code in (403, 429):
            return [], None
        if resp.status_code != 200:
            return [], None
        if "json" not in resp.headers.get("content-type", ""):
            return [], None
        payload = resp.json()
    except Exception:
        return [], None

    if not isinstance(payload, list) or len(payload) < 2:
        return [], None

    fresh_post: dict[str, Any] | None = None
    try:
        fresh_post = payload[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        pass

    raw_comments: list[dict[str, Any]] = []
    try:
        children = payload[1]["data"]["children"]
    except (KeyError, IndexError, TypeError):
        children = []

    def _walk_comments(nodes: list[dict], depth: int = 0) -> None:
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

            replies = cdata.get("replies")
            if isinstance(replies, dict):
                reply_children = replies.get("data", {}).get("children", [])
                _walk_comments(reply_children, depth + 1)

    _walk_comments(children)
    return raw_comments, fresh_post


# ---------------------------------------------------------------------------
# Post model builder
# ---------------------------------------------------------------------------

def _post_from_raw(post_data: dict[str, Any], scraped_at: float) -> tuple[Post, ParsedTitle | None]:
    """Convert a Reddit-shaped dict into a Post + ParsedTitle."""
    post_id = post_data.get("id", "") or ""
    title = post_data.get("title", "") or ""
    parsed = parse_title(title)

    author = post_data.get("author")
    permalink = post_data.get("permalink", "") or ""
    if permalink.startswith("http"):
        post_url = permalink
    elif permalink.startswith("/"):
        post_url = f"https://reddit.com{permalink}"
    else:
        post_url = post_data.get("url", "") or f"https://reddit.com/comments/{post_id}"

    post = Post(
        reddit_id=post_id,
        title=title,
        campus=parsed.campus if parsed else None,
        course=parsed.course if parsed else None,
        professor_id=parsed.professor_id if parsed else None,
        url=post_url,
        score=int(post_data.get("score", 0) or 0),
        num_comments=int(post_data.get("num_comments", 0) or 0),
        created_utc=float(post_data.get("created_utc", 0) or 0),
        author=author if author and author != "[deleted]" else None,
        selftext=post_data.get("selftext", "") or "",
        scraped_at=scraped_at,
    )
    return post, parsed


# ---------------------------------------------------------------------------
# Public API: main fetch pipeline
# ---------------------------------------------------------------------------

def fetch_posts(
    sort: str = "new",
    limit: int | None = None,
    skip_ids: set[str] | None = None,
    subreddit: str = SUBREDDIT_NAME,
    query: str | None = None,
    after: float | None = None,
    before: float | None = None,
    archive: bool = False,
) -> Generator[tuple[Post, ParsedTitle | None, list[Comment]], None, None]:
    """Fetch posts from r/RateUPProfs and yield (Post, ParsedTitle, [Comment]).

    Uses PRAW if OAuth credentials exist, otherwise JSON → Arctic Shift → RSS.
    ``archive=True`` pages Arctic Shift over an optional after/before window.
    """
    global _last_listing_backend
    skip = skip_ids or set()

    if archive or after is not None or before is not None:
        raw_posts = _fetch_arctic_posts(
            subreddit,
            limit=limit,
            query=query,
            sort=sort,
            after=after,
            before=before,
        )
        _last_listing_backend = "arctic" if raw_posts else "none"
        now = datetime.now(timezone.utc).timestamp()
        for post_data in raw_posts:
            post_id = post_data.get("id", "")
            if post_id in skip:
                continue
            if post_data.get("removed_by_category") or post_data.get("stickied"):
                continue
            post, parsed = _post_from_raw(post_data, now)
            if not post.reddit_id:
                continue
            yield post, parsed, []
        return

    if _has_praw_credentials() and not query and not PREFER_ARCTIC:
        _last_listing_backend = "praw"
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
    raw_posts: list[dict[str, Any]] = []
    _last_listing_backend = "none"

    # Query shards (and CI) go straight to Arctic — Reddit search/listing JSON is 403.
    if query or PREFER_ARCTIC:
        raw_posts = _fetch_arctic_posts(subreddit, limit=limit, query=query, sort=sort)
        _last_listing_backend = "arctic" if raw_posts else "none"
        if not raw_posts:
            raw_posts = _fetch_rss(urls["rss_new"])
            if limit:
                raw_posts = raw_posts[:limit]
            _last_listing_backend = "rss" if raw_posts else "none"
    else:
        sort_key = f"json_{sort}"
        json_url = urls.get(sort_key, urls["json_new"])
        extra_params: dict[str, str] = {}
        if sort == "top":
            extra_params["t"] = "all"

        raw_posts = _fetch_json_paginated(json_url, limit=limit, extra_params=extra_params)
        if raw_posts:
            _last_listing_backend = "json"
        else:
            raw_posts = _fetch_arctic_posts(subreddit, limit=limit, sort=sort)
            if raw_posts:
                _last_listing_backend = "arctic"
            else:
                raw_posts = _fetch_rss(urls["rss_new"])
                if limit:
                    raw_posts = raw_posts[:limit]
                _last_listing_backend = "rss" if raw_posts else "none"

    now = datetime.now(timezone.utc).timestamp()

    for post_data in raw_posts:
        post_id = post_data.get("id", "")
        if post_id in skip:
            continue
        if post_data.get("removed_by_category") or post_data.get("stickied"):
            continue

        post, parsed = _post_from_raw(post_data, now)
        if not post.reddit_id:
            continue
        yield post, parsed, []


def _comments_from_raw(
    post_id: str, raw: list[dict[str, Any]], now: float
) -> list[Comment]:
    comments: list[Comment] = []
    for c in raw:
        comments.append(
            Comment(
                reddit_id=c["id"],
                post_reddit_id=post_id,
                parent_id=c["parent_id"],
                author=c["author"] if c["author"] != "[deleted]" else None,
                body=c["body"],
                score=c["score"],
                created_utc=float(c["created_utc"] or 0),
                depth=c["depth"],
                scraped_at=now,
            )
        )
    return comments


def _fetch_comments_for_post(post: Post, now: float) -> tuple[str, list[Comment], str]:
    """Fetch comments for one post. Arctic-first when PREFER_ARCTIC is set."""
    if PREFER_ARCTIC:
        raw = _fetch_arctic_comments(post.reddit_id)
        return post.reddit_id, _comments_from_raw(post.reddit_id, raw, now), (
            "arctic" if raw else "none"
        )

    if _has_praw_credentials() and praw is not None:
        try:
            reddit = praw.Reddit(
                client_id=REDDIT_CLIENT_ID,
                client_secret=REDDIT_CLIENT_SECRET,
                user_agent=_ua(),
            )
            reddit.read_only = True
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
            return post.reddit_id, comments, "praw"
        except Exception:
            raw = _fetch_arctic_comments(post.reddit_id)
            return (
                post.reddit_id,
                _comments_from_raw(post.reddit_id, raw, now),
                "arctic",
            )

    raw_comments, fresh_post = _fetch_post_comments(post.reddit_id)
    backend = "json"
    if fresh_post and isinstance(fresh_post, dict):
        post.score = fresh_post.get("score", post.score)
        post.num_comments = fresh_post.get("num_comments", post.num_comments)
    if not raw_comments:
        raw_comments = _fetch_arctic_comments(post.reddit_id)
        backend = "arctic"
    return post.reddit_id, _comments_from_raw(post.reddit_id, raw_comments, now), backend


def enrich_with_comments(
    posts: list[Post],
    top_n: int = COMMENT_ENRICH_TOP_N,
) -> dict[str, list[Comment]]:
    """Fetch comments for the top N posts.

    Sequential + courtesy delay by default. In CI, set RUPP_PREFER_ARCTIC=1
    and RUPP_COMMENT_WORKERS>1 to fetch Arctic comments in parallel.
    """
    global _last_comment_backend
    now = datetime.now(timezone.utc).timestamp()
    result: dict[str, list[Comment]] = {}
    backends_used: set[str] = set()
    targets = [
        p for p in posts[:top_n] if p.reddit_id and len(p.reddit_id) <= 12
    ]

    if _has_praw_credentials() and not PREFER_ARCTIC and praw is not None:
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=_ua(),
        )
        reddit.read_only = True
        for post in targets:
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
                backends_used.add("praw")
            except Exception:
                raw = _fetch_arctic_comments(post.reddit_id)
                result[post.reddit_id] = _comments_from_raw(post.reddit_id, raw, now)
                backends_used.add("arctic")
            time.sleep(COURTESY_DELAY)
        _last_comment_backend = "+".join(sorted(backends_used)) or "none"
        return result

    workers = COMMENT_WORKERS if PREFER_ARCTIC else 1
    if workers <= 1:
        for post in targets:
            rid, comments, backend = _fetch_comments_for_post(post, now)
            result[rid] = comments
            backends_used.add(backend)
            time.sleep(COURTESY_DELAY)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_fetch_comments_for_post, post, now) for post in targets
            ]
            for fut in as_completed(futures):
                rid, comments, backend = fut.result()
                result[rid] = comments
                backends_used.add(backend)

    _last_comment_backend = "+".join(sorted(backends_used)) or "none"
    return result
