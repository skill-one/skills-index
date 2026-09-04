"""Fetch skills.sh data."""

from __future__ import annotations

import sys
import time

from .config import JSON, SKILLS_API, Record, is_github_source
from .http import POLITE_PAUSE, HttpError, build_client, get_json

# Give up after this many consecutive page failures (each page already
# retried internally by http.get_json). Prevents an infinite loop against
# a dead API while still tolerating isolated page errors.
MAX_CONSECUTIVE_PAGE_FAILURES = 3


def fetch_all(max_pages: int = 0, *, token: str = "") -> tuple[list[Record], list[int]]:
    """Fetch every page of skills.sh `all-time` rankings.

    `max_pages=0` (default) fetches until `hasMore` is false. A page that
    fails after its internal retries is skipped and recorded; fetching stops
    only after `MAX_CONSECUTIVE_PAGE_FAILURES` consecutive failures.

    Returns ``(skills, failed_page_numbers)``.
    """
    client = build_client(token)
    out: list[Record] = []
    failed: list[int] = []
    consecutive_failures = 0
    page = 0
    while max_pages == 0 or page < max_pages:
        try:
            data = get_json(client, f"{SKILLS_API}/{page}")
        except HttpError as exc:
            consecutive_failures += 1
            failed.append(page)
            print(f"  [skip] page {page}: {exc}", file=sys.stderr)
            if consecutive_failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                print(
                    f"  [abort] {MAX_CONSECUTIVE_PAGE_FAILURES} consecutive page "
                    "failures; stopping",
                    file=sys.stderr,
                )
                break
            page += 1
            time.sleep(POLITE_PAUSE)
            continue
        consecutive_failures = 0
        batch = data.get("skills", [])
        out.extend(batch)
        print(
            f"page {page}: +{len(batch)} skills, "
            f"hasMore={data.get('hasMore')}, total={data.get('total')}",
            file=sys.stderr,
        )
        if not data.get("hasMore"):
            break
        page += 1
        time.sleep(POLITE_PAUSE)
    return out, failed


def filter_github(skills: list[Record]) -> tuple[list[Record], int]:
    """Keep only GitHub-sourced skills, passing all fields through verbatim."""
    kept = [s for s in skills if is_github_source(str(s.get("source", "")))]
    return kept, len(skills) - len(kept)


def run_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list[Record], dict[str, JSON]]:
    """Fetch skills.sh data and keep GitHub sources.

    Returns ``(skills, summary)``; the skills.sh fields travel verbatim
    (source / skillId / name / installs / weeklyInstalls / isOfficial).
    Nothing is written: `update` passes the records to `scan` in memory.
    """
    raw, failed_pages = fetch_all(max_pages, token=token)
    if failed_pages:
        print(
            f"fetch done with {len(failed_pages)} skipped page(s): {failed_pages}",
            file=sys.stderr,
        )
    skills, dropped = filter_github(raw)
    print(f"filtered non-GitHub sources: dropped {dropped}, kept {len(skills)}", file=sys.stderr)
    summary = {
        "raw_skills": len(raw),
        "kept_github": len(skills),
        "dropped_non_github": dropped,
        "failed_pages": failed_pages,
    }
    return skills, summary
