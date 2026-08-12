"""CLI entrypoint for the RateUPProfs scraper."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from scraper.config import PROGRESSIVE_PASSES, SUBREDDIT_NAME
from scraper.database import (
    get_connection,
    get_posts_missing_comments,
    get_scraped_post_ids,
    get_stats,
    get_unparsed_posts,
    init_db,
    update_post_parse,
    upsert_post_with_comments,
    upsert_professor,
)
from scraper.crs_matcher import CRSLookup, match_scraped_professors
from scraper.exporter import export_full, export_professors
from scraper.models import Post, Professor
from scraper.parser import parse_title
from scraper.reddit_client import (
    enrich_with_comments,
    fetch_posts,
    get_last_backends,
    has_praw_credentials,
)

console = Console()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared scrape pass
# ---------------------------------------------------------------------------


@dataclass
class ScrapePassResult:
    posts_scraped: int = 0
    comments_scraped: int = 0
    parse_successes: int = 0
    parse_failures: int = 0
    listing_backend: str = "none"
    comment_backend: str = "none"


def _print_backend_banner() -> None:
    if has_praw_credentials():
        console.print("[dim]Backend: PRAW OAuth[/dim]")
    else:
        console.print(
            "[dim]Backend: public JSON → Arctic Shift → RSS "
            "(set REDDIT_CLIENT_ID/SECRET for OAuth)[/dim]"
        )


def _run_scrape_pass(
    *,
    sort: str = "new",
    limit: int | None = None,
    comments: int | None = None,
    resume: bool = False,
    query: str | None = None,
    quiet: bool = False,
) -> ScrapePassResult:
    """Execute one scrape + comment-enrichment pass. Returns counters."""
    conn = get_connection()
    init_db(conn)
    result = ScrapePassResult()

    skip_ids: set[str] = set()
    if resume:
        skip_ids = get_scraped_post_ids(conn)
        if skip_ids and not quiet:
            console.print(
                f"[dim]Resume mode: skipping {len(skip_ids)} already-scraped posts[/dim]"
            )

    if not quiet:
        console.print(
            f"[bold cyan]Scraping r/{SUBREDDIT_NAME}[/bold cyan] "
            f"(sort={sort}, limit={limit or 'all'}"
            f"{f', query={query!r}' if query else ''})"
        )
        _print_backend_banner()

    scraped_posts: list[Post] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        disable=quiet,
    ) as progress:
        task = progress.add_task("Fetching posts...", total=limit)

        for post, parsed, _ in fetch_posts(
            sort=sort,
            limit=limit,
            skip_ids=skip_ids,
            query=query,
        ):
            professor: Professor | None = None
            if parsed is not None:
                result.parse_successes += 1
                professor = Professor(
                    id=parsed.professor_id,
                    last_name=parsed.last_name,
                    first_name=parsed.first_name,
                    campus=parsed.campus,
                )
            else:
                result.parse_failures += 1

            upsert_post_with_comments(conn, post, [], professor)
            scraped_posts.append(post)
            result.posts_scraped += 1

            progress.update(
                task,
                advance=1,
                description=(
                    f"[green]{result.posts_scraped}[/green] posts · "
                    f"[yellow]{result.parse_failures}[/yellow] unparsed"
                ),
            )

    enrich_count = 0
    if scraped_posts:
        enrich_count = min(len(scraped_posts), comments or len(scraped_posts))
        if not quiet:
            console.print(
                f"\n[bold cyan]Enriching[/bold cyan] top {enrich_count} posts with comments "
                f"[dim](~{enrich_count * 1.2:.0f}s at 1.2s/req)[/dim]"
            )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            disable=quiet,
        ) as progress:
            task = progress.add_task("Fetching comments...", total=enrich_count)
            comments_map = enrich_with_comments(scraped_posts, top_n=enrich_count)

            for post_id, comment_list in comments_map.items():
                post = next((p for p in scraped_posts if p.reddit_id == post_id), None)
                if post:
                    upsert_post_with_comments(conn, post, comment_list)
                    result.comments_scraped += len(comment_list)

                progress.update(
                    task,
                    advance=1,
                    description=(
                        f"[blue]{result.comments_scraped}[/blue] comments collected"
                    ),
                )

    conn.close()
    result.listing_backend, result.comment_backend = get_last_backends()
    return result


def _print_scrape_summary(result: ScrapePassResult, title: str = "Scrape Complete") -> None:
    console.print()
    summary = Table(title=title, show_header=False, border_style="cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Posts scraped", str(result.posts_scraped))
    summary.add_row("Comments collected", str(result.comments_scraped))
    summary.add_row("Titles parsed", f"{result.parse_successes} ✓")
    summary.add_row("Titles unparsed", f"{result.parse_failures} ✗")
    if result.posts_scraped > 0:
        rate = result.parse_successes / result.posts_scraped
        summary.add_row("Parse rate", f"{rate:.1%}")
    summary.add_row("Listing backend", result.listing_backend)
    summary.add_row("Comment backend", result.comment_backend)
    console.print(summary)

    if result.posts_scraped == 0:
        console.print(
            "[yellow]Warning:[/yellow] no posts fetched. "
            "Reddit may be blocking this IP — Arctic/RSS may also be down."
        )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_scrape(args: argparse.Namespace) -> None:
    """Scrape r/RateUPProfs posts and comments into SQLite."""
    result = _run_scrape_pass(
        sort=args.sort,
        limit=args.limit,
        comments=args.comments,
        resume=args.resume,
        query=args.query,
    )
    _print_scrape_summary(result)
    if result.posts_scraped == 0:
        sys.exit(2)


def cmd_scrape_all(args: argparse.Namespace) -> None:
    """Run progressive multi-pass scrape (sorts + subject queries) with resume."""
    console.print(
        f"[bold cyan]Progressive scrape[/bold cyan] of r/{SUBREDDIT_NAME} "
        f"({len(PROGRESSIVE_PASSES)} passes, resume={not args.no_resume})"
    )
    _print_backend_banner()

    totals = ScrapePassResult()
    scale = max(0.1, float(args.scale))

    for i, (sort, query, post_limit, comment_limit) in enumerate(PROGRESSIVE_PASSES, 1):
        pl = max(1, int(post_limit * scale))
        cl = max(0, int(comment_limit * scale))
        label = f"{sort}" + (f" q={query}" if query else "")
        console.print(f"\n[bold]Pass {i}/{len(PROGRESSIVE_PASSES)}[/bold] — {label} "
                      f"(limit={pl}, comments={cl})")

        result = _run_scrape_pass(
            sort=sort,
            limit=pl,
            comments=cl,
            resume=not args.no_resume,
            query=query,
            quiet=False,
        )
        totals.posts_scraped += result.posts_scraped
        totals.comments_scraped += result.comments_scraped
        totals.parse_successes += result.parse_successes
        totals.parse_failures += result.parse_failures
        totals.listing_backend = result.listing_backend
        totals.comment_backend = result.comment_backend

        console.print(
            f"  → +{result.posts_scraped} posts, +{result.comments_scraped} comments "
            f"[{result.listing_backend}/{result.comment_backend}]"
        )

    _print_scrape_summary(totals, title="Progressive Scrape Complete")
    cmd_stats(argparse.Namespace())

    if args.export:
        out = Path(args.export)
        count = export_professors(out, with_crs=args.crs)
        console.print(f"[green]✓[/green] Exported {count} professors → [bold]{out}[/bold]")

    if totals.posts_scraped == 0:
        sys.exit(2)


def cmd_reparse(args: argparse.Namespace) -> None:
    """Re-run the title parser on unparsed (or all) posts already in the DB."""
    conn = get_connection()
    init_db(conn)

    if args.all:
        rows = list(
            conn.execute(
                """
                SELECT reddit_id, title FROM posts ORDER BY created_utc DESC
                """
            )
        )
    else:
        rows = get_unparsed_posts(conn)

    if not rows:
        console.print("[green]Nothing to reparse.[/green]")
        conn.close()
        return

    newly_parsed = 0
    still_unparsed = 0

    with conn:
        for row in rows:
            parsed = parse_title(row["title"])
            if parsed is None:
                still_unparsed += 1
                if args.all:
                    update_post_parse(conn, row["reddit_id"], None, None, None)
                continue

            upsert_professor(
                conn,
                Professor(
                    id=parsed.professor_id,
                    last_name=parsed.last_name,
                    first_name=parsed.first_name,
                    campus=parsed.campus,
                ),
            )
            update_post_parse(
                conn,
                row["reddit_id"],
                parsed.campus,
                parsed.course,
                parsed.professor_id,
            )
            newly_parsed += 1

    conn.close()
    console.print(
        f"[green]✓[/green] Reparsed {len(rows)} titles → "
        f"[bold]{newly_parsed}[/bold] parsed, {still_unparsed} still unparsed"
    )
    cmd_stats(argparse.Namespace())


def cmd_enrich(args: argparse.Namespace) -> None:
    """Backfill comments for DB posts that have none stored yet."""
    conn = get_connection()
    init_db(conn)
    rows = get_posts_missing_comments(conn, limit=args.limit)
    if not rows:
        console.print("[green]All posts already have comments (or DB is empty).[/green]")
        conn.close()
        return

    console.print(
        f"[bold cyan]Enriching[/bold cyan] {len(rows)} posts missing comments"
    )
    _print_backend_banner()

    posts = [
        Post(
            reddit_id=r["reddit_id"],
            title=r["title"],
            campus=r["campus"],
            course=r["course"],
            professor_id=r["professor_id"],
            url=r["url"],
            score=r["score"],
            num_comments=r["num_comments"],
            created_utc=r["created_utc"],
            author=r["author"],
            selftext=r["selftext"] or "",
            scraped_at=r["scraped_at"],
        )
        for r in rows
    ]

    comments_scraped = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching comments...", total=len(posts))
        comments_map = enrich_with_comments(posts, top_n=len(posts))
        for post in posts:
            comment_list = comments_map.get(post.reddit_id, [])
            upsert_post_with_comments(conn, post, comment_list)
            comments_scraped += len(comment_list)
            progress.update(
                task,
                advance=1,
                description=f"[blue]{comments_scraped}[/blue] comments collected",
            )

    conn.close()
    _, comment_backend = get_last_backends()
    console.print(
        f"[green]✓[/green] Collected {comments_scraped} comments "
        f"(backend={comment_backend})"
    )
    cmd_stats(argparse.Namespace())


def cmd_export(args: argparse.Namespace) -> None:
    """Export scraped data to JSON."""
    output = Path(args.output)
    crs_path = Path(args.crs_db) if getattr(args, "crs_db", None) else None

    if args.format == "full":
        count = export_full(output)
        console.print(
            f"[green]✓[/green] Exported {count} posts → [bold]{output}[/bold]"
        )
    elif args.format == "professors":
        count = export_professors(output, with_crs=args.crs, crs_db_path=crs_path)
        crs_note = " (with CRS enrichment)" if args.crs else ""
        console.print(
            f"[green]✓[/green] Exported {count} professors{crs_note} → [bold]{output}[/bold]"
        )


def cmd_stats(args: argparse.Namespace) -> None:
    """Print database statistics."""
    conn = get_connection()
    init_db(conn)
    stats = get_stats(conn)
    conn.close()

    table = Table(title="Database Statistics", border_style="cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total posts", str(stats["total_posts"]))
    table.add_row("Total comments", str(stats["total_comments"]))
    table.add_row("Unique professors", str(stats["total_professors"]))
    table.add_row("Parsed posts", str(stats["parsed_posts"]))
    table.add_row("Unparsed posts", str(stats["unparsed_posts"]))
    table.add_row("Parse rate", stats["parse_rate"])

    console.print(table)

    if stats["campuses"]:
        campus_table = Table(title="Posts by Campus", border_style="yellow")
        campus_table.add_column("Campus", style="bold")
        campus_table.add_column("Posts", justify="right")
        for campus, count in stats["campuses"].items():
            campus_table.add_row(campus, str(count))
        console.print(campus_table)


def cmd_match(args: argparse.Namespace) -> None:
    """Cross-reference scraped professors against the CRS instructor database."""
    from scraper.config import DB_PATH

    crs_path = Path(args.crs_db) if args.crs_db else None
    lookup = CRSLookup(crs_path)

    if not lookup.is_available:
        console.print(
            "[red bold]Error:[/red bold] CRS database not found at "
            f"[dim]{lookup._db_path}[/dim]\n"
            "Use --crs-db to specify the path."
        )
        return

    console.print("[bold cyan]Cross-referencing against CRS instructor database...[/bold cyan]")
    results = match_scraped_professors(DB_PATH, crs_path)

    if "error" in results:
        console.print(f"[red bold]Error:[/red bold] {results['error']}")
        return

    console.print(
        f"  CRS instructors: [bold]{results['crs_instructor_count']:,}[/bold]\n"
        f"  Scraped professors: [bold]{results['scraped_professor_count']}[/bold]\n"
    )

    matched = results["matched"]
    if matched:
        mt = Table(title=f"✓ Matched ({len(matched)})", border_style="green")
        mt.add_column("Reddit Name", style="bold")
        mt.add_column("CRS Name")
        mt.add_column("Campus")
        mt.add_column("Match", justify="center")
        mt.add_column("CRS Courses")
        for m in matched:
            confidence_icon = "🟢" if m["confidence"] == 1.0 else "🟡"
            courses_str = ", ".join(m["crs_courses"][:5])
            if len(m["crs_courses"]) > 5:
                courses_str += f" (+{len(m['crs_courses']) - 5})"
            mt.add_row(
                m["name"],
                m["crs_name"],
                m["campus"],
                f"{confidence_icon} {m['match_type']}",
                courses_str,
            )
        console.print(mt)

    ambiguous = results["ambiguous"]
    if ambiguous:
        at = Table(title=f"? Ambiguous ({len(ambiguous)})", border_style="yellow")
        at.add_column("Reddit Name", style="bold")
        at.add_column("Campus")
        at.add_column("Candidates")
        for a in ambiguous:
            candidates_str = "; ".join(
                f"{c['crs_name']} ({c['match_type']})"
                for c in a["candidates"][:3]
            )
            at.add_row(a["name"], a["campus"], candidates_str)
        console.print(at)

    unmatched = results["unmatched"]
    if unmatched:
        ut = Table(title=f"✗ Unmatched ({len(unmatched)})", border_style="red")
        ut.add_column("Reddit Name", style="bold")
        ut.add_column("Campus")
        for u in unmatched:
            ut.add_row(u["name"], u["campus"])
        console.print(ut)

    total = results["scraped_professor_count"]
    if total > 0:
        rate = len(matched) / total
        console.print(
            f"\n[bold]Match rate:[/bold] {len(matched)}/{total} ({rate:.0%})"
        )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="ruppscraper",
        description="Scrape r/RateUPProfs into structured data.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ---- scrape ----
    sp_scrape = subparsers.add_parser("scrape", help="Scrape posts and comments.")
    sp_scrape.add_argument(
        "--sort",
        choices=["new", "hot", "top", "rising"],
        default="new",
        help="Reddit sort method (default: new).",
    )
    sp_scrape.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max posts to fetch (default: all available).",
    )
    sp_scrape.add_argument(
        "--comments",
        type=int,
        default=None,
        help="Max posts to enrich with comments (default: all scraped).",
    )
    sp_scrape.add_argument(
        "--resume",
        action="store_true",
        help="Skip posts already in the database.",
    )
    sp_scrape.add_argument(
        "--query",
        default=None,
        help="Search query within the subreddit (uses Arctic Shift archive).",
    )
    sp_scrape.set_defaults(func=cmd_scrape)

    # ---- scrape-all (progressive) ----
    sp_all = subparsers.add_parser(
        "scrape-all",
        help="Progressive multi-pass scrape (sorts + subject queries) with resume.",
    )
    sp_all.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Scale all pass limits (e.g. 0.25 for a quick smoke run).",
    )
    sp_all.add_argument(
        "--no-resume",
        action="store_true",
        help="Do not skip already-scraped post IDs.",
    )
    sp_all.add_argument(
        "--export",
        default=None,
        help="Optional professors JSON path to write after scraping.",
    )
    sp_all.add_argument(
        "--crs",
        action="store_true",
        help="With --export, enrich professors JSON via CRS matcher.",
    )
    sp_all.set_defaults(func=cmd_scrape_all)

    # ---- reparse ----
    sp_reparse = subparsers.add_parser(
        "reparse",
        help="Re-run title parser on unparsed DB posts (applies parser upgrades).",
    )
    sp_reparse.add_argument(
        "--all",
        action="store_true",
        help="Reparse every post, not only unparsed ones.",
    )
    sp_reparse.set_defaults(func=cmd_reparse)

    # ---- enrich ----
    sp_enrich = subparsers.add_parser(
        "enrich",
        help="Backfill comments for posts that have none stored yet.",
    )
    sp_enrich.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max posts to enrich (default: 50).",
    )
    sp_enrich.set_defaults(func=cmd_enrich)

    # ---- export ----
    sp_export = subparsers.add_parser("export", help="Export data to JSON.")
    sp_export.add_argument(
        "--format",
        choices=["full", "professors"],
        default="full",
        help="Export format (default: full).",
    )
    sp_export.add_argument(
        "--output",
        "-o",
        default="output/export.json",
        help="Output file path (default: output/export.json).",
    )
    sp_export.add_argument(
        "--crs",
        action="store_true",
        help="Enrich professor export with matched CRS catalog data.",
    )
    sp_export.add_argument(
        "--crs-db",
        default=None,
        help="Path to CRS SQLite database (default: auto-detect sibling repo).",
    )
    sp_export.set_defaults(func=cmd_export)

    # ---- stats ----
    sp_stats = subparsers.add_parser("stats", help="Show database statistics.")
    sp_stats.set_defaults(func=cmd_stats)

    # ---- match ----
    sp_match = subparsers.add_parser(
        "match", help="Cross-reference professors against CRS instructor database."
    )
    sp_match.add_argument(
        "--crs-db",
        default=None,
        help="Path to CRS SQLite database (default: auto-detect sibling repo).",
    )
    sp_match.set_defaults(func=cmd_match)

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(130)
    except Exception as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        logger.exception("Unhandled error")
        sys.exit(1)


if __name__ == "__main__":
    main()
