"""GitHub surface: repo metadata (REST) and per-skill enrichment (git clone).

Only the REST metadata endpoint (per-repo stars) and the git smart-HTTP
endpoint are used. Each repo gets one bare partial clone
(`--filter=blob:limit` keeps SKILL.md-class blobs local while excluding
large assets; `--shallow-since` trims old history). Git protocol is not
billed against the REST API rate limit, keeping full scans well under the
Actions GITHUB_TOKEN quota (1000 req/h) and any personal PAT (5000 req/h).
"""

from __future__ import annotations

import datetime
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml

from .config import JSON
from .http import HttpError, get_json, new_github_client

# Blobs at or under this size arrive with the clone, so SKILL.md frontmatter
# can be read locally without per-blob lazy fetches; larger blobs (assets,
# binaries) are excluded and simply never needed.
CLONE_BLOB_LIMIT = 65536

# Initial history window. Skill dirs with no commit inside it trigger a one
# time `git fetch --unshallow` for that repo, so every published date is
# exact regardless of window.
SHALLOW_SINCE = "1.year.ago"

# Hard cap per git invocation; a hung clone fails the repo for this run.
GIT_TIMEOUT = 300.0


def _repo_info(source: str, *, client: httpx.Client | None = None) -> int:
    """Return the star count for `source`."""
    owner, repo = _split(source)
    c = client or new_github_client()
    data = get_json(c, f"/repos/{owner}/{repo}")
    return int(data.get("stargazers_count") or 0)


def _split(source: str) -> tuple[str, str]:
    owner, repo = source.split("/", 1)
    return owner, repo


def _run_git(args: list[str], *, cwd: str | None = None) -> str:
    """Run one git command and return its stdout (UTF-8, errors replaced)."""
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, timeout=GIT_TIMEOUT
    )
    if res.returncode != 0:
        detail = res.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise RuntimeError(f"git {args[0]} failed: {detail}")
    return res.stdout.decode("utf-8", errors="replace")


def _head_exists(repo: str) -> bool:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=repo,
        capture_output=True,
        timeout=GIT_TIMEOUT,
    )
    return res.returncode == 0


def _to_utc_z(git_iso: str) -> str:
    """Normalize a git ISO-8601 date (`+02:00` offset) to UTC `...Z`."""
    dt = datetime.datetime.fromisoformat(git_iso.strip())
    return dt.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _collect(repo: str, wanted: set[str]) -> tuple[list[JSON], bool] | None:
    """Extract per-skill rows from a cloned repo.

    Candidates are tree paths of the form `<any prefix>/<skillId>/SKILL.md`
    (a repo-root SKILL.md has no parent directory and never matches), sorted
    lexicographically with the FIRST match per skillId winning. Each row
    carries the repo-relative skill directory `path`, the frontmatter
    `description` (empty when absent), and `lastCommitAt` — the committer
    date of the most recent commit touching that directory, normalized to
    UTC. skillIds with no match are simply absent.

    Returns ``(rows, complete)``. ``complete`` is False when the clone is
    shallow and some row's commit sits on the shallow boundary: git grafts
    treat the boundary commit as a root commit that "created" every surviving
    path, so such a `lastCommitAt` may be fabricated — the caller deepens
    once and recomputes. Returns None if a wanted directory has no visible
    commit at all (same remedy).
    """
    boundary: set[str] = set()
    shallow_file = Path(repo) / "shallow"
    if shallow_file.exists():
        boundary = set(shallow_file.read_text(encoding="utf-8").split())
    paths = _run_git(["ls-tree", "-r", "--name-only", "HEAD"], cwd=repo).splitlines()
    first: dict[str, str] = {}
    for rel in sorted(paths):
        if rel.endswith("/SKILL.md"):
            d = rel[: -len("/SKILL.md")]
            sid = d.rsplit("/", 1)[-1]
            if sid in wanted:
                first.setdefault(sid, d)

    rows: list[JSON] = []
    complete = True
    for sid, d in sorted(first.items()):
        out = _run_git(["log", "-1", "--format=%H %cI", "--", d], cwd=repo).strip()
        if not out:
            return None
        sha, iso = out.split(" ", 1)
        if sha in boundary:
            complete = False
        text = _run_git(["show", f"HEAD:{d}/SKILL.md"], cwd=repo)
        rows.append(
            {
                "skillId": sid,
                "path": d,
                "description": extract_description(text),
                "lastCommitAt": _to_utc_z(iso),
            }
        )
    return rows, complete


def _deepen(repo: str) -> None:
    """Unshallow a clone (no-op failure when already complete)."""
    subprocess.run(
        ["git", "fetch", "--unshallow", "origin"],
        cwd=repo,
        capture_output=True,
        timeout=GIT_TIMEOUT,
    )


