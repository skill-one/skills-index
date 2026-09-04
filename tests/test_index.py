"""Tests for the index step: join, ordering, dedup, field layout."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills_index import config, index
from skills_index.io_utils import read_json

NOW = "2026-09-04T00:00:00Z"


def _fetched(source: str, skill_id: str, installs: int = 1) -> dict:
    return {
        "source": source,
        "skillId": skill_id,
        "name": skill_id,
        "installs": installs,
        "weeklyInstalls": [installs],
    }


def _scanned(
    source: str, skill_id: str, path: str, last_commit_at: str, stars: int = 7
) -> dict:
    return {
        "source": source,
        "skillId": skill_id,
        "path": path,
        "description": f"desc {skill_id}",
        "lastCommitAt": last_commit_at,
        "stars": stars,
    }


@pytest.fixture()
def outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect index.jsonl / index-meta.json into tmp_path."""
    monkeypatch.setattr(index, "INDEX_JSONL", tmp_path / "index.jsonl")
    monkeypatch.setattr(index, "INDEX_META_JSON", tmp_path / "meta.json")
    return tmp_path


def test_run_index_joins_in_rank_order(outputs: Path) -> None:
    records, summary = index.run_index(
        fetched=[_fetched("z/z", "beta", 2), _fetched("a/a", "alpha", 9)],
        scanned=[
            _scanned("a/a", "alpha", "skills/alpha", "2026-01-02T03:04:05Z"),
            _scanned("z/z", "beta", "b", "2026-06-07T08:09:10Z"),
        ],
        now=NOW,
    )

    # skills.sh ranking order wins over any other order.
    assert [r["skillId"] for r in records] == ["beta", "alpha"]
    assert records[1]["installs"] == 9
    assert records[1]["stars"] == 7
    assert records[1]["path"] == "skills/alpha"
    # Field order of the published record; fetched `name` stays unpublished.
    assert list(records[1]) == [
        "skillId", "source", "stars", "description",
        "installs", "weeklyInstalls", "path", "lastCommitAt",
    ]
    # lastCommitAt is factual data taken verbatim from the scan.
    assert records[0]["lastCommitAt"] == "2026-06-07T08:09:10Z"
    assert records[1]["lastCommitAt"] == "2026-01-02T03:04:05Z"
    assert summary["index"] == 2
    assert summary["not_in_repo"] == 0
    assert read_json(outputs / "meta.json")["counts"]["total"] == 2


def test_run_index_drops_unconfirmed_and_unknown(outputs: Path) -> None:
    records, summary = index.run_index(
        fetched=[_fetched("a/a", "kept"), _fetched("a/a", "gone-from-repo")],
        scanned=[_scanned("a/a", "kept", "skills/kept", "2026-01-01T00:00:00Z")],
        now=NOW,
    )

    assert [r["skillId"] for r in records] == ["kept"]
    assert summary["not_in_repo"] == 1
    assert summary["confirmed"] == 1


def test_run_index_dedups_cross_repo_duplicates(outputs: Path) -> None:
    records, summary = index.run_index(
        fetched=[
            _fetched("low/installs", "dup", 5),
            _fetched("high/installs", "dup", 99),
            _fetched("x/x", "unique", 1),
        ],
        scanned=[
            _scanned("low/installs", "dup", "d", "2026-01-01T00:00:00Z"),
            _scanned("high/installs", "dup", "d", "2026-01-01T00:00:00Z"),
            _scanned("x/x", "unique", "u", "2026-01-01T00:00:00Z"),
        ],
        now=NOW,
    )

    dup = [r for r in records if r["skillId"] == "dup"]
    assert len(dup) == 1
    assert dup[0]["source"] == "high/installs"
    assert summary["deduped"] == 1


def test_run_index_dedup_tiebreaks_on_rank(outputs: Path) -> None:
    """Equal installs -> the earlier-ranked (first fetched) record wins."""
    records, _ = index.run_index(
        fetched=[_fetched("first/rank", "dup", 5), _fetched("second/rank", "dup", 5)],
        scanned=[
            _scanned("first/rank", "dup", "d", "2026-01-01T00:00:00Z"),
            _scanned("second/rank", "dup", "d", "2026-01-01T00:00:00Z"),
        ],
        now=NOW,
    )

    assert len(records) == 1
    assert records[0]["source"] == "first/rank"


def test_run_index_keeps_empty_descriptions(outputs: Path) -> None:
    """description 为空不参与去重：未知不等于相同。"""
    records, summary = index.run_index(
        fetched=[_fetched("a/a", "same", 5), _fetched("b/b", "same", 5)],
        scanned=[
            {**_scanned("a/a", "same", "d", "2026-01-01T00:00:00Z"), "description": ""},
            {**_scanned("b/b", "same", "d", "2026-01-01T00:00:00Z"), "description": ""},
        ],
        now=NOW,
    )
    assert len(records) == 2
    assert summary["deduped"] == 0


def test_run_index_writes_meta_with_optional_tag(outputs: Path) -> None:
    records, _ = index.run_index(
        fetched=[_fetched("a/a", "x")],
        scanned=[_scanned("a/a", "x", "p", "2026-01-01T00:00:00Z")],
        now=NOW,
        tag="data-20260904T000000Z",
    )

    meta = read_json(outputs / "meta.json")
    assert meta == {
        "formatVersion": config.INDEX_FORMAT_VERSION,
        "generatedAt": NOW,
        "counts": {"total": len(records)},
        "tag": "data-20260904T000000Z",
    }

    # No tag passed -> the key is absent, not empty.
    index.run_index(
        fetched=[_fetched("a/a", "x")],
        scanned=[_scanned("a/a", "x", "p", "2026-01-01T00:00:00Z")],
        now=NOW,
    )
    meta2 = read_json(outputs / "meta.json")
    assert "tag" not in meta2


def test_run_index_empty_inputs_writes_empty_index(outputs: Path) -> None:
    records, summary = index.run_index(fetched=[], scanned=[], now=NOW)
    assert records == []
    assert summary["index"] == 0
    assert read_json(outputs / "meta.json")["counts"]["total"] == 0
