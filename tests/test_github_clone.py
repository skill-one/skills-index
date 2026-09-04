"""End-to-end tests for the git-clone scan path (local fixture repos, no network).

Builds real git repositories with controlled commit dates, then verifies that
`clone_skills` finds every wanted skillId with first-lexicographic matching,
extracts the frontmatter description, and reports each skill directory's true
last-commit time (normalized to UTC) — including the shallow-history fallback.
"""

from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path

import pytest

from skills_index.github import clone_skills

_SKILL = "---\nname: foo\ndescription: Foo\n---\n"
_EARLY = datetime.datetime(2024, 1, 1, 10, 0, 0, tzinfo=datetime.UTC)
_LATE = datetime.datetime(2024, 6, 1, 12, 30, 0, tzinfo=datetime.UTC)


def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None, allow_fail: bool = False
) -> str:
    full_env = {**os.environ, **(env or {})}
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )
    if not allow_fail and res.returncode != 0:
        raise AssertionError(f"git {args} failed: {res.stderr}")
    return res.stdout


def _commit(
    repo: Path, files: dict[str, str | None], date: datetime.datetime, msg: str
) -> None:
    """Create one commit with a fixed committer date (`None` deletes a file)."""
    iso = date.isoformat()
    for rel, content in files.items():
        p = repo / rel
        if content is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg, env={"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso})


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A source repo configured to serve partial-clone filters locally."""
    r = tmp_path / "src"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "test")
    _git(r, "config", "uploadpack.allowFilter", "true")
    return r


def _clone(repo: Path, wanted: set[str], *, since: str = "100.years.ago") -> list[dict]:
    # A wide window keeps the main tests on the non-shallow path; the
    # fallback test narrows it explicitly.
    return clone_skills("owner/repo", wanted, url=str(repo), since=since)


def test_locates_wanted_skills_with_true_last_commit(repo: Path) -> None:
    """Two commits touch skills/foo; lastCommitAt must be the later one."""
    _commit(repo, {"skills/foo/SKILL.md": _SKILL}, _EARLY, "add foo")
    _commit(repo, {"skills/foo/notes.txt": "x\n"}, _LATE, "touch foo dir")
    _commit(repo, {"README.md": "hi\n"}, _LATE, "outside the skill dir")

    rows = _clone(repo, {"foo"})

    assert rows == [
        {
            "skillId": "foo",
            "path": "skills/foo",
            "description": "Foo",
            "lastCommitAt": "2024-06-01T12:30:00Z",
        }
    ]


def test_unwanted_and_missing_skillids(repo: Path) -> None:
    _commit(repo, {"skills/foo/SKILL.md": _SKILL}, _EARLY, "add foo")
    rows = _clone(repo, {"foo", "nope"})
    assert [r["skillId"] for r in rows] == ["foo"]  # "nope" simply absent


def test_first_lexicographic_candidate_wins(repo: Path) -> None:
    """同名目录时字典序第一个胜出（examples < skills）。"""
    _commit(
        repo,
        {
            "examples/foo/SKILL.md": _SKILL,
            "skills/foo/SKILL.md": "---\nname: foo\ndescription: Other\n---\n",
        },
        _EARLY,
        "two foos",
    )
    rows = _clone(repo, {"foo"})
    assert len(rows) == 1
    assert rows[0]["path"] == "examples/foo"
    assert rows[0]["description"] == "Foo"


def test_root_skill_md_never_matches(repo: Path) -> None:
    """仓库根的裸 SKILL.md 没有父目录可命名技能，永不命中。"""
    _commit(repo, {"SKILL.md": _SKILL}, _EARLY, "root only")
    assert _clone(repo, {"foo"}) == []


def test_nested_skill_md_is_a_candidate_of_its_own_basename(repo: Path) -> None:
    """嵌套 SKILL.md 以自身目录名参与匹配。"""
    _commit(
        repo,
        {
            "skills/foo/SKILL.md": _SKILL,
            "skills/foo/nested/SKILL.md": "---\nname: n\ndescription: N\n---\n",
        },
        _EARLY,
        "nested",
    )
    rows = _clone(repo, {"foo", "nested"})
    assert [(r["skillId"], r["path"]) for r in rows] == [
        ("foo", "skills/foo"),
        ("nested", "skills/foo/nested"),
    ]


def test_description_empty_without_frontmatter(repo: Path) -> None:
    _commit(repo, {"skills/foo/SKILL.md": "no frontmatter here\n"}, _EARLY, "x")
    assert _clone(repo, {"foo"})[0]["description"] == ""


def test_timezone_offsets_are_normalized_to_utc(repo: Path) -> None:
    _commit(
        repo,
        {"skills/foo/SKILL.md": _SKILL},
        datetime.datetime(
            2024, 3, 5, 10, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
        ),
        "offset date",
    )
    rows = _clone(repo, {"foo"})
    assert rows[0]["lastCommitAt"] == "2024-03-05T08:00:00Z"


def test_shallow_history_falls_back_to_unshallow(repo: Path) -> None:
    """The default 1-year window cuts off the old skill dir; the clone must
    deepen once and still report the exact old commit date."""
    _commit(repo, {"skills/old/SKILL.md": _SKILL}, _EARLY, "old dir")
    recent = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    _commit(repo, {"skills/new/SKILL.md": _SKILL}, recent, "new dir")

    rows = _clone(repo, {"old", "new"}, since="1.year.ago")

    by_id = {r["skillId"]: r for r in rows}
    assert by_id["old"]["lastCommitAt"] == "2024-01-01T10:00:00Z"
    assert by_id["new"]["lastCommitAt"] == recent.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_empty_repo_yields_no_rows(repo: Path) -> None:
    """A repo without any commits produces no skills (and must not crash)."""
    assert _clone(repo, {"foo"}, since="1.year.ago") == []
