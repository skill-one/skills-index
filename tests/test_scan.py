"""Tests for the stateless scan step (mocked GitHub layer, no real network)."""

from __future__ import annotations

import pytest

from skills_index import scan


def test_wanted_by_source_groups_in_fetch_order() -> None:
    rows = [
        {"source": "a/b", "skillId": "x"},
        {"source": "c/d", "skillId": "y"},
        {"source": "a/b", "skillId": "w"},
        {"source": "https://example.com/e/f", "skillId": "z"},  # non-GitHub
        {"source": "a/b", "skillId": "x"},  # duplicate
    ]
    out = scan.wanted_by_source(rows)
    assert list(out) == ["a/b", "c/d"]
    assert out["a/b"] == {"x", "w"}
    assert out["c/d"] == {"y"}


def test_scan_repositories_full_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Located skills get stars attached; gone repos drop their skills;
    rows keep fetch order, sorted by skillId within a repo."""
    fetched = [
        {"source": "a/b", "skillId": "x"},
        {"source": "a/b", "skillId": "y"},
        {"source": "c/d", "skillId": "z"},
    ]
    metas = {"a/b": 10}  # a real get_repo_metas never maps a gone repo
    monkeypatch.setattr(
        scan, "get_repo_metas", lambda sources, client=None: (metas, {"c/d"})
    )
    monkeypatch.setattr(scan, "new_github_client", lambda: object())

    def fake_clone(source: str, wanted: set[str]):
        return [
            {
                "skillId": sid,
                "path": f"skills/{sid}",
                "description": f"D {sid}",
                "lastCommitAt": "2026-01-01T00:00:00Z",
            }
            for sid in sorted(wanted)
        ]

    monkeypatch.setattr(scan, "clone_skills", fake_clone)

    skills, summary = scan.scan_repositories(fetched)

    # c/d is gone (404): only a/b's skills survive.
    assert [(s["source"], s["skillId"]) for s in skills] == [("a/b", "x"), ("a/b", "y")]
    assert skills[0]["stars"] == 10
    assert summary == {
        "repos_total": 2,
        "repos_gone": 1,
        "repos_failed": 0,
        "skills_wanted": 3,
        "skills_located": 2,
        "skills_not_found": 0,
    }


def test_scan_repositories_counts_missing_and_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skillId absent from its repo counts as not_found; a repo whose
    clone fails is skipped for this run and counted."""
    fetched = [
        {"source": "a/b", "skillId": "x"},
        {"source": "a/b", "skillId": "missing"},
        {"source": "boom/rep", "skillId": "y"},
    ]
    monkeypatch.setattr(
        scan,
        "get_repo_metas",
        lambda sources, client=None: ({"a/b": 1, "boom/rep": 1}, set()),
    )
    monkeypatch.setattr(scan, "new_github_client", lambda: object())

    def fake_clone(source: str, wanted: set[str]):
        if source == "boom/rep":
            raise RuntimeError("clone boom")
        return [
            {
                "skillId": "x",
                "path": "skills/x",
                "description": "D",
                "lastCommitAt": "2026-01-01T00:00:00Z",
            }
        ]

    monkeypatch.setattr(scan, "clone_skills", fake_clone)

    skills, summary = scan.scan_repositories(fetched)

    assert [(s["source"], s["skillId"]) for s in skills] == [("a/b", "x")]
    assert summary["skills_not_found"] == 1
    assert summary["repos_failed"] == 1
    assert summary["skills_located"] == 1
