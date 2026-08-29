"""Tests for the CLI entry point (no network required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills_index import cli


def test_parser_has_all_subcommands() -> None:
    parser = cli.build_parser()
    # Each known command parses and tags args.command (no private internals).
    for command in ("fetch", "scan", "index", "update"):
        args = parser.parse_args([command])
        assert args.command == command


def test_cli_forwards_args_to_fetch_scan_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch / scan / index subcommands dispatch with their args forwarded."""
    seen: dict[str, dict] = {}

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list, dict]:
        seen["fetch"] = {"max_pages": max_pages}
        return [], {}

    def fake_scan(*, force: bool = False, max_skill_count=None) -> dict:
        seen["scan"] = {"force": force, "max_skill_count": max_skill_count}
        return {}

    def fake_index() -> tuple[list, dict]:
        seen["index"] = {"called": True}
        return [], {}

    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)

    assert cli.main(["fetch", "--pages", "3"]) == 0
    assert cli.main(["scan", "--force", "--max-skill-count", "7"]) == 0
    assert cli.main(["index"]) == 0

    assert seen["fetch"] == {"max_pages": 3}
    assert seen["scan"] == {"force": True, "max_skill_count": 7}
    assert seen["index"] == {"called": True}


def test_update_runs_pipeline_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`update` calls clean -> fetch -> scan -> index with args forwarded correctly."""
    calls: list[tuple[str, dict]] = []
    # Keep the run report out of the real data/ dir.
    monkeypatch.setattr(cli, "RUN_SUMMARY", tmp_path / "run-summary.md")

    def fake_clean() -> None:
        calls.append(("clean", {}))

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list, dict]:
        calls.append(("fetch", {"max_pages": max_pages}))
        return [], {}

    def fake_scan(*, force: bool = False, base_dir=None, **kwargs) -> dict:
        calls.append(("scan", {"force": force, **kwargs}))
        return {}

    def fake_index(*, base_dir=None) -> tuple[list, dict]:
        calls.append(("index", {}))
        return [], {}

    monkeypatch.setattr(cli, "clean_workspace", fake_clean)
    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)

    assert cli.main(["update", "--pages", "1", "--force"]) == 0

    assert [c[0] for c in calls] == ["clean", "fetch", "scan", "index"]
    assert calls[1][1] == {"max_pages": 1}
    assert calls[2][1] == {
        "force": True,
        "max_skill_count": None,
    }


def test_update_defaults_is_incremental(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without flags, update keeps the cache: no clean, fetch all, prune stale."""
    seen: dict[str, dict] = {}
    # Keep the run report out of the real data/ dir.
    monkeypatch.setattr(cli, "RUN_SUMMARY", tmp_path / "run-summary.md")

    def fake_clean() -> None:
        seen["clean"] = {}

    def fake_fetch(*, max_pages: int = 0, token: str = "") -> tuple[list, dict]:
        seen["fetch"] = {"max_pages": max_pages}
        return [{"source": "a/b"}], {}

    def fake_scan(*, force: bool = False, base_dir=None, **kwargs) -> dict:
        seen["scan"] = {"force": force, **kwargs}
        return {}

    def fake_index(*, base_dir=None) -> tuple[list, dict]:
        return [], {}

    def fake_prune(sources, base_dir=None) -> int:
        seen["prune"] = {"sources": sources}
        return len(sources)

    monkeypatch.setattr(cli, "clean_workspace", fake_clean)
    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)
    monkeypatch.setattr(cli, "prune_stale_repos", fake_prune)

    assert cli.main(["update"]) == 0
    # Incremental path: cache is preserved (no clean), stale dirs are pruned.
    assert "clean" not in seen
    assert seen == {
        "fetch": {"max_pages": 0},
        "prune": {"sources": {"a/b"}},
        "scan": {"force": False, "max_skill_count": None},
    }


def test_clean_workspace_wipes_stale_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """clean_workspace removes every registered published artifact and the
    whole per-repo cache tree, leaving unregistered files alone."""
    data = tmp_path / "data"
    data.mkdir()
    published = [
        data / "fetched-skills.jsonl",
        data / "index.jsonl",
        data / "index-meta.json",
        data / "scanned-repos.jsonl",
        data / "scanned-repos-by-stars.jsonl",
        data / "scanned-repos-by-skillcount.jsonl",
        data / "run-summary.md",
    ]
    for path in published:
        path.write_text("{}")
    unregistered = data / "unregistered.txt"
    unregistered.write_text("keep me")

    by_source = tmp_path / "cache" / "by-source"
    stale_repo = by_source / "owner__repo"
    stale_repo.mkdir(parents=True)
    (stale_repo / "meta.json").write_text(json.dumps({"pushedAt": "x"}))
    (stale_repo / "scanned.jsonl").write_text("{}")

    monkeypatch.setattr(cli, "PUBLISHED_FILES", tuple(published))
    monkeypatch.setattr(cli, "BY_SOURCE_DIR", by_source)

    cli.clean_workspace()

    assert all(not path.exists() for path in published)
    # Only registered artifacts are removed.
    assert unregistered.exists()
    # The per-repo cache tree is wiped entirely (no stale repo dirs remain).
    assert not by_source.exists()


def test_unknown_command_returns_error() -> None:
    with pytest.raises(SystemExit):
        cli.main(["bogus"])
