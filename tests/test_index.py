"""Tests for the index merge step (no network required)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from skills_index import config
from skills_index import index as index_mod
from skills_index.io_utils import read_jsonl, write_json, write_jsonl


def _setup_data(
    tmp_path: Path,
    *,
    fetched: list[dict],
    scanned: dict[str, list[dict]],
    stars: dict[str, int] | None = None,
) -> tuple[Path, Path, Path]:
    """Create data/fetched-skills.jsonl + data/by-source/<dir>/ cache files.

    Each repo dir gets a scanned.jsonl plus an "ok"-status meta.json whose
    `stars` comes from `stars[dir]` (0 by default), mirroring what the scan
    step persists. Returns ``(fetched_path, index_path, by_source_dir)``.
    """
    data = tmp_path / "data"
    by_source = data / "by-source"
    by_source.mkdir(parents=True)
    fetched_path = data / "fetched-skills.jsonl"
    write_jsonl(fetched_path, fetched)
    for dir_name, records in scanned.items():
        gh = by_source / dir_name
        gh.mkdir()
        write_jsonl(gh / "scanned.jsonl", records)
        write_json(
            gh / "meta.json",
            {
                "schemaVersion": config.SCHEMA_VERSION,
                "status": "ok",
                "source": config.dir_to_source(dir_name),
                "stars": (stars or {}).get(dir_name, 0),
            },
        )
    return fetched_path, data / "index.jsonl", by_source


def _patch_paths(monkeypatch: pytest.MonkeyPatch, fetched_path: Path, index_path: Path) -> None:
    monkeypatch.setattr(index_mod, "FETCHED_SKILLS", fetched_path)
    monkeypatch.setattr(index_mod, "INDEX_JSONL", index_path)
    monkeypatch.setattr(index_mod, "INDEX_META_JSON", index_path.parent / "index-meta.json")
    # run_index 会读写跨运行 rev 账本：不打桩就会污染仓库真实的 cache/。
    monkeypatch.setattr(index_mod, "REV_LEDGER", index_path.parent / "rev-ledger.jsonl")


def test_run_index_merges_scanned_into_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = [
        {"source": "owner/repo", "skillId": "a", "installs": 10},
        {"source": "owner/repo", "skillId": "b", "installs": 20},
    ]
    scanned = {"owner__repo": [{"path": "skills/a", "description": "A"}]}
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    # `b` only exists in skills.sh, not in the repo scan -> dropped.
    assert records == [
        {"source": "owner/repo", "skillId": "a", "stars": 0, "installs": 10,
         "weeklyInstalls": [], "path": "skills/a", "description": "A"}
    ]
    assert read_jsonl(index_path) == records
    assert summary["index"] == 1
    assert summary["scanned_merged"] == 1
    assert summary["not_in_repo"] == 1
    assert summary["scan_only"] == 0


def test_run_index_writes_meta_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """index-meta.json 只含运行期变化的字段（formatVersion / generatedAt /
    counts.total）；静态 schema 说明归 README，distCommit 由 CI 推送后回填。"""
    fetched = [
        {"source": "owner/repo", "skillId": "a", "installs": 1},
        {"source": "owner/repo", "skillId": "b", "installs": 2},
    ]
    scanned = {
        "owner__repo": [
            {"path": "skills/a", "description": "A"},
            {"path": "skills/gh-only", "description": "not on skills.sh"},
        ]
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, _summary = index_mod.run_index(base_dir=by_source)

    meta_path = index_path.parent / "index-meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["formatVersion"] == config.INDEX_FORMAT_VERSION
    # generatedAt 是秒级 UTC Z 格式。
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", meta["generatedAt"])
    assert meta["counts"] == {"total": len(records)}
    # Exactly these keys: consumers may rely on the minimal shape, and a
    # leaking static-schema or internal field would be a format regression.
    assert set(meta) == {"formatVersion", "generatedAt", "counts"}


def test_run_index_keeps_fetched_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched = [
        {"source": "o/r", "skillId": "b", "installs": 2},
        {"source": "o/r", "skillId": "a", "installs": 1},
        {"source": "o/r", "skillId": "c", "installs": 3},
    ]
    # scanned order is deliberately the reverse of fetched order.
    scanned = {
        "o__r": [
            {"path": "skills/b", "description": "B"},
            {"path": "skills/a", "description": "A"},
        ]
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert [r["skillId"] for r in records] == ["b", "a"]
    assert summary["not_in_repo"] == 1  # `c` dropped


def test_run_index_includes_scan_only_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仓库有、榜单无的技能仍入索引，installs/weeklyInstalls 字段不出现，追加在末尾；
    stars 是仓库级数据，scan-only 技能同样携带。"""
    fetched = [{"source": "owner/repo", "skillId": "a", "installs": 1}]
    scanned = {
        "owner__repo": [
            {"path": "skills/a", "description": "A"},
            {"path": "skills/gh-only", "description": "not on skills.sh"},
        ]
    }
    fetched_path, index_path, by_source = _setup_data(
        tmp_path, fetched=fetched, scanned=scanned, stars={"owner__repo": 321}
    )
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert records == [
        {"source": "owner/repo", "skillId": "a", "stars": 321, "installs": 1,
         "weeklyInstalls": [], "path": "skills/a", "description": "A"},
        {"source": "owner/repo", "skillId": "gh-only", "stars": 321,
         "path": "skills/gh-only", "description": "not on skills.sh"},
    ]
    assert summary["scan_only"] == 1
    assert summary["index"] == 2


