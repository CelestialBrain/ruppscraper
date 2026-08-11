"""CLI entrypoint for the RateUPProfs scraper."""

from __future__ import annotations

import argparse
import sys
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

from scraper.config import SUBREDDIT_NAME
from scraper.database import (
    get_connection,
    get_scraped_post_ids,
    get_stats,
    init_db,
    upsert_post_with_comments,
)
from scraper.crs_matcher import CRSLookup, match_scraped_professors
from scraper.exporter import export_full, export_professors
from scraper.models import Professor
from scraper.reddit_client import enrich_with_comments, fetch_posts

console = Console()

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_scrape(args: argparse.Namespace) -> None:
    """Scrape r/RateUPProfs posts and comments into SQLite."""
    conn = get_connection()
    init_db(conn)

    # Resume support: skip already-scraped posts
    skip_ids: set[str] = set()
    if args.resume:
        skip_ids = get_scraped_post_ids(conn)
        if skip_ids:
            console.print(
                f"[dim]Resume mode: skipping {len(skip_ids)} already-scraped posts[/dim]"
            )

    console.print(
        f"[bold cyan]Scraping r/{SUBREDDIT_NAME}[/bold cyan] "
        f"(sort={args.sort}, limit={args.limit or 'all'})"
    )
    console.print("[dim]Using public .json endpoints (no OAuth required)[/dim]")

    # Counters
    posts_scraped = 0
    comments_scraped = 0
    parse_successes = 0
    parse_failures = 0

    # Phase 1: Fetch all post listings
    scraped_posts = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching posts...", total=args.limit)

        for post, parsed, _ in fetch_posts(
            sort=args.sort,
            limit=args.limit,
            skip_ids=skip_ids,
        ):
            # Build professor model if title parsed successfully
            professor: Professor | None = None
            if parsed is not None:
                parse_successes += 1
                professor = Professor(
                    id=parsed.professor_id,
                    last_name=parsed.last_name,
                    first_name=parsed.first_name,
                    campus=parsed.campus,
                )
            else:
                parse_failures += 1

            # Store post (comments empty for now)
            upsert_post_with_comments(conn, post, [], professor)
            scraped_posts.append(post)
            posts_scraped += 1

            progress.update(
                task,
                advance=1,
                description=(
                    f"[green]{posts_scraped}[/green] posts · "
                    f"[yellow]{parse_failures}[/yellow] unparsed"
                ),
            )

    # Phase 2: Enrich top posts with comments (rate-paced)
    if scraped_posts:
        enrich_count = min(len(scraped_posts), args.comments or len(scraped_posts))
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
        ) as progress:
            task = progress.add_task("Fetching comments...", total=enrich_count)

            comments_map = enrich_with_comments(scraped_posts, top_n=enrich_count)

            for post_id, comments in comments_map.items():
                # Find the post and its professor to re-upsert with comments
                post = next((p for p in scraped_posts if p.reddit_id == post_id), None)
                if post:
                    upsert_post_with_comments(conn, post, comments)
                    comments_scraped += len(comments)

                progress.update(
                    task,
                    advance=1,
                    description=(
                        f"[blue]{comments_scraped}[/blue] comments collected"
                    ),
                )

    conn.close()

    # Summary
    console.print()
    summary = Table(title="Scrape Complete", show_header=False, border_style="cyan")
    summary.add_column("Metric", style="bold")
    summary.add_column("Value", justify="right")
    summary.add_row("Posts scraped", str(posts_scraped))
    summary.add_row("Comments collected", str(comments_scraped))
    summary.add_row("Titles parsed", f"{parse_successes} ✓")
    summary.add_row("Titles unparsed", f"{parse_failures} ✗")
    if posts_scraped > 0:
        rate = parse_successes / posts_scraped
        summary.add_row("Parse rate", f"{rate:.1%}")
    console.print(summary)


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
    from pathlib import Path

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

    # Summary
    console.print(
        f"  CRS instructors: [bold]{results['crs_instructor_count']:,}[/bold]\n"
        f"  Scraped professors: [bold]{results['scraped_professor_count']}[/bold]\n"
    )

    # Matched table
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

    # Ambiguous table
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

    # Unmatched table
    unmatched = results["unmatched"]
    if unmatched:
        ut = Table(title=f"✗ Unmatched ({len(unmatched)})", border_style="red")
        ut.add_column("Reddit Name", style="bold")
        ut.add_column("Campus")
        for u in unmatched:
            ut.add_row(u["name"], u["campus"])
        console.print(ut)

    # Final summary
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
    sp_scrape.set_defaults(func=cmd_scrape)

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
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(130)
    except Exception as exc:
        console.print(f"[red bold]Error:[/red bold] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
