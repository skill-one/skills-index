"""End-to-end test for concurrent repo scanning (no real network).

Mocks GitHub network calls and drives `scan_repositories` against a temp
`by-source` tree, asserting that concurrency produces correct, complete
per-repo artifacts and run-summary counts.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import skills_index.config as config
import skills_index.scan as scan_mod
from skills_index.io_utils import read_json, read_jsonl
from skills_index.scan import scan_repositories

OWNERS = {
    f"owner{i}/repo{i}": (f"2024-01-0{i}T00:00:0{i}Z", "main", i * 100)
    for i in range(1, 7)
}


def _make_by_source(base_dir: Path) -> None:
    """Create 6 repo dirs; mark the first 3 as up-to-date (cached)."""
    for i, (source, (pushed, _branch, _stars)) in enumerate(OWNERS.items(), start=1):
        repo_dir = base_dir / config.source_to_dir(source)
        repo_dir.mkdir(parents=True, exist_ok=True)
        # Every repo already has a previous cache. Repos 4-6 will be "stale"
        # (pushedAt mismatch) so they get rescanned; 1-3 stay up-to-date.
        prev_pushed = pushed if i <= 3 else "1999-01-01T00:00:00Z"
        (repo_dir / config.META_FILE).write_text(
            f'{{"schemaVersion": {config.SCHEMA_VERSION}, "status": "ok", '
            f'"pushedAt": "{prev_pushed}", "stars": {i * 100}, '
            f'"skillCount": 1, "skillTreeShas": {{}}}}'
        )
        (repo_dir / config.SCANNED_FILE).write_text(
            '{"path": "skills/a", "description": "cached"}\n'
        )


@pytest.fixture
def patched(monkeypatch, tmp_path):
    base_dir = tmp_path / "by-source"
    _make_by_source(base_dir)
    # Redirect the global scanned-repos summary into tmp. scan.py binds this
    # name at import time, so patch the attribute on that module.
    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)

    seen_threads: set[int] = set()
    lock = threading.Lock()

    def fake_metas(sources, *, client=None, max_workers=8):
        with lock:
            for _ in sources:
                seen_threads.add(threading.get_ident())
        return {s: OWNERS[s] for s in sources if s in OWNERS}, set()

    def fake_contents(source, branch, *, client=None):
        with lock:
            seen_threads.add(threading.get_ident())
        # (revs, contents, filtered): 每次重扫上报 1 个被过滤的技能。
        return (
            {"skills/a": f"rev-{source}"},
            {"skills/a": "---\ndescription: desc for a\n---\n"},
            1,
        )

    monkeypatch.setattr(scan_mod, "get_repo_metas", fake_metas)
    monkeypatch.setattr(scan_mod, "get_skill_contents", fake_contents)
    return base_dir, seen_threads, scanned_repos


def test_scan_runs_concurrently_and_marks_threads(patched):
    base_dir, seen_threads, scanned_repos = patched
    summary = scan_repositories(base_dir=base_dir)
    # At least 2 distinct threads did the GitHub work (proves concurrency).
    assert len(seen_threads) >= 2
    # repo metadata fetch + per-repo blob/desc work all happened off main thread.
    assert summary["repos_total"] == 6
    assert summary["repos_skipped"] == 3
    assert summary["repos_updated"] == 3
    assert summary["repos_failed"] == 0
    # skills_scanned counts every skill in every valid repo that survived the
    # scan, including unchanged repos whose cached scanned.jsonl was reused.
    assert summary["skills_scanned"] == 6
    assert summary["skills_scanned_new"] == 3
    # 只有重扫的仓库（4-6）上报过滤计数；skipped 仓库不重新解析 tarball。
    assert summary["skills_filtered"] == 3
    assert scanned_repos.exists()


def test_scan_writes_per_repo_artifacts(patched):
    base_dir, _seen, _sr = patched
    scan_repositories(base_dir=base_dir)
    # Stale repos (4-6) get rescanned -> fresh meta + scanned.jsonl.
    for i in range(4, 7):
        source = f"owner{i}/repo{i}"
        repo_dir = base_dir / config.source_to_dir(source)
        meta = read_json(repo_dir / config.META_FILE)
        assert meta["pushedAt"] == OWNERS[source][0]  # updated to new pushed
        assert meta["stars"] == OWNERS[source][2]  # stargazers persisted
        skills = read_jsonl(repo_dir / config.SCANNED_FILE)
        assert skills == [
            {"path": "skills/a", "rev": f"rev-{source}", "description": "desc for a"}
        ]
        # 基准取不到（Trees 请求失败）不阻塞扫描：空 map 会在下次推送时
        # 预检不命中而自然重录。
        assert meta["skillTreeShas"] == {}
    # Up-to-date repos (1-3) keep their cached description untouched.
    for i in range(1, 4):
        source = f"owner{i}/repo{i}"
        repo_dir = base_dir / config.source_to_dir(source)
        meta = read_json(repo_dir / config.META_FILE)
        assert meta["stars"] == OWNERS[source][2]
        skills = read_jsonl(repo_dir / config.SCANNED_FILE)
        assert skills == [{"path": "skills/a", "description": "cached"}]
    # The per-repo summary (scanned-repos.jsonl) records stars too.
    repos = read_jsonl(scan_mod.SCANNED_REPOS)
    assert len(repos) == 6
    for rec in repos:
        source = rec["source"]
        assert rec["stars"] == OWNERS[source][2]


def test_scan_force_rescans_everything(patched):
    base_dir, _seen, _sr = patched
    summary = scan_repositories(force=True, base_dir=base_dir)
    assert summary["repos_skipped"] == 0
    assert summary["repos_updated"] == 6
    assert summary["skills_scanned"] == 6
    assert summary["skills_scanned_new"] == 6


def test_scan_removes_stale_data_for_missing_repo(monkeypatch, tmp_path):
    base_dir = tmp_path / "by-source"
    ghost = "ghost/removed"
    repo_dir = base_dir / config.source_to_dir(ghost)
    repo_dir.mkdir(parents=True)
    # Stale cache from a previous run when the repo still existed.
    (repo_dir / config.META_FILE).write_text(
        f'{{"pushedAt": "2000-01-01T00:00:00Z", "schemaVersion": {config.SCHEMA_VERSION}}}'
    )
    (repo_dir / config.SCANNED_FILE).write_text(
        '{"path": "skills/a", "description": "stale"}\n'
    )
    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)
    # The repo is definitively gone (404): meta fetch returns it as missing.
    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: ({}, {ghost}),
    )

    def _fail(*args, **kwargs):  # noqa: ARG002
        raise AssertionError("must not scan a repo that is gone (404)")

    monkeypatch.setattr(scan_mod, "get_skill_contents", _fail)

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_total"] == 1
    assert summary["repos_gone"] == 1
    assert summary["repos_failed"] == 0
    assert not repo_dir.exists()  # stale scan data removed
    assert read_jsonl(scanned_repos) == []  # repo not recorded


def test_scan_dedups_identical_skill_trees(monkeypatch, tmp_path):
    """Repos whose entire skill tree is byte-identical (mirror / undiverged
    fork): only the most-starred one survives; the loser is tombstoned
    (scanned.jsonl deleted, meta.json replaced by a dedupedInto marker) and
    removed from the per-repo summary, so its duplicate skills never reach
    index.jsonl and later runs skip it entirely."""
    OWNERS = {
        "big/repo": ("2024-01-01T00:00:00Z", "main", 1000),
        "small/mirror": ("2024-01-01T00:00:00Z", "main", 10),
        "other/repo": ("2024-01-01T00:00:00Z", "main", 5),
    }
    base_dir = tmp_path / "by-source"
    for source in OWNERS:
        (base_dir / config.source_to_dir(source)).mkdir(parents=True)

    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)

    def fake_metas(sources, *, client=None, max_workers=8):
        return dict(OWNERS), set()

    def fake_contents(source, branch, *, client=None):
        if source == "other/repo":
            return {"skills/o": "rev-o"}, {"skills/o": "---\ndescription: O\n---\n"}, 0
        # big/repo 与 small/mirror：同一棵技能树（path + blob sha 完全一致）。
        return (
            {"skills/a": "rev-a", "skills/b": "rev-b"},
            {
                "skills/a": "---\ndescription: A\n---\n",
                "skills/b": "---\ndescription: B\n---\n",
            },
            0,
        )

    monkeypatch.setattr(scan_mod, "get_repo_metas", fake_metas)
    monkeypatch.setattr(scan_mod, "get_skill_contents", fake_contents)

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_total"] == 3
    assert summary["repos_deduped"] == 1
    # 幸存者是星数更高的 big/repo；镜像不进入汇总。
    assert [r["source"] for r in read_jsonl(scanned_repos)] == ["big/repo", "other/repo"]
    # 镜像被墓碑化：目录保留，scanned.jsonl 删除，meta.json 记录裁决依据。
    mirror_dir = base_dir / config.source_to_dir("small/mirror")
    meta = read_json(mirror_dir / config.META_FILE)
    assert meta["status"] == "tombstoned"
    assert meta["dedupedInto"] == "big/repo"
    assert meta["winnerPushedAt"] == "2024-01-01T00:00:00Z"
    assert meta["pushedAt"] == "2024-01-01T00:00:00Z"
    assert "skillTreeShas" not in meta  # 无过期指纹，无效化后的重扫必走 tarball
    assert not (mirror_dir / config.SCANNED_FILE).exists()
    # 仅统计幸存仓库的技能（big/repo 2 个 + other/repo 1 个）。
    assert summary["skills_scanned"] == 3


def test_scan_tombstoned_mirror_skipped_until_push(monkeypatch, tmp_path):
    """墓碑生命周期：两仓均无新推送时直接跳过（不下载 tarball）；镜像有
    推送则重新扫描，技能树仍与胜者全等则更新墓碑（新的 pushedAt 快照）。"""
    OWNERS = {
        "big/repo": ("2024-01-01T00:00:00Z", "main", 1000),
        "small/mirror": ("2024-01-01T00:00:00Z", "main", 10),
    }
    base_dir = tmp_path / "by-source"
    for source in OWNERS:
        (base_dir / config.source_to_dir(source)).mkdir(parents=True)
    mirror_dir = base_dir / config.source_to_dir("small/mirror")
    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)

    downloads = {"n": 0}

    def fake_metas(sources, *, client=None, max_workers=8):
        return {s: OWNERS[s] for s in sources}, set()

    def fake_contents(source, branch, *, client=None):
        downloads["n"] += 1
        return ({"skills/a": "rev-a"}, {"skills/a": "---\ndescription: A\n---\n"}, 0)

    monkeypatch.setattr(scan_mod, "get_repo_metas", fake_metas)
    monkeypatch.setattr(scan_mod, "get_skill_contents", fake_contents)

    # 第 1 轮：small/mirror 与 big/repo 指纹全等 -> 墓碑化。
    scan_repositories(base_dir=base_dir)
    assert read_json(mirror_dir / config.META_FILE)["dedupedInto"] == "big/repo"

    # 第 2 轮：两仓均无推送 -> 墓碑仓库被跳过，零 tarball 下载。
    downloads["n"] = 0
    summary = scan_repositories(base_dir=base_dir)
    assert summary["repos_skipped"] == 2
    assert summary["repos_updated"] == 0
    assert downloads["n"] == 0
    assert [r["source"] for r in read_jsonl(scanned_repos)] == ["big/repo"]

    # 第 3 轮：镜像有推送 -> 墓碑失效、重新扫描；仍全等 -> 更新墓碑快照。
    OWNERS["small/mirror"] = ("2024-02-01T00:00:00Z", "main", 10)
    summary = scan_repositories(base_dir=base_dir)
    assert downloads["n"] == 1  # 只有镜像被重扫（big/repo 走 pushed_at 跳过）
    assert summary["repos_updated"] == 1
    meta = read_json(mirror_dir / config.META_FILE)
    assert meta["dedupedInto"] == "big/repo"
    assert meta["pushedAt"] == "2024-02-01T00:00:00Z"
    assert meta["winnerPushedAt"] == "2024-01-01T00:00:00Z"
    assert [r["source"] for r in read_jsonl(scanned_repos)] == ["big/repo"]


def test_scan_tombstoned_mirror_resurrected_when_diverged(monkeypatch, tmp_path):
    """镜像分叉（技能树与胜者不再全等）后恢复正常收录，墓碑清除。"""
    OWNERS = {
        "big/repo": ("2024-01-01T00:00:00Z", "main", 1000),
        "small/mirror": ("2024-01-01T00:00:00Z", "main", 10),
    }
    base_dir = tmp_path / "by-source"
    for source in OWNERS:
        (base_dir / config.source_to_dir(source)).mkdir(parents=True)
    mirror_dir = base_dir / config.source_to_dir("small/mirror")
    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)

    downloads = {"n": 0, "diverged": False}

    def fake_metas(sources, *, client=None, max_workers=8):
        return {s: OWNERS[s] for s in sources}, set()

    def fake_contents(source, branch, *, client=None):
        downloads["n"] += 1
        if source == "big/repo" or not downloads["diverged"]:
            # 分叉前：两仓指纹全等；分叉后：仅胜者保持原树。
            return ({"skills/a": "rev-a"}, {"skills/a": "---\ndescription: A\n---\n"}, 0)
        # 镜像新增了一个技能：与胜者不再全等。
        return (
            {"skills/a": "rev-a", "skills/c": "rev-c"},
            {
                "skills/a": "---\ndescription: A\n---\n",
                "skills/c": "---\ndescription: C\n---\n",
            },
            0,
        )

    monkeypatch.setattr(scan_mod, "get_repo_metas", fake_metas)
    monkeypatch.setattr(scan_mod, "get_skill_contents", fake_contents)

    # 先墓碑化。
    scan_repositories(base_dir=base_dir)
    assert read_json(mirror_dir / config.META_FILE)["dedupedInto"] == "big/repo"

    # 镜像推送（分叉）-> 重新扫描并保留在汇总中。
    downloads["diverged"] = True
    OWNERS["small/mirror"] = ("2024-02-01T00:00:00Z", "main", 10)
    summary = scan_repositories(base_dir=base_dir)
    assert summary["repos_deduped"] == 0
    assert summary["repos_updated"] == 1
    meta = read_json(mirror_dir / config.META_FILE)
    assert "dedupedInto" not in meta
    assert (mirror_dir / config.SCANNED_FILE).exists()
    assert [r["source"] for r in read_jsonl(scanned_repos)] == ["big/repo", "small/mirror"]


def test_scan_dedup_no_skills_repo_never_deduped(monkeypatch, tmp_path):
    """Repos with zero skills have no fingerprint: two empty repos coexist."""
    OWNERS = {
        "a/empty": ("2024-01-01T00:00:00Z", "main", 100),
        "b/empty": ("2024-01-01T00:00:00Z", "main", 1),
    }
    base_dir = tmp_path / "by-source"
    for source in OWNERS:
        (base_dir / config.source_to_dir(source)).mkdir(parents=True)

    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", tmp_path / "scanned-repos.jsonl")

    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: (dict(OWNERS), set()),
    )
    monkeypatch.setattr(
        scan_mod,
        "get_skill_contents",
        lambda source, branch, *, client=None: ({}, {}, 0),
    )

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_deduped"] == 0
    assert summary["repos_updated"] == 2
    assert len(read_jsonl(scan_mod.SCANNED_REPOS)) == 2


def test_scan_tree_precheck_skips_tarball_on_skill_unchanged(monkeypatch, tmp_path):
    """Trees 预检：pushedAt 变了但所有 SKILL.md 的 blob sha 未变（如
    README-only push）→ 跳过 tarball 下载，刷新 meta 时间戳并复用缓存。"""
    OWNERS = {"owner/repo": ("2024-06-01T00:00:00Z", "main", 50)}
    base_dir = tmp_path / "by-source"
    repo_dir = base_dir / config.source_to_dir("owner/repo")
    repo_dir.mkdir(parents=True)
    # 上次扫描：pushedAt 较旧，skillTreeShas 是本次 tree 预检的比对基准。
    (repo_dir / config.META_FILE).write_text(
        f'{{"schemaVersion": {config.SCHEMA_VERSION}, "status": "ok", '
        f'"pushedAt": "2024-01-01T00:00:00Z", "stars": 50, "skillCount": 1, '
        f'"skillTreeShas": {{"skills/a": "tree-a"}}}}'
    )
    (repo_dir / config.SCANNED_FILE).write_text(
        '{"path": "skills/a", "description": "cached"}\n'
    )

    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)

    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: (dict(OWNERS), set()),
    )

    def fake_tree(source, branch, *, client=None):
        # pushedAt 变了，但技能目录的 tree sha 与缓存一致（README-only push）。
        return {"skills/a": "tree-a"}

    def _fail_tarball(*args, **kwargs):
        raise AssertionError("tarball must not be downloaded when tree pre-check hits")

    monkeypatch.setattr(scan_mod, "get_skill_tree_shas", fake_tree)
    monkeypatch.setattr(scan_mod, "get_skill_contents", _fail_tarball)

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_tree_skipped"] == 1
    assert summary["repos_updated"] == 0
    # meta 的 pushedAt 已刷新为最新（下轮直接走 [skip]，不再重查 tree）。
    meta = read_json(repo_dir / config.META_FILE)
    assert meta["pushedAt"] == "2024-06-01T00:00:00Z"
    # 技能记录复用缓存，未重新扫描。
    assert read_jsonl(repo_dir / config.SCANNED_FILE) == [
        {"path": "skills/a", "description": "cached"}
    ]
    assert summary["skills_scanned"] == 1


def test_scan_tree_precheck_mismatch_downloads_tarball(monkeypatch, tmp_path):
    """Trees 预检未命中（技能目录 tree sha 变了）→ 下载 tarball 全量扫描，
    并复用预检已经拿到的 tree sha 作为下轮基准（不再多打一次请求）。"""
    OWNERS = {"owner/repo": ("2024-06-01T00:00:00Z", "main", 50)}
    base_dir = tmp_path / "by-source"
    repo_dir = base_dir / config.source_to_dir("owner/repo")
    repo_dir.mkdir(parents=True)
    (repo_dir / config.META_FILE).write_text(
        f'{{"schemaVersion": {config.SCHEMA_VERSION}, "status": "ok", '
        f'"pushedAt": "2024-01-01T00:00:00Z", "stars": 50, "skillCount": 1, '
        f'"skillTreeShas": {{"skills/a": "tree-old"}}}}'
    )
    (repo_dir / config.SCANNED_FILE).write_text(
        '{"path": "skills/a", "rev": "t1-old", "description": "old"}\n'
    )

    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", tmp_path / "scanned-repos.jsonl")

    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: (dict(OWNERS), set()),
    )
    tree_calls = []

    def fake_tree(source, branch, *, client=None):
        tree_calls.append(source)
        return {"skills/a": "tree-new"}

    # tree 与缓存不同（技能文件变了）。
    monkeypatch.setattr(scan_mod, "get_skill_tree_shas", fake_tree)
    monkeypatch.setattr(
        scan_mod,
        "get_skill_contents",
        lambda source, branch, *, client=None: (
            {"skills/a": "rev-new"},
            {"skills/a": "---\ndescription: fresh desc\n---\n"},
            0,
        ),
    )

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_tree_skipped"] == 0
    assert summary["repos_updated"] == 1
    assert read_jsonl(repo_dir / config.SCANNED_FILE) == [
        {"path": "skills/a", "rev": "rev-new", "description": "fresh desc"}
    ]
    meta = read_json(repo_dir / config.META_FILE)
    assert meta["skillTreeShas"] == {"skills/a": "tree-new"}
    # 预检那次请求即可复用：本轮只打了一次 Trees。
    assert tree_calls == ["owner/repo"]


def test_scan_tree_precheck_error_falls_back_to_tarball(monkeypatch, tmp_path):
    """Trees 预检失败（网络错误等）→ 降级为 tarball 全量路径，不算失败。"""
    OWNERS = {"owner/repo": ("2024-06-01T00:00:00Z", "main", 50)}
    base_dir = tmp_path / "by-source"
    repo_dir = base_dir / config.source_to_dir("owner/repo")
    repo_dir.mkdir(parents=True)
    (repo_dir / config.META_FILE).write_text(
        f'{{"schemaVersion": {config.SCHEMA_VERSION}, "status": "ok", '
        f'"pushedAt": "2024-01-01T00:00:00Z", "stars": 50, "skillCount": 1, '
        f'"skillTreeShas": {{"skills/a": "tree-a"}}}}'
    )
    (repo_dir / config.SCANNED_FILE).write_text(
        '{"path": "skills/a", "description": "cached"}\n'
    )

    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", tmp_path / "scanned-repos.jsonl")

    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: (dict(OWNERS), set()),
    )

    def broken_tree(source, branch, *, client=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(scan_mod, "get_skill_tree_shas", broken_tree)
    monkeypatch.setattr(
        scan_mod,
        "get_skill_contents",
        lambda source, branch, *, client=None: (
            {"skills/a": "rev-a"},
            {"skills/a": "---\ndescription: desc\n---\n"},
            0,
        ),
    )

    summary = scan_repositories(base_dir=base_dir)

    # 预检失败不计为 failed，走 tarball 正常更新。
    assert summary["repos_failed"] == 0
    assert summary["repos_updated"] == 1
    assert summary["repos_tree_skipped"] == 0


def test_scan_cold_cache_skips_precheck_but_records_baseline(monkeypatch, tmp_path):
    """冷缓存（首次扫描）不做预检——没有可比基准——直接下载 tarball；扫描后
    补一次 Trees 记下基准，下一轮推送才能走廉价预检而不是重复下载 tarball。"""
    OWNERS = {"owner/repo": ("2024-06-01T00:00:00Z", "main", 50)}
    base_dir = tmp_path / "by-source"
    repo_dir = base_dir / config.source_to_dir("owner/repo")
    repo_dir.mkdir(parents=True)
    # 无 meta.json（冷缓存）。

    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", tmp_path / "scanned-repos.jsonl")

    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: (dict(OWNERS), set()),
    )

    tree_calls = {"n": 0}

    def counting_tree(source, branch, *, client=None):
        tree_calls["n"] += 1
        return {"skills/a": "tree-a"}

    scanned = {"n": 0}

    def fake_contents(source, branch, *, client=None):
        scanned["n"] += 1
        assert tree_calls["n"] == 0  # 预检必须先于 tarball 被跳过
        return (
            {"skills/a": "rev-a"},
            {"skills/a": "---\ndescription: desc\n---\n"},
            0,
        )

    monkeypatch.setattr(scan_mod, "get_skill_tree_shas", counting_tree)
    monkeypatch.setattr(scan_mod, "get_skill_contents", fake_contents)

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_updated"] == 1
    assert scanned["n"] == 1
    # 恰好一次：扫描后的基准记录，不是预检。
    assert tree_calls["n"] == 1
    meta = read_json(repo_dir / config.META_FILE)
    assert meta["skillTreeShas"] == {"skills/a": "tree-a"}


def test_scan_max_skill_count_zero_disables_cap(monkeypatch, tmp_path):
    """--max-skill-count 0 显式关闭上限：超过 500 个技能的仓库不再被过滤。"""
    OWNERS = {"big/aggregator": ("2024-01-01T00:00:00Z", "main", 10)}
    base_dir = tmp_path / "by-source"
    (base_dir / config.source_to_dir("big/aggregator")).mkdir(parents=True)
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", tmp_path / "scanned-repos.jsonl")
    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: (dict(OWNERS), set()),
    )
    revs = {f"skills/s{i}": f"rev-{i}" for i in range(501)}
    contents = {f"skills/s{i}": f"---\ndescription: s{i}\n---\n" for i in range(501)}
    monkeypatch.setattr(
        scan_mod,
        "get_skill_contents",
        lambda source, branch, *, client=None: (revs, contents, 0),
    )

    summary = scan_repositories(max_skill_count=0, base_dir=base_dir)

    assert summary["repos_filtered"] == 0
    assert summary["repos_updated"] == 1
    assert summary["skills_scanned"] == 501


def test_is_missing_repo_checks_http_status():
    from skills_index.github import _is_missing_repo
    from skills_index.http import HttpError

    # get_json wraps a definitive 404 as HttpError with status=404.
    assert _is_missing_repo(HttpError("404 on /repos/o/r", status=404)) is True
    assert _is_missing_repo(HttpError("451 on /repos/o/r", status=451)) is False
    # Exhausted retries / other errors are not definitive.
    assert _is_missing_repo(HttpError("request failed after retries")) is False
    assert _is_missing_repo(RuntimeError("boom")) is False


def test_scan_filters_high_skillcount_repos(monkeypatch, tmp_path):
    """Repos with skillCount > --max-skill-count are dropped (both the cached
    up-to-date branch and the freshly scanned branch) and tombstoned: their
    meta keeps `status: filtered` + the count fingerprint, while scanned.jsonl
    is removed so no skill leaks into the index.

    owner1 is up-to-date with a cached skillCount=600 (>500) -> filtered via
    the up-to-date branch. owner4 is stale and, when rescanned, yields 501
    skill revs (>500) -> filtered via the scan branch. owner2/3 (up-to-date)
    and owner5/6 (stale, 1 skill) are kept.
    """
    OWNERS = {
        f"owner{i}/repo{i}": (f"2024-01-0{i}T00:00:0{i}Z", "main", i * 100)
        for i in range(1, 7)
    }
    base_dir = tmp_path / "by-source"
    for i, (source, (pushed, _b, _s)) in enumerate(OWNERS.items(), start=1):
        repo_dir = base_dir / config.source_to_dir(source)
        repo_dir.mkdir(parents=True, exist_ok=True)
        # Repos 1-3 stay up-to-date (cached); 4-6 are stale and get rescanned.
        prev_pushed = pushed if i <= 3 else "1999-01-01T00:00:00Z"
        cached_skill = 600 if i == 1 else 1  # owner1 cached above the cap
        (repo_dir / config.META_FILE).write_text(
            f'{{"schemaVersion": {config.SCHEMA_VERSION}, "status": "ok", '
            f'"pushedAt": "{prev_pushed}", "stars": {i * 100}, '
            f'"skillCount": {cached_skill}, "skillTreeShas": {{}}}}'
        )
        (repo_dir / config.SCANNED_FILE).write_text(
            '{"path": "skills/a", "description": "cached"}\n'
        )

    scanned_repos = tmp_path / "scanned-repos.jsonl"
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", scanned_repos)

    def fake_metas(sources, *, client=None, max_workers=8):
        return {s: OWNERS[s] for s in sources if s in OWNERS}, set()

    def fake_contents(source, branch, *, client=None):
        if source == "owner4/repo4":
            # 501 skill revs -> exceeds the default cap of 500.
            revs = {f"skills/s{i}": f"rev-{i}" for i in range(501)}
            contents = {
                f"skills/s{i}": f"---\ndescription: s{i}\n---\n" for i in range(501)
            }
            return revs, contents, 0
        # Per-source sha keeps each repo's skill-tree fingerprint distinct
        # (identical skillTreeShas would trip the mirror dedup).
        return (
            {"a": ("skills/a", f"sha-{source}")},
            {"skills/a": "---\ndescription: desc\n---\n"},
            0,
        )

    monkeypatch.setattr(scan_mod, "get_repo_metas", fake_metas)
    monkeypatch.setattr(scan_mod, "get_skill_contents", fake_contents)

    summary = scan_repositories(base_dir=base_dir)
    assert summary["repos_filtered"] == 2
    assert summary["repos_filtered_high_skill"] == 2
    assert summary["repos_skipped"] == 2   # owner2, owner3
    assert summary["repos_updated"] == 2   # owner5, owner6
    # 4 valid repos survive (owner2/3 skipped, owner5/6 updated); the 2
    # high-skill repos (owner1, owner4) are filtered out and excluded.
    assert summary["skills_scanned"] == 4
    assert summary["skills_scanned_new"] == 2
    # High-skill repos are tombstoned, not deleted: the dir keeps a
    # `filtered` meta carrying pushedAt + the count fingerprint, while the
    # scan data is gone.
    for source, count in (("owner1/repo1", 600), ("owner4/repo4", 501)):
        repo_dir = base_dir / config.source_to_dir(source)
        assert repo_dir.exists()
        assert not (repo_dir / config.SCANNED_FILE).exists()
        meta = read_json(repo_dir / config.META_FILE)
        assert meta["status"] == "filtered"
        assert meta["skillCount"] == count
        assert meta["pushedAt"] == OWNERS[source][0]
        assert "skillTreeShas" not in meta
    # Kept repos retain their cache.
    assert (base_dir / config.source_to_dir("owner2/repo2") / config.SCANNED_FILE).exists()
    assert (base_dir / config.source_to_dir("owner5/repo5") / config.SCANNED_FILE).exists()
    # Filtered repos are absent from the per-repo summary.
    repos = read_jsonl(scanned_repos)
    kept = {r["source"] for r in repos}
    assert kept == {"owner2/repo2", "owner3/repo3", "owner5/repo5", "owner6/repo6"}


def test_scan_filtered_repo_skipped_until_push(monkeypatch, tmp_path):
    """F4 墓碑生命周期：超上限仓库被过滤后，后续轮次免 tarball 直接跳过；
    仓库有新推送（可能回到上限以内）时重新全量裁决，仍超限则更新墓碑。"""
    OWNERS = {"big/aggregator": ("2024-01-01T00:00:00Z", "main", 10)}
    base_dir = tmp_path / "by-source"
    repo_dir = base_dir / config.source_to_dir("big/aggregator")
    repo_dir.mkdir(parents=True)
    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", tmp_path / "scanned-repos.jsonl")

    downloads = {"n": 0}

    def fake_metas(sources, *, client=None, max_workers=8):
        return {s: OWNERS[s] for s in sources}, set()

    def fake_contents(source, branch, *, client=None):
        downloads["n"] += 1
        revs = {f"skills/s{i}": f"rev-{i}" for i in range(501)}
        contents = {f"skills/s{i}": f"---\ndescription: s{i}\n---\n" for i in range(501)}
        return revs, contents, 0

    monkeypatch.setattr(scan_mod, "get_repo_metas", fake_metas)
    monkeypatch.setattr(scan_mod, "get_skill_contents", fake_contents)

    # 第 1 轮：501 个技能 > 500 -> 过滤并墓碑化。
    summary = scan_repositories(base_dir=base_dir)
    assert summary["repos_filtered"] == 1
    assert summary["repos_updated"] == 0
    assert downloads["n"] == 1
    meta = read_json(repo_dir / config.META_FILE)
    assert meta["status"] == "filtered"
    assert meta["skillCount"] == 501
    assert not (repo_dir / config.SCANNED_FILE).exists()

    # 第 2 轮：无新推送 -> 免 tarball 跳过（增量模式下过滤结果被缓存）。
    downloads["n"] = 0
    summary = scan_repositories(base_dir=base_dir)
    assert summary["repos_filtered"] == 1
    assert summary["repos_updated"] == 0
    assert downloads["n"] == 0
    assert read_jsonl(scan_mod.SCANNED_REPOS) == []

    # 第 3 轮：仓库有推送 -> 墓碑失效、重新裁决；仍超限 -> 更新墓碑快照。
    OWNERS["big/aggregator"] = ("2024-02-01T00:00:00Z", "main", 10)
    summary = scan_repositories(base_dir=base_dir)
    assert downloads["n"] == 1
    assert summary["repos_filtered"] == 1
    meta = read_json(repo_dir / config.META_FILE)
    assert meta["status"] == "filtered"
    assert meta["pushedAt"] == "2024-02-01T00:00:00Z"


def test_scan_purges_legacy_files_on_skip(monkeypatch, tmp_path):
    """缓存目录内契约之外的遗留文件（如旧版 per-dir fetched.jsonl 副本）在
    任意一次 meta 重写（含 pushed_at 未变的 skip 刷新）时被清理。"""
    OWNERS = {"owner/repo": ("2024-01-01T00:00:00Z", "main", 50)}
    base_dir = tmp_path / "by-source"
    repo_dir = base_dir / config.source_to_dir("owner/repo")
    repo_dir.mkdir(parents=True)
    (repo_dir / config.META_FILE).write_text(
        f'{{"schemaVersion": {config.SCHEMA_VERSION}, "status": "ok", '
        f'"pushedAt": "2024-01-01T00:00:00Z", "stars": 50, "skillCount": 1, '
        f'"skillTreeShas": {{}}}}'
    )
    (repo_dir / config.SCANNED_FILE).write_text(
        '{"path": "skills/a", "description": "cached"}\n'
    )
    legacy = repo_dir / "fetched.jsonl"  # 旧版格式遗留的 per-dir 副本
    legacy.write_text('{"skillId": "a"}\n')

    monkeypatch.setattr(scan_mod, "SCANNED_REPOS", tmp_path / "scanned-repos.jsonl")
    monkeypatch.setattr(
        scan_mod,
        "get_repo_metas",
        lambda sources, *, client=None, max_workers=8: (dict(OWNERS), set()),
    )

    summary = scan_repositories(base_dir=base_dir)

    assert summary["repos_skipped"] == 1
    assert not legacy.exists()  # skip 时的 meta 刷新顺手清掉了遗留文件
    assert (repo_dir / config.SCANNED_FILE).exists()
