"""Scan GitHub repositories for skills (incremental via pushed_at)."""

from __future__ import annotations

import datetime
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .cache import RepoCache
from .config import (
    BY_SOURCE_DIR,
    FETCHED_SKILLS,
    JSON,
    MAX_SKILL_COUNT,
    SCANNED_REPOS,
    dir_to_source,
    iter_repo_dirs,
    source_to_dir,
)
from .github import (
    extract_description,
    get_repo_metas,
    get_skill_contents,
    get_tree_shas,
)
from .http import new_github_client
from .io_utils import read_jsonl, write_jsonl

# Concurrency for repo-level scanning. GitHub requests are I/O-bound, so a
# thread pool overlaps network waits across repos. Capped at 8 to stay friendly
# to GitHub's secondary per-token concurrency limits; the rate-limit-aware
# backoff in http.py absorbs any 429/403 spikes this may trigger.
SCAN_WORKERS = 8


def build_skill_records(
    blobs: dict[str, tuple[str, str]],
    contents: dict[str, str],
) -> list[JSON]:
    """Build a repo's scanned.jsonl records from its tarball, sorted by path.

    The tarball is already downloaded and parsed by `get_skill_contents`, so
    every description is extracted locally — no per-file network fetches and
    no sha-based subsetting needed (that only paid off when each SKILL.md was
    fetched individually from the blob API).
    """
    return [
        {"path": path, "description": extract_description(contents.get(path, ""))}
        for _name, (path, _sha) in sorted(blobs.items(), key=lambda kv: kv[1][0])
    ]


def _scan_one_repo(
    dir_name: str,
    *,
    force: bool,
    now: str,
    base_dir: Path,
    metas: dict[str, tuple[str, str, int]],
    missing: set[str],
    client: httpx.Client,
    counters: dict[str, int],
    counters_lock: threading.Lock,
    max_skill_count: int | None = None,
) -> JSON | None:
    """Scan a single repo dir. Returns its summary record, or None to skip.

    Pure function of its arguments plus the on-disk cache. Shared mutable
    state is limited to `counters` (guarded by `counters_lock`) and the
    rate-limited http client (thread-safe).
    """
    effective_max = MAX_SKILL_COUNT if max_skill_count is None else max_skill_count
    source = dir_to_source(dir_name)
    cache = RepoCache.load(base_dir / dir_name, source)

    def bump(key: str, n: int = 1) -> None:
        with counters_lock:
            counters[key] = counters.get(key, 0) + n

    if source not in metas:
        if source in missing:
            # Repo is definitively gone (404): drop its stale scan data so it
            # (and its skills) no longer appear in the index.
            bump("gone")
            print(f"  [gone] {source}: repo not found (404); removing stale data")
            cache.remove()
        else:
            bump("failed")
        return None
    pushed, branch, stars = metas[source]

    if not force:
        # Tombstoned mirror (loser of a previous run's fingerprint dedup):
        # while neither repo has pushed again, the two skill trees are still
        # identical, so skip entirely — no tarball download. A push on either
        # side, or the winner disappearing, invalidates the tombstone and
        # falls through to a full rescan; the dedup pass then re-adjudicates
        # (re-tombstone or keep).
        if cache.status == "tombstoned":
            winner = str(cache.meta.get("dedupedInto") or "")
            winner_meta = metas.get(winner)
            if (
                winner_meta is not None
                and winner_meta[0] == cache.meta.get("winnerPushedAt")
                and pushed == cache.pushed_at
            ):
                bump("skipped")
                print(f"  [dedup-skip] {source}: still a mirror of {winner}")
                return None

        # Repos excluded by the skillCount cap stay excluded without any
        # tarball download while their pushedAt is unchanged and the cached
        # count is still above the (current) cap. A push may bring the repo
        # back under the cap, so it triggers a full re-adjudication below.
        if (
            cache.status == "filtered"
            and effective_max > 0
            and cache.skill_count > effective_max
            and cache.pushed_at == pushed
        ):
            bump("filtered")
            bump("filtered_high_skill")
            print(
                f"  [high-skill] {source}: {cache.skill_count} > {effective_max} (cached)"
            )
            return None

        if cache.status == "ok" and not cache.schema_stale and cache.has_data:
            if cache.pushed_at == pushed:
                # Cached repos stay subject to the skillCount cap so
                # aggregator repos cannot sneak back in via a lowered cap.
                if effective_max > 0 and cache.skill_count > effective_max:
                    bump("filtered")
                    bump("filtered_high_skill")
                    print(f"  [high-skill] {source}: {cache.skill_count} > {effective_max}")
                    cache.write_filtered(
                        pushed=pushed, now=now, skill_count=cache.skill_count
                    )
                    return None
                bump("skipped")
                print(f"  [skip] {source}: pushed_at unchanged ({pushed})")
                # Cached repos still join the summary; refresh stars /
                # lastScanned so the summary and the next run's comparison
                # always work from fresh values.
                cache.refresh(pushed=pushed, stars=stars, now=now)
                return cache.summarize()

            # Trees pre-check (warm cache only): a changed pushedAt does not
            # necessarily mean the SKILL.md files changed. One Trees API call
            # (shas from the same source as the local fingerprint) compares
            # the repo's current public SKILL.md blob shas against the cache;
            # on a full match (e.g. a README-only push) the tarball download
            # is skipped and only the meta timestamps are refreshed. A cold
            # cache (first scan) or a truncated tree goes straight to the
            # tarball path (get_tree_shas returns None).
            if cache.skill_shas:
                try:
                    tree_shas = get_tree_shas(source, branch, client=client)
                except Exception as exc:
                    print(f"  [tree] {source}: tree pre-check failed - {exc}; fetching tarball")
                    tree_shas = None
                if tree_shas is not None and tree_shas == cache.skill_shas:
                    bump("tree_skipped")
                    print(f"  [tree-skip] {source}: skills unchanged since {pushed}")
                    cache.refresh(pushed=pushed, stars=stars, now=now)
                    return cache.summarize()

    try:
        blobs, contents, filtered_nonpublic = get_skill_contents(
            source, branch, client=client
        )
    except Exception as exc:
        print(f"  [skip] {source}: scan failed - {exc}")
        bump("failed")
        return None
    if filtered_nonpublic:
        bump("skills_filtered_nonpublic", filtered_nonpublic)

    skills = build_skill_records(blobs, contents)

    # Repos whose skill count exceeds the cap (e.g. aggregator/awesome-list
    # repos) never enter the index: tombstone the cache (keeping the count
    # fingerprint) instead of deleting it, so later runs skip the tarball
    # entirely until the repo pushes again.
    if effective_max > 0 and len(skills) > effective_max:
        bump("filtered")
        bump("filtered_high_skill")
        print(f"  [high-skill] {source}: {len(skills)} > {effective_max}; tombstoned")
        cache.write_filtered(pushed=pushed, now=now, skill_count=len(skills))
        return None

    cache.write_ok(
        branch=branch,
        pushed=pushed,
        stars=stars,
        now=now,
        skills=skills,
        skill_shas={path: sha for _name, (path, sha) in blobs.items()},
    )
    bump("updated")
    bump("new_skills", len(skills))
    filtered_note = (
        f", {filtered_nonpublic} non-public filtered" if filtered_nonpublic else ""
    )
    print(f"  [scan] {source}: {len(skills)} skills{filtered_note}")
    return cache.summarize()


