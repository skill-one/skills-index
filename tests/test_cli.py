"""Tests for the CLI entry point (no network required)."""

from __future__ import annotations

import pytest

from skills_index import cli


def test_parser_accepts_new_surface() -> None:
    args = cli.build_parser().parse_args(["update", "--pages", "2", "--tag", "data-x"])
    assert (args.command, args.pages, args.tag) == ("update", 2, "data-x")


def test_parser_has_no_legacy_flags() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["scan", "--force"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["update", "--max-skill-count", "5"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["update", "--prev-index", "p.jsonl"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["pull", "--repo", "a/b"])


def test_build_summary_renders_all_sections() -> None:
    text = cli._build_summary(
        {"raw_skills": 10, "kept_github": 8, "dropped_non_github": 2, "failed_pages": [1]},
        {"repos_total": 4, "repos_gone": 1, "repos_failed": 0,
         "skills_wanted": 6, "skills_located": 5, "skills_not_found": 1},
        {"confirmed": 5, "not_in_repo": 1, "deduped": 1, "index": 4},
        total=12.0, fetch=1.0, scan=10.0, index=1.0, pages=0, tag="data-x",
    )
    assert "**Scope:** full refresh, tag `data-x`" in text
    assert "### Fetch (skills.sh)" in text
    assert "### Scan (GitHub repos, stateless full scan)" in text
    assert "**Final index entries: `4`**" in text
    assert "Skipped pages (errors): `1`" in text
    assert "Cross-repo duplicates dropped: `1`" in text


def test_update_prints_run_summary(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`update` chains the three steps and prints the report to stdout."""
    calls: list[str] = []

    def fake_fetch(*, max_pages: int = 0, token: str = ""):
        calls.append(f"fetch:{max_pages}")
        return [], {"raw_skills": 1, "kept_github": 1, "dropped_non_github": 0, "failed_pages": []}

    def fake_scan(records):
        calls.append("scan")
        return [], {"repos_total": 1, "repos_gone": 0, "repos_failed": 0,
                    "skills_wanted": 1, "skills_located": 1, "skills_not_found": 0}

    def fake_index(fetched, scanned, *, tag: str = ""):
        calls.append(f"index:{tag}")
        return [], {"confirmed": 1, "not_in_repo": 0, "deduped": 0, "index": 1}

    monkeypatch.setattr(cli, "run_fetch", fake_fetch)
    monkeypatch.setattr(cli, "scan_repositories", fake_scan)
    monkeypatch.setattr(cli, "run_index", fake_index)

    assert cli.main(["update", "--tag", "data-x"]) == 0
    assert calls == ["fetch:0", "scan", "index:data-x"]
    out = capsys.readouterr().out
    assert "### Fetch (skills.sh)" in out and "### Index (merged)" in out
