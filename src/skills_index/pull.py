"""Standalone helper: download a published snapshot locally for human review.

Unlike fetch / scan / index / update, this command never touches the pipeline's
``data/`` or ``cache/`` trees and never feeds the incremental chain — it is purely
read-from-the-remote. It resolves the repository's latest ``data-`` GitHub
Release (CI marks smoke-test releases as prerelease, so ``releases/latest``
always means the newest full snapshot), downloads the bundled ``data.tar.gz``
asset, extracts it under ``pulled/<tag>/`` mirroring the published layout, and
prints a short inspection report (index metadata, entry count, file sizes) so a
person can eyeball exactly what was published before pointing a tool at it.
"""

from __future__ import annotations

import re
import shutil
import sys
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

from .config import DATA_ASSET, GITHUB_REPO, JSON, PULLED_DIR
from .http import download_file, get_json, new_github_client
from .io_utils import read_json

if TYPE_CHECKING:
    import httpx

# `owner/repo` slug, or an SSH / HTTPS git remote pointing at github.com.
_REPO_SLUG = re.compile(r"^[^/\s]+/[^/\s]+$")
_REPO_FROM_URL = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def parse_repo(value: str) -> str:
    """Normalize a repo reference to `owner/repo`.

    Accepts a bare slug (`skill-one/skills-index`) or a git remote URL in SSH
    (`git@github.com:skill-one/skills-index.git`) or HTTPS form
    (`https://github.com/skill-one/skills-index.git`). Raises ValueError on
    anything that is not clearly a GitHub repo, so a typo fails loudly instead
    of silently hitting the wrong releases endpoint.
    """
    v = value.strip()
    m = _REPO_FROM_URL.search(v)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if _REPO_SLUG.match(v):
        return v
    raise ValueError(f"not a GitHub repo (want 'owner/repo' or a github.com URL): {value!r}")


def pick_download_url(release: JSON, asset_name: str = DATA_ASSET) -> str:
    """Return the download URL of `asset_name` from a Release payload.

    Prefers ``browser_download_url`` (publicly served by an object host, no
    auth needed) and falls back to the asset API ``url``. Raises KeyError when
    the named asset is absent so a wrong asset name is reported clearly.
    """
    for asset in release.get("assets") or []:
        if asset.get("name") == asset_name:
            url = asset.get("browser_download_url") or asset.get("url")
            if url:
                return str(url)
    names = [a.get("name") for a in (release.get("assets") or [])]
    raise KeyError(f"asset {asset_name!r} not found in release; available: {names}")


def _within(dest: Path, name: str) -> bool:
    """True if a tar member name resolves inside `dest` (no traversal / absolute)."""
    if name.startswith("/") or name.startswith("\\"):
        return False
    target = (dest / name).resolve()
    root = dest.resolve()
    return target == root or root in target.parents


def safe_extract(tar_path: Path, dest: Path) -> list[str]:
    """Extract only regular files/dirs from a tar.gz into `dest`, safely.

    Rejects any member whose path escapes `dest` (absolute paths or ``..``
    traversal) before writing, and refuses non-regular entries (symlinks,
    hardlinks, devices) rather than following them. Returns the relative paths of
    extracted files. The published ``data.tar.gz`` contains only plain files, so
    any exotic member is treated as tampering and aborts the extraction.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        members = tf.getmembers()
        # Validate every member first so a single unsafe/exotic path aborts the
        # whole extraction before a single byte is written.
        files: list[tarfile.TarInfo] = []
        for member in members:
            if not _within(dest, member.name):
                raise RuntimeError(f"refusing to extract unsafe path: {member.name!r}")
            if member.isdir():
                continue
            if not member.isfile():
                raise RuntimeError(f"refusing to extract non-regular member: {member.name!r}")
            files.append(member)
        # Manual write (no extractall) keeps this safe and warning-free on every
        # supported Python; directories materialize implicitly from file paths.
        extracted: list[str] = []
        for member in files:
            target = dest / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = tf.extractfile(member)
            target.write_bytes(handle.read() if handle is not None else b"")
            extracted.append(member.name)
    return extracted


def _human_size(n: int) -> str:
    """Format a byte count with binary units (1024-based)."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"  # pragma: no cover