def scan_repositories(
    *,
    force: bool = False,
    max_skill_count: int | None = None,
    base_dir: Path = BY_SOURCE_DIR,
) -> dict[str, JSON]:
    """Walk `base_dir`, skip unchanged repos by `pushed_at`, emit per-repo files.

    `max_skill_count` filters out repos with more than that many skills. When
    it is `None` (the default), `config.MAX_SKILL_COUNT` is used, so the filter
    applies in every invocation unless explicitly overridden.

    Returns a summary dict with counts for the run report.
    """
    client = new_github_client()
    # Second-precision UTC ("...Z"), consistent with GitHub's pushedAt format.
    now = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _t0 = time.monotonic()
    _meta_time = 0.0
    effective_max = MAX_SKILL_COUNT if max_skill_count is None else max_skill_count

    subdirs = iter_repo_dirs(base_dir)
    print(
        f"scanning by-source: {len(subdirs)} GitHub repo dirs"
        + (" (force)" if force else "")
        + f" (max-skill-count {effective_max})"
    )

    # Fetch all repo metadata concurrently (network-bound) before the loop.
    _tm = time.monotonic()
    sources = [dir_to_source(d) for d in subdirs]
    metas, missing = get_repo_metas(sources, client=client)
    _meta_time += time.monotonic() - _tm

    counters = {
        "skipped": 0,
        "updated": 0,
        "failed": 0,
        "gone": 0,
        "filtered": 0,
        "filtered_high_skill": 0,
        "deduped": 0,
        "tree_skipped": 0,
        "new_skills": 0,
        "total_skills": 0,
        "skills_filtered_nonpublic": 0,
    }
    counters_lock = threading.Lock()
    scanned: dict[str, JSON] = {}

    # Repo-level scanning is I/O-bound: overlap network waits across repos.
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        futures = [
            ex.submit(
                _scan_one_repo,
                dir_name,
                force=force,
                now=now,
                base_dir=base_dir,
                metas=metas,
                missing=missing,
                client=client,
                counters=counters,
                counters_lock=counters_lock,
                max_skill_count=effective_max,
            )
            for dir_name in subdirs
        ]
        for fut in futures:
            res = fut.result()  # propagate unexpected exceptions
            if res is not None:
                scanned[res["source"]] = res

    # Original scan order follows the fetch order (skills.sh ranking), i.e. the
    # order in which each source first appeared in fetched-skills.jsonl. Build a
    # stable, deduplicated source list from that file.
    fetch_order: dict[str, int] = {}
    for rec in read_jsonl(FETCHED_SKILLS):
        src = str(rec.get("source", "")).strip()
        if src and src not in fetch_order:
            fetch_order[src] = len(fetch_order)

    def _orig_key(r: JSON) -> tuple[int, str]:
        src = r.get("source", "")
        return (fetch_order.get(src, len(fetch_order)), src)

    # Keep every scanned repo, ordered by fetch order (sources not present in
    # the fetch file sink to the end, alphabetical as a tie-breaker).
    repos = sorted(scanned.values(), key=_orig_key)

    # 内容指纹去重：整棵技能树（path + blob sha）完全一致的仓库是未分叉的
    # fork / 镜像，每组仅保留星数最高者，其余墓碑化并移出汇总，避免重复
    # 技能进入 index。去重后的 repos 才是 index 步骤的真实输入。
    repos, deduped = _dedup_repos(repos, base_dir, now=now)
    counters["deduped"] = len(deduped)

    # `total_skills` counts every skill in every *valid* repo that survived the
    # scan (including unchanged repos whose cached scanned.jsonl was reused via
    # the incremental skip). This is the true size of the scan side that step 3
    # merges against — not just the skills re-downloaded this run.
    counters["total_skills"] = sum(len(r.get("skills", [])) for r in repos)

    # scanned-repos.jsonl is the raw scan order (fetch order); sorted views
    # (by stars / skillCount) are generated by CI at publish time.
    write_jsonl(SCANNED_REPOS, repos)
    print(
        f"scan done: skipped {counters['skipped']} unchanged, "
        f"tree-skipped {counters['tree_skipped']} skills-unchanged, "
        f"updated {counters['updated']}, failed {counters['failed']}, "
        f"gone {counters['gone']}, filtered {counters['filtered']} "
        f"(high-skill {counters['filtered_high_skill']}), "
        f"deduped {counters['deduped']}."
    )
    print(f"wrote {len(repos)} repos -> {SCANNED_REPOS.name} (scan order)")
    _total = time.monotonic() - _t0
    print(
        f"[timer] scan: total={_total:.1f}s "
        f"meta={_meta_time:.1f}s other={_total - _meta_time:.1f}s"
    )
    summary = {
        "repos_total": len(subdirs),
        "repos_skipped": counters["skipped"],
        "repos_updated": counters["updated"],
        "repos_failed": counters["failed"],
        "repos_gone": counters["gone"],
        "repos_filtered": counters["filtered"],
        "repos_filtered_high_skill": counters["filtered_high_skill"],
        "repos_deduped": counters["deduped"],
        "repos_tree_skipped": counters["tree_skipped"],
        "skills_scanned": counters["total_skills"],
        "skills_scanned_new": counters["new_skills"],
        "skills_filtered_nonpublic": counters["skills_filtered_nonpublic"],
    }
    return summary


