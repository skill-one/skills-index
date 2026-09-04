"""Locate every skills.sh-known skill in its GitHub repository (stateless).

Every run is a full scan: repo metadata (stars) is fetched for every source,
and every repo gets one bare partial git clone from which the wanted
skillIds are located (github.clone_skills), together with each skill
directory's true last-commit time. There is no cache to maintain — the
pipeline carries no memory between runs.
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from .config import JSON, Record, is_github_source
from .github import clone_skills, get_repo_metas
from .http import new_github_client

# Repo-level scanning is I/O-bound: overlap network waits across repos.
# Capped at 8 to stay friendly to GitHub's secondary per-token limits.
SCAN_WORKERS = 8


def wanted_by_source(fetched: list[Record]) -> dict[str, set[str]]:
    """Group the fetched skillIds by source, in fetch (ranking) order."""
    out: dict[str, set[str]] = {}
    for rec in fetched:
        source = str(rec.get("source", "")).strip()
        if is_github_source(source):
            out.setdefault(source, set()).add(str(rec.get("skillId", "")))
    return out


def scan_repositories(fetched: list[Record]) -> tuple[list[JSON], dict[str, JSON]]:
    """Locate and enrich every fetched skill; return per-skill rows.

    - the fetched records decide what exists; this step only confirms and
      enriches: one row per located skill — {source, skillId, path,
      description, lastCommitAt, stars} — in fetch order, sorted by skillId
      per repo;
    - fetched skills their repo does not contain are dropped (counted as
      not_found); repos gone (404) drop all their skills; transient repo
      failures skip the repo for this run. Nothing is ever invented.
    """
    wanted = wanted_by_source(fetched)
    wanted_total = sum(len(ids) for ids in wanted.values())
    client = new_github_client()
    metas, missing = get_repo_metas(list(wanted), client=client)
    print(f"scanning {len(wanted)} GitHub repos (stateless full scan)", file=sys.stderr)

    def work(source: str) -> list[JSON]:
        stars = metas[source]
        rows = clone_skills(source, wanted[source])
        return [{**row, "source": source, "stars": stars} for row in rows]

    skills: list[JSON] = []
    located = not_found = failed = 0
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        futures = {ex.submit(work, s): s for s in metas}
        for fut, source in futures.items():
            try:
                rows = fut.result()
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"  [skip] {source}: scan failed - {exc}", file=sys.stderr)
                continue
            located += len(rows)
            not_found += len(wanted[source]) - len(rows)
            skills.extend(rows)
    gone = len(missing)

    print(
        f"scan done: located {located}/{wanted_total} skills, "
        f"gone {gone}, failed {failed}",
        file=sys.stderr,
    )
    summary = {
        "repos_total": len(wanted),
        "repos_gone": gone,
        "repos_failed": failed,
        "skills_wanted": wanted_total,
        "skills_located": located,
        "skills_not_found": not_found,
    }
    return skills, summary