def summarize(snapshot_dir: Path) -> dict[str, JSON]:
    """Read the extracted snapshot and print a human inspection report.

    Looks at the published ``data/index/`` files: reports index-meta.json
    (formatVersion / generatedAt / counts.total), the real line count of
    index.jsonl, and every file under ``data/`` with its size. Returns a summary
    dict for callers / tests.
    """
    data_dir = snapshot_dir / "data"
    index_jsonl = data_dir / "index" / "index.jsonl"
    index_meta = data_dir / "index" / "index-meta.json"

    meta: JSON = read_json(index_meta, default={}) or {}
    line_count = 0
    if index_jsonl.exists():
        with index_jsonl.open("r", encoding="utf-8") as f:
            line_count = sum(1 for line in f if line.strip())

    files = sorted(p for p in data_dir.rglob("*") if p.is_file()) if data_dir.exists() else []
    meta_total = (meta.get("counts") or {}).get("total") if meta else None

    print(f"[pull] snapshot at {snapshot_dir}")
    print(
        f"[pull] index-meta: formatVersion={meta.get('formatVersion', '?')} "
        f"generatedAt={meta.get('generatedAt', '?')} "
        f"counts.total={meta_total if meta_total is not None else '?'}"
    )
    if meta.get("distCommit"):
        print(f"[pull] distCommit={meta['distCommit']}")
    mismatch = (
        ""
        if meta_total is None or line_count == meta_total
        else f"  (⚠ != meta total {meta_total})"
    )
    print(f"[pull] index.jsonl lines: {line_count}{mismatch}")
    print(f"[pull] files under data/ ({len(files)}):")
    for p in files:
        print(f"    {p.relative_to(snapshot_dir)}  ({_human_size(p.stat().st_size)})")

    return {
        "meta": meta,
        "index_lines": line_count,
        "files": [str(p.relative_to(snapshot_dir)) for p in files],
    }


def run_pull(
    repo: str = GITHUB_REPO,
    *,
    dest_root: Path = PULLED_DIR,
    asset: str = DATA_ASSET,
    client: httpx.Client | None = None,
) -> dict[str, JSON]:
    """Download the latest release's data snapshot into ``pulled/<tag>/``.

    Resolves `repo` (slug or git URL) to ``owner/repo``, fetches its latest
    ``data-`` release, downloads the ``data.tar.gz`` asset into a per-tag
    directory, safely extracts it, and prints an inspection report. The tag
    directory is wiped first so a re-pull is always a faithful, complete copy of
    that release. Returns the summary dict (tag, url, byte size, file count).
    """
    repo = parse_repo(repo)
    if client is None:
        client = new_github_client()

    print(f"[pull] resolving latest release of {repo} ...")
    release = get_json(client, f"/repos/{repo}/releases/latest")
    tag = str(release.get("tag_name", ""))
    if not tag:
        raise RuntimeError(f"release payload has no tag_name: {release!r}")
    url = pick_download_url(release, asset)
    print(f"[pull] latest: {tag}  (published {release.get('published_at', '?')})")

    snapshot_dir = dest_root / tag
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    archive = snapshot_dir / asset
    size = download_file(url, archive)
    print(f"[pull] downloaded {asset} ({_human_size(size)}) -> {archive}")

    extracted = safe_extract(archive, snapshot_dir)
    archive.unlink()
    print(f"[pull] extracted {len(extracted)} file(s), removed the archive")

    file_summary = summarize(snapshot_dir)
    summary: dict[str, JSON] = {
        "repo": repo,
        "tag": tag,
        "url": url,
        "bytes": size,
        "extracted_files": len(extracted),
        "snapshot_dir": str(snapshot_dir),
        **file_summary,
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    """Entry point for a direct ``python -m skills_index.pull`` invocation."""
    args = sys.argv[1:] if argv is None else list(argv)
    repo = args[0] if args else GITHUB_REPO
    try:
        run_pull(repo)
    except Exception as exc:  # noqa: BLE001
        print(f"[pull] failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
