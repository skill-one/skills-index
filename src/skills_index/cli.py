"""Command-line entry point for skills-index."""

from __future__ import annotations

import argparse
import sys
import time

from .config import JSON
from .fetch import run_fetch
from .index import run_index
from .scan import scan_repositories


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skills-index",
        description="Aggregate skills.sh metadata and GitHub skill locations.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    fetch_p = sub.add_parser("fetch", help="fetch skills.sh data (no GitHub access)")
    fetch_p.add_argument("--pages", type=int, default=0, help="max pages (0 = all)")

    update_p = sub.add_parser(
        "update",
        help="fetch -> scan -> index in one run (the whole pipeline)",
    )
    update_p.add_argument("--pages", type=int, default=0, help="max fetch pages (0 = all)")
    update_p.add_argument(
        "--tag", default="", help="release tag recorded in index-meta.json"
    )

    return p


def _build_summary(
    fetch_sum: dict[str, JSON],
    scan_sum: dict[str, JSON],
    index_sum: dict[str, JSON],
    *,
    total: float,
    fetch: float,
    scan: float,
    index: float,
    pages: int,
    tag: str,
) -> str:
    """Render a Markdown run report printed to stdout and used as the Release body."""
    pages_str = "all" if not pages else str(pages)
    scope_str = "full refresh" if not pages else f"smoke test ({pages} page)"
    failed = fetch_sum.get("failed_pages") or []
    lines = [
        "## Run summary",
        "",
        f"- **Scope:** {scope_str}" + (f", tag `{tag}`" if tag else ""),
        f"- **Total time:** {total:.1f}s "
        f"(fetch {fetch:.1f}s / scan {scan:.1f}s / index {index:.1f}s)",
        "",
        "### Fetch (skills.sh)",
        f"- Pages fetched: `{pages_str}`",
        f"- Skills kept: `{fetch_sum.get('kept_github', 0)}` "
        f"of `{fetch_sum.get('raw_skills', 0)}` raw "
        f"(`{fetch_sum.get('dropped_non_github', 0)}` non-GitHub dropped)",
    ]
    if failed:
        lines.append(f"- Skipped pages (errors): `{len(failed)}` {failed}")
    lines += [
        "",
        "### Scan (GitHub repos, stateless full scan)",
        f"- Repos scanned: `{scan_sum.get('repos_total', 0)}`",
        f"- Skills located: `{scan_sum.get('skills_located', 0)}` "
        f"of `{scan_sum.get('skills_wanted', 0)}` wanted "
        f"(`{scan_sum.get('skills_not_found', 0)}` not in their repo)",
        f"- Repos gone (404): `{scan_sum.get('repos_gone', 0)}`; "
        f"failed: `{scan_sum.get('repos_failed', 0)}`",
        "",
        "### Index (merged)",
        f"- Confirmed & merged: `{index_sum.get('confirmed', 0)}`; "
        f"dropped (not in repo): `{index_sum.get('not_in_repo', 0)}`",
    ]
    if index_sum.get("deduped"):
        lines.append(f"- Cross-repo duplicates dropped: `{index_sum['deduped']}`")
    lines += [
        f"- **Final index entries: `{index_sum.get('index', 0)}`**",
        "",
        "### Artifacts",
        "- `index.jsonl` — merged skills index (also mirrored to the `dist` "
        "branch for CDN access)",
        "- `index-meta.json` — index metadata (formatVersion / generatedAt / "
        "counts / tag)",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "fetch":
        run_fetch(max_pages=args.pages)
        return 0

    if args.command == "update":
        t_start = time.monotonic()
        records, fetch_sum = run_fetch(max_pages=args.pages)
        t_fetch = time.monotonic()
        rows, scan_sum = scan_repositories(records)
        t_scan = time.monotonic()
        _, index_sum = run_index(records, rows, tag=args.tag)
        t_index = time.monotonic()

        t_total = t_index - t_start
        summary = _build_summary(
            fetch_sum,
            scan_sum,
            index_sum,
            total=t_total,
            fetch=t_fetch - t_start,
            scan=t_scan - t_fetch,
            index=t_index - t_scan,
            pages=args.pages,
            tag=args.tag,
        )
        print(summary, end="")
        return 0

    # argparse with required=True makes every other value unreachable.
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
