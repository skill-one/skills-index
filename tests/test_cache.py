"""Tests for the per-repo incremental cache (RepoCache, no network)."""

from __future__ import annotations

from pathlib import Path

from skills_index.cache import RepoCache
from skills_index.config import SCHEMA_VERSION
from skills_index.io_utils import read_json, read_jsonl


def _cache(tmp_path: Path, name: str = "owner__repo") -> RepoCache:
    return RepoCache.load(tmp_path / name, "owner/repo")


def test_load_missing_meta_yields_empty_cache(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    assert cache.status == ""
    assert cache.pushed_at == ""
    assert cache.skill_count == 0
    assert cache.skill_shas == {}
    assert not cache.has_data
    assert cache.schema_stale  # missing meta always counts as stale


def test_write_ok_roundtrip(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    skills = [{"path": "skills/a", "description": "A"}]
    cache.write_ok(
        branch="main",
        pushed="2024-01-01T00:00:00Z",
        stars=7,
        now="2024-06-01T00:00:00Z",
        skills=skills,
        skill_shas={"skills/a": "sha-a"},
    )

    assert cache.has_data
    assert not cache.schema_stale
    assert read_jsonl(cache.repo_dir / "scanned.jsonl") == skills
    meta = read_json(cache.repo_dir / "meta.json")
    assert meta == {
        "schemaVersion": SCHEMA_VERSION,
        "status": "ok",
        "source": "owner/repo",
        "branch": "main",
        "pushedAt": "2024-01-01T00:00:00Z",
        "stars": 7,
        "lastScanned": "2024-06-01T00:00:00Z",
        "skillCount": 1,
        "skillShas": {"skills/a": "sha-a"},
    }


def test_refresh_updates_bookkeeping_only(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.write_ok(
        branch="main",
        pushed="2024-01-01T00:00:00Z",
        stars=7,
        now="2024-06-01T00:00:00Z",
        skills=[{"path": "skills/a", "description": "A"}],
        skill_shas={"skills/a": "sha-a"},
    )

    cache.refresh(pushed="2024-02-01T00:00:00Z", stars=9, now="2024-06-02T00:00:00Z")

    meta = read_json(cache.repo_dir / "meta.json")
    assert meta["pushedAt"] == "2024-02-01T00:00:00Z"
    assert meta["stars"] == 9
    assert meta["skillShas"] == {"skills/a": "sha-a"}  # fingerprint untouched
    assert (cache.repo_dir / "scanned.jsonl").exists()  # data untouched


def test_write_filtered_tombstone_keeps_count_drops_data(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.write_ok(
        branch="main",
        pushed="2024-01-01T00:00:00Z",
        stars=7,
        now="2024-06-01T00:00:00Z",
        skills=[{"path": "skills/a", "description": "A"}],
        skill_shas={"skills/a": "sha-a"},
    )

    cache.write_filtered(
        pushed="2024-01-01T00:00:00Z", now="2024-06-01T00:00:00Z", skill_count=600
    )
    meta = read_json(cache.repo_dir / "meta.json")
    assert meta["status"] == "filtered"
    assert meta["skillCount"] == 600
    assert meta["pushedAt"] == "2024-01-01T00:00:00Z"
    assert "skillShas" not in meta
    assert not (cache.repo_dir / "scanned.jsonl").exists()

    cache.write_tombstone(
        winner="other/winner",
        winner_pushed="2024-01-02T00:00:00Z",
        pushed="2024-01-01T00:00:00Z",
        now="2024-06-01T00:00:00Z",
    )
    meta = read_json(cache.repo_dir / "meta.json")
    assert meta["status"] == "tombstoned"
    assert meta["dedupedInto"] == "other/winner"
    assert meta["winnerPushedAt"] == "2024-01-02T00:00:00Z"
    assert not (cache.repo_dir / "scanned.jsonl").exists()


def test_rewrites_purge_foreign_files(tmp_path: Path) -> None:
    """Files outside the cache contract (legacy per-dir copies) cannot
    survive a meta rewrite — any status transition cleans them up."""
    cache = _cache(tmp_path)
    cache.repo_dir.mkdir(parents=True)
    legacy = cache.repo_dir / "fetched.jsonl"
    legacy.write_text('{"skillId": "a"}\n')

    cache.write_filtered(pushed="2024-01-01T00:00:00Z", now="x", skill_count=1)

    assert not legacy.exists()
    assert (cache.repo_dir / "meta.json").exists()


def test_remove_drops_whole_repo_dir(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.write_ok(
        branch="main",
        pushed="p",
        stars=1,
        now="n",
        skills=[],
        skill_shas={},
    )

    cache.remove()

    assert not cache.repo_dir.exists()
    # Removing an absent dir is a no-op.
    cache.remove()