def test_run_index_empty_fetched_still_indexes_scanned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """fetched 数据缺失时仍以扫描为基准输出，installs/weeklyInstalls 字段不出现。"""
    fetched = []
    scanned = {"owner__repo": [{"path": "skills/a", "description": "A"}]}
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert records == [
        {"source": "owner/repo", "skillId": "a", "stars": 0,
         "path": "skills/a", "description": "A"}
    ]
    assert read_jsonl(index_path) == records
    assert summary["scan_only"] == 1
    assert summary["not_in_repo"] == 0
    assert "no fetched data" in capsys.readouterr().out


def test_run_index_empty_fetched_writes_empty_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=[], scanned={})
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert records == []
    assert read_jsonl(index_path) == []
    assert "no fetched data" in capsys.readouterr().out


def test_run_index_dedups_identical_skills_across_repos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同 skillId + 同 description 的跨仓库重复只保留 installs 更高者；
    description 不同的同名技能（真实存在的不同实现）不受影响。"""
    fetched = [
        {"source": "a/r", "skillId": "x", "installs": 10},
        {"source": "b/r", "skillId": "x", "installs": 20},
        {"source": "c/r", "skillId": "x", "installs": 5},
    ]
    scanned = {
        "a__r": [{"path": "skills/x", "description": "same"}],
        "b__r": [{"path": "skills/x", "description": "same"}],
        # 同名但不同实现（description 不同）-> 两个不同技能，都保留。
        "c__r": [{"path": "skills/x", "description": "different"}],
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert summary["deduped_skills"] == 1
    assert summary["index"] == 2
    # 保留 installs 更高的 b/r；description 不同的 c/r 不受影响。
    assert [r["source"] for r in records] == ["b/r", "c/r"]
    assert records[0]["installs"] == 20
    assert records[0]["description"] == "same"


def test_run_index_dedup_keeps_first_on_equal_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """installs 相同时保留 fetch 顺序靠前者（skills.sh 排名更高）。"""
    fetched = [
        {"source": "first/r", "skillId": "x", "installs": 7},
        {"source": "second/r", "skillId": "x", "installs": 7},
    ]
    scanned = {
        "first__r": [{"path": "skills/x", "description": "same"}],
        "second__r": [{"path": "skills/x", "description": "same"}],
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert summary["deduped_skills"] == 1
    assert [r["source"] for r in records] == ["first/r"]


def test_run_index_no_dedup_on_empty_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """description 为空表示未知而非相同：不参与去重。"""
    fetched = [
        {"source": "a/r", "skillId": "x", "installs": 10},
        {"source": "b/r", "skillId": "x", "installs": 20},
    ]
    scanned = {
        "a__r": [{"path": "skills/x", "description": ""}],
        "b__r": [{"path": "skills/x", "description": ""}],
    }
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, summary = index_mod.run_index(base_dir=by_source)

    assert summary["deduped_skills"] == 0
    assert len(records) == 2


def test_run_index_attaches_repo_stars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每条记录的 stars 取自其所在仓库（cache meta.json），仓库内所有技能共享同一值。"""
    fetched = [
        {"source": "hot/r", "skillId": "x", "installs": 10},
        {"source": "cold/r", "skillId": "y", "installs": 5},
    ]
    scanned = {
        "hot__r": [{"path": "skills/x", "description": "X"}],
        "cold__r": [{"path": "skills/y", "description": "Y"}],
    }
    fetched_path, index_path, by_source = _setup_data(
        tmp_path, fetched=fetched, scanned=scanned, stars={"hot__r": 5000, "cold__r": 7}
    )
    _patch_paths(monkeypatch, fetched_path, index_path)

    records, _ = index_mod.run_index(base_dir=by_source)

    stars_by_skill = {r["skillId"]: r["stars"] for r in records}
    assert stars_by_skill == {"x": 5000, "y": 7}