def clone_skills(
    source: str,
    wanted: set[str],
    *,
    url: str | None = None,
    since: str = SHALLOW_SINCE,
) -> list[JSON]:
    """Locate every wanted skillId in one git clone; return per-skill rows.

    One bare partial clone per repo: `--filter=blob:limit` keeps small blobs
    (SKILL.md) local and excludes large assets, `--shallow-since` trims old
    history, `--no-local` forces the transport layer (local clones would
    bypass filters). Two fallbacks keep the result exact:
    - a repo with no commit inside the window fails the shallow request
      outright ("no commits selected") — retried once with full history;
      the failed shallow attempt transfers no objects, so this is cheap;
    - a repo with recent commits but a wanted skill directory untouched
      inside the window deepens once via `git fetch --unshallow`.
    skillIds with no match are simply absent; the caller decides what
    absence means. `url`/`since` are injectable for tests (local fixture
    repos, artificial windows).
    """
    clone_url = url or f"https://github.com/{source}.git"
    base_args = [
        "clone",
        "--bare",
        "--no-local",
        f"--filter=blob:limit={CLONE_BLOB_LIMIT}",
        "--single-branch",
    ]
    # One transient-failure retry: mid-transfer network hiccups (curl 18 et
    # al.) would otherwise silently drop repos from the scan.
    for attempt in range(2):
        try:
            return _clone_and_collect(clone_url, base_args, since, wanted)
        except RuntimeError:
            if attempt:
                raise
            print(f"  [retry] {source}: clone failed; retrying once", file=sys.stderr)
    raise AssertionError("unreachable")  # pragma: no cover


def _clone_and_collect(
    clone_url: str, base_args: list[str], since: str, wanted: set[str]
) -> list[JSON]:
    with tempfile.TemporaryDirectory(prefix="skills-index-") as tmp:
        try:
            _run_git([*base_args, f"--shallow-since={since}", clone_url, tmp])
        except RuntimeError as exc:
            if "shallow" not in str(exc):
                raise
            _run_git([*base_args, clone_url, tmp])  # full history instead

        for attempt in (0, 1):
            if not _head_exists(tmp):
                if attempt:
                    return []  # repo has no commits at all
            else:
                res = _collect(tmp, wanted)
                if res is not None:
                    rows, complete = res
                    if complete:
                        return rows
            _deepen(tmp)
        raise RuntimeError("history incomplete after unshallow")


def parse_frontmatter(markdown: str) -> dict[str, JSON]:
    """Return the YAML frontmatter of a SKILL.md as a dict (empty if absent)."""
    text = markdown.lstrip("\ufeff").lstrip()
    if not text.startswith("---"):
        return {}
    body = text[3:].lstrip("\n")
    parts = body.split("\n---", 1)
    if len(parts) < 2:
        return {}
    try:
        data = yaml.safe_load(parts[0]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_description(markdown: str) -> str:
    """Return the `description` from a SKILL.md YAML frontmatter (empty if absent)."""
    desc = parse_frontmatter(markdown).get("description")
    return str(desc).strip() if desc else ""


def _is_missing_repo(exc: Exception) -> bool:
    """True if the failure is a definitive 404 (repo deleted / renamed / private).

    `get_json` wraps such responses as ``HttpError`` with ``status=404``;
    other failures (network errors, 5xx, rate limits) are not definitive.
    """
    return isinstance(exc, HttpError) and exc.status == 404


def get_repo_metas(
    sources: list[str],
    *,
    client: httpx.Client | None = None,
    max_workers: int = 8,
) -> tuple[dict[str, int], set[str]]:
    """Concurrently fetch the star count for many repos.

    Returns ``(metas, missing)``:
    - `metas` maps the repos whose metadata was fetched successfully;
    - `missing` contains the sources whose repo is definitively gone (404).

    Other failures are reported and skipped. Network-bound, so concurrent
    fetches materially cut wall-clock time on large source sets.
    """
    if not sources:
        return {}, set()
    client = client or new_github_client()

    def work(source: str) -> tuple[str, Exception | int]:
        try:
            return source, _repo_info(source, client=client)
        except Exception as exc:  # noqa: BLE001
            return source, exc

    out: dict[str, int] = {}
    missing: set[str] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for source, res in ex.map(work, list(sources)):
            if isinstance(res, Exception):
                if _is_missing_repo(res):
                    missing.add(source)
                    print(f"  [gone] {source}: repo not found (404)", file=sys.stderr)
                else:
                    print(
                        f"  [skip] {source}: meta fetch failed - {res}",
                        file=sys.stderr,
                    )
                continue
            out[source] = res
    return out, missing