def _content_fingerprint(shas: dict[str, str]) -> str:
    """Stable serialization of a repo's skill tree ({path: blob sha})."""
    return json.dumps(sorted(shas.items()), ensure_ascii=False)


def _dedup_repos(
    repos: list[JSON], base_dir: Path, *, now: str
) -> tuple[list[JSON], list[tuple[str, str]]]:
    """Drop mirror repos whose skill tree is byte-identical to another's.

    Two repos with the same {path: blob sha} map carry exactly the same
    SKILL.md files — an undiverged fork or copy. Within each identical group
    only the most-starred repo survives (fetch order breaks ties); the rest
    are removed from the summary and tombstoned via
    :meth:`RepoCache.write_tombstone` (scanned.jsonl deleted, meta.json
    replaced by a ``tombstoned`` marker), so their duplicate skills never
    reach index.jsonl and later runs skip them without any network I/O (until
    either repo pushes again, which invalidates the tombstone and triggers a
    rescan). Repos with no skills have no fingerprint and are never deduped.
    Returns ``(kept, [(loser, winner)])``.
    """
    pushed_at = {str(r.get("source", "")): r.get("pushedAt") for r in repos}
    groups: dict[str, list[JSON]] = {}
    for r in repos:
        source = str(r.get("source", ""))
        shas = RepoCache.load(base_dir / source_to_dir(source), source).skill_shas
        if not shas:
            continue
        groups.setdefault(_content_fingerprint(shas), []).append(r)

    losers: dict[str, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        # max 返回迭代顺序中的第一个最大值；group 保持 fetch 顺序，星数
        # 相同时 skills.sh 排名靠前者（更早出现）胜出。
        winner = max(group, key=lambda r: r.get("stars") or 0)
        for r in group:
            if r is not winner:
                losers[str(r["source"])] = str(winner["source"])
    if not losers:
        return repos, []

    for loser, winner in sorted(losers.items()):
        print(f"  [dedup] {loser}: identical skill tree to {winner}; tombstoned")
        loser_dir = base_dir / source_to_dir(loser)
        if loser_dir.exists():
            RepoCache.load(loser_dir, loser).write_tombstone(
                winner=winner,
                winner_pushed=str(pushed_at.get(winner) or ""),
                pushed=str(pushed_at.get(loser) or ""),
                now=now,
            )
    kept = [r for r in repos if str(r.get("source", "")) not in losers]
    return kept, sorted(losers.items())