def test_run_index_stars_zero_without_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """meta.json 缺失（如旧版缓存）时不报错，stars 兜底为 0。"""
    fetched = [{"source": "owner/repo", "skillId": "a", "installs": 1}]
    scanned = {"owner__repo": [{"path": "skills/a", "description": "A"}]}
    fetched_path, index_path, by_source = _setup_data(tmp_path, fetched=fetched, scanned=scanned)
    _patch_paths(monkeypatch, fetched_path, index_path)
    (by_source / "owner__repo" / "meta.json").unlink()

    records, _ = index_mod.run_index(base_dir=by_source)

    assert records[0]["stars"] == 0


DAY1 = "2026-08-30T00:00:00Z"
DAY2 = "2026-08-31T00:00:00Z"


def _version_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scanned: list[dict]
) -> tuple[Path, Path]:
    """One repo with `scanned` records; returns (index path, ledger path)."""
    fetched = [
        {"source": "owner/repo", "skillId": "a", "installs": 1},
        {"source": "owner/repo", "skillId": "b", "installs": 2},
    ]
    fetched_path, index_path, by_source = _setup_data(
        tmp_path, fetched=fetched, scanned={"owner__repo": scanned}
    )
    _patch_paths(monkeypatch, fetched_path, index_path)
    return index_path, index_path.parent / "rev-ledger.jsonl"


def test_run_index_publishes_rev_and_first_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """scan 得到的 rev 原样发布；firstSeenAt 首轮落在本轮时间，并写进账本。"""
    scanned = [
        {"path": "skills/a", "rev": "aaa", "description": "A"},
        {"path": "skills/b", "rev": "bbb", "description": "B"},
    ]
    index_path, ledger_path = _version_setup(tmp_path, monkeypatch, scanned)

    records, summary = index_mod.run_index(base_dir=tmp_path / "data" / "by-source", now=DAY1)

    assert [(r["rev"], r["firstSeenAt"]) for r in records] == [
        ("aaa", DAY1),
        ("bbb", DAY1),
    ]
    assert read_jsonl(index_path) == records
    assert summary["rev_refreshed"] == 2
    assert summary["ledger_total"] == 2
    assert read_jsonl(ledger_path) == [
        {"source": "owner/repo", "path": "skills/a", "rev": "aaa", "firstSeenAt": DAY1},
        {"source": "owner/repo", "path": "skills/b", "rev": "bbb", "firstSeenAt": DAY1},
    ]


def test_run_index_date_holds_until_content_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """核心性质：第二天重跑，内容没变的技能日期不动；只有 rev 变了的那条前进。"""
    scanned = [
        {"path": "skills/a", "rev": "aaa", "description": "A"},
        {"path": "skills/b", "rev": "bbb", "description": "B"},
    ]
    _index_path, _ledger = _version_setup(tmp_path, monkeypatch, scanned)
    by_source = tmp_path / "data" / "by-source"
    index_mod.run_index(base_dir=by_source, now=DAY1)

    second = [
        {"path": "skills/a", "rev": "aaa", "description": "A"},
        {"path": "skills/b", "rev": "bbb-edited", "description": "B"},
    ]
    write_jsonl(by_source / "owner__repo" / config.SCANNED_FILE, second)
    records, summary = index_mod.run_index(base_dir=by_source, now=DAY2)

    assert [(r["rev"], r["firstSeenAt"]) for r in records] == [
        ("aaa", DAY1),
        ("bbb-edited", DAY2),
    ]
    assert summary["rev_refreshed"] == 1


def test_run_index_without_rev_yields_no_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧缓存没有 rev：两个字段都不出现，也不给一个假的日期。"""
    scanned = [{"path": "skills/a", "description": "A"}]
    index_path, ledger_path = _version_setup(tmp_path, monkeypatch, scanned)

    records, _summary = index_mod.run_index(
        base_dir=tmp_path / "data" / "by-source", now=DAY1
    )

    assert records[0]["skillId"] == "a"
    assert "rev" not in records[0] and "firstSeenAt" not in records[0]
    assert not ledger_path.exists() or read_jsonl(ledger_path) == []
