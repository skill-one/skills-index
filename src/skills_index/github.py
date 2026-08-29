"""GitHub API surface: repo metadata and SKILL.md content.

Only the REST metadata endpoint (per-repo pushed_at / default_branch) and the
codeload tarball endpoint are used. Repositories are never cloned; each repo's
SKILL.md files are read from a single tarball download, which is NOT billed
against the REST API rate limit -- keeping full scans well under the Actions
GITHUB_TOKEN quota (1000 req/h) and any personal PAT (5000 req/h).
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx
import yaml

from .config import HIDDEN_FRONTMATTER_MARKERS, JSON, is_internal_skill_path
from .http import HttpError, get_json, new_github_client

# codeload serves archive downloads and is not part of the REST API rate limit.
CODELOAD = "https://codeload.github.com"


def _repo_info(source: str, *, client: httpx.Client | None = None) -> tuple[str, str, int]:
    """Return (pushed_at, default_branch, stars) for `source`."""
    owner, repo = _split(source)
    c = client or new_github_client()
    data = get_json(c, f"/repos/{owner}/{repo}")
    pushed = data.get("pushed_at") or data.get("updated_at") or ""
    branch = str(data.get("default_branch", "main"))
    stars = int(data.get("stargazers_count") or 0)
    return pushed, branch, stars


def _split(source: str) -> tuple[str, str]:
    owner, repo = source.split("/", 1)
    return owner, repo


def _git_blob_sha(content: bytes) -> str:
    """Return the git blob sha1 (`sha1("blob <len>\\0" + content)`).

    Identical to the blob sha GitHub exposes on trees/contents endpoints, so
    locally computed fingerprints stay comparable across runs and machines.
    """
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()


def _scan_repo(  # noqa: E501
    source: str, branch: str, *, client: httpx.Client
) -> tuple[dict[str, tuple[str, str]], dict[str, str], int]:
    """Download and scan a repo tarball; return (blobs, contents, filtered)."""
    owner, repo = _split(source)
    url = f"{CODELOAD}/{owner}/{repo}/tar.gz/{quote(branch, safe='')}"
    resp = client.get(url)
    resp.raise_for_status()
    return _parse_tarball(resp.content)


def get_skill_contents(  # noqa: E501
    source: str, branch: str = "HEAD", *, client: httpx.Client | None = None
) -> tuple[dict[str, tuple[str, str]], dict[str, str], int]:
    """Return (blobs, contents, filtered_count) for every public, valid SKILL.md.

    - blobs:    {basename: (relative_path, blob_sha)}
    - contents: {relative_path: raw SKILL.md text}

    Backed by a single codeload tarball download (not billed to the REST
    quota); the blob sha is computed locally with git's exact algorithm, so
    the repo-level fingerprints are comparable across runs and match the
    Trees API shas used by `get_tree_shas`. SKILL.md files on internal paths
    (tests/examples/...), marked non-public, or lacking the required
    frontmatter fields (`is_invalid_frontmatter`) are filtered out and
    counted in the third value. Nested SKILL.md files — those inside another
    skill's directory — are payload of that skill unit, not candidates, and
    are skipped without entering the counter (see `_outermost_skill_dirs`).
    """
    client = client or new_github_client()
    return _scan_repo(source, branch, client=client)


def get_tree_shas(
    source: str, branch: str, *, client: httpx.Client | None = None
) -> dict[str, str] | None:
    """Return {relative_path: blob_sha} for every public-path SKILL.md.

    One Trees API call (recursive) per repo — the sha values are identical to
    the locally computed `_git_blob_sha` fingerprints, so callers can compare
    them against the cached `meta.json` skillShas to decide whether any
    public SKILL.md changed since the last scan (e.g. a README-only push
    would show an unchanged set, and the tarball download can be skipped
    entirely). Internal paths (tests/examples/... — see
    `is_internal_skill_path`) are excluded so the comparison domain matches
    the cached fingerprint, which only covers public skills; nested SKILL.md
    dirs (payload of an enclosing skill unit, see `_outermost_skill_dirs`)
    are excluded for the same reason. SKILL.md files dropped by content-level
    filters (frontmatter markers, missing required fields) cannot be
    recognized from the tree alone, so repos carrying those simply never hit
    the pre-check (conservative: they fall through to the tarball).

    Returns `None` when the tree is truncated (>100k entries / 7MB response)
    or the request fails: the caller should fall back to downloading the
    tarball, which is always authoritative.
    """
    owner, repo = _split(source)
    c = client or new_github_client()
    data = get_json(c, f"/repos/{owner}/{repo}/git/trees/{quote(branch, safe='')}?recursive=1")
    if data.get("truncated"):
        print(f"  [tree] {source}: tree truncated; falling back to tarball")
        return None
    shas = {
        e["path"][: -len("/SKILL.md")]: e["sha"]
        for e in data.get("tree", [])
        if e.get("type") == "blob"
        and e.get("path", "").endswith("/SKILL.md")
    }
    return {
        d: shas[d]
        for d in _outermost_skill_dirs(shas)
        if not is_internal_skill_path(d)
    }


def _outermost_skill_dirs(dirs: Iterable[str]) -> list[str]:
    """Return the dirs whose SKILL.md is not nested inside another skill unit.

    A directory containing SKILL.md is a self-contained skill unit and claims
    its whole subtree: a SKILL.md below it is that unit's payload (a bundled
    template / example / asset), not an independent candidate — agents
    discover skills one level deep, so a nested SKILL.md could never be
    triggered as a skill of its own. Claims are structural and precede the
    content filters: a unit owns its subtree even when it is itself dropped
    by S2/S3/S4, so an invalid or hidden parent hides its nested files too.
    Sorting makes parents precede children, so one pass suffices. A
    repo-root SKILL.md never enters here (S1 does not collect it) and thus
    never claims anything.
    """
    claimed: list[str] = []
    out: list[str] = []
    for d in sorted(dirs):
        if any(d.startswith(c + "/") for c in claimed):
            continue
        claimed.append(d)
        out.append(d)
    return out


def _parse_tarball(  # noqa: E501
    raw: bytes,
) -> tuple[dict[str, tuple[str, str]], dict[str, str], int]:
    """Scan a repo tarball for every SKILL.md; return (blobs, contents, filtered).

    Non-public SKILL.md files are dropped before the basename-keyed dict is
    built, so a same-named test fixture can never shadow the real skill:
    internal paths (tests/examples/templates/... -- see `is_internal_skill_path`),
    non-public frontmatter markers (`is_nonpublic_frontmatter`), and files
    without the required `name` + `description` frontmatter
    (`is_invalid_frontmatter`) all count toward `filtered`. Nested SKILL.md
    files (inside another skill's directory) are payload of that skill unit:
    they are skipped before the filter chain and do not count toward
    `filtered` — they were never candidates (see `_outermost_skill_dirs`).

    The tarball has a top-level `<repo>-<sha>/` directory, which is stripped so
    `relative_path` is the path within the repo (as used elsewhere).
    """
    skill_members: dict[str, tarfile.TarInfo] = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)
            if len(parts) < 2:
                continue  # the top-level directory entry itself
            rel = parts[1]
            if not rel.endswith("/SKILL.md"):
                continue
            skill_members[rel[: -len("/SKILL.md")]] = member

        blobs: dict[str, tuple[str, str]] = {}
        contents: dict[str, str] = {}
        filtered = 0
        for skill_dir in _outermost_skill_dirs(skill_members):
            if is_internal_skill_path(skill_dir):
                filtered += 1
                continue
            f = tf.extractfile(skill_members[skill_dir])
            if f is None:
                continue
            data = f.read()
            text = data.decode("utf-8", errors="replace")
            if is_nonpublic_frontmatter(text) or is_invalid_frontmatter(text):
                filtered += 1
                continue
            blobs[skill_dir.rsplit("/", 1)[-1]] = (skill_dir, _git_blob_sha(data))
            contents[skill_dir] = text
    return blobs, contents, filtered


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


def is_nonpublic_frontmatter(markdown: str) -> bool:
    """True if the SKILL.md frontmatter explicitly opts out of public listing.

    HIDDEN_FRONTMATTER_MARKERS 中任一字段为真值（true / yes / 1），或
    `public: false`，视为作者声明该技能不对外发布。
    """
    data = parse_frontmatter(markdown)
    if not data:
        return False
    if data.get("public") is False:
        return True
    return any(data.get(marker) for marker in HIDDEN_FRONTMATTER_MARKERS)


def is_invalid_frontmatter(markdown: str) -> bool:
    """True if the SKILL.md lacks a usable `name` / `description` frontmatter.

    name + description 是 agent skills 规范的必备字段：两者都必须是非空字符串，
    技能才能被 agent 发现与触发。缺失任一字段、值为非字符串或纯空白、或没有
    （可解析的）frontmatter 的 SKILL.md 无法作为技能工作，视为无效文件而非
    公开技能（与 agents-skills 安装器的判定一致）。
    """
    data = parse_frontmatter(markdown)
    name = data.get("name")
    description = data.get("description")
    return not (
        isinstance(name, str)
        and name.strip()
        and isinstance(description, str)
        and description.strip()
    )


def _is_missing_repo(exc: Exception) -> bool:
    """True if the failure is a definitive 404 (repo deleted / renamed / private).

    `get_json` wraps such responses as ``HttpError`` with ``status=404``;
    other failures (network errors, 5xx, rate limits) are not definitive and
    must not drop a repo's cached data.
    """
    return isinstance(exc, HttpError) and exc.status == 404


def get_repo_metas(
    sources: list[str], *, client: httpx.Client | None = None, max_workers: int = 8
) -> tuple[dict[str, tuple[str, str, int]], set[str]]:
    """Concurrently fetch (pushed_at, default_branch, stars) for many repos.

    Returns ``(metas, missing)``:
    - `metas` maps the repos whose metadata was fetched successfully;
    - `missing` contains the sources whose repo is definitively gone (404,
      e.g. deleted or renamed), so callers can drop their stale data.

    Other failures are reported and skipped. Network-bound, so concurrent
    fetches materially cut wall-clock time on large source sets.
    """
    if not sources:
        return {}, set()
    client = client or new_github_client()

    def work(source: str) -> tuple[str, Exception | tuple[str, str, int]]:
        try:
            return source, _repo_info(source, client=client)
        except Exception as exc:  # noqa: BLE001
            return source, exc

    out: dict[str, tuple[str, str, int]] = {}
    missing: set[str] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for source, res in ex.map(work, list(sources)):
            if isinstance(res, Exception):
                if _is_missing_repo(res):
                    missing.add(source)
                    print(f"  [gone] {source}: repo not found (404)")
                else:
                    print(f"  [skip] {source}: meta fetch failed - {res}")
                continue
            out[source] = res
    return out, missing
