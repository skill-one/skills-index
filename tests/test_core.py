"""Tests for the pure-logic helpers (no network required)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

from skills_index import config
from skills_index.fetch import filter_github
from skills_index.github import (
    _git_blob_sha,
    _parse_tarball,
    extract_description,
    is_nonpublic_frontmatter,
)
from skills_index.io_utils import read_jsonl, write_jsonl
from skills_index.scan import build_skill_records


def _make_tarball(files: dict[str, bytes]) -> bytes:
    """Build an in-memory gzipped tarball with a top-level `repo-<sha>/` dir."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(f"repo-abc123/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_source_dir_roundtrip() -> None:
    assert config.source_to_dir("vercel-labs/skills") == "vercel-labs__skills"
    assert config.dir_to_source("vercel-labs__skills") == "vercel-labs/skills"


def test_source_dir_only_first_sep_split() -> None:
    # dir_to_source splits on the FIRST separator, so repo names containing
    # `__` round-trip losslessly (owner names cannot contain underscores at
    # all, so the first separator is always the real one).
    assert config.source_to_dir("a/b/c") == "a__b__c"
    assert config.dir_to_source("a__b__c") == "a/b__c"
    assert config.source_to_dir("owner/my__repo") == "owner__my__repo"
    assert config.dir_to_source("owner__my__repo") == "owner/my__repo"


def test_is_github_source() -> None:
    assert config.is_github_source("owner/repo")
    assert not config.is_github_source("https://example.com/owner/repo")
    assert not config.is_github_source("not-a-source")


def test_filter_github_whitelists_fields() -> None:
    skills = [
        {"source": "owner/repo", "skillId": "x", "name": "X", "installs": 1, "extra": "drop"},
        {"source": "https://other.com/x/y", "skillId": "y"},
    ]
    kept, dropped = filter_github(skills)
    assert dropped == 1
    assert len(kept) == 1
    assert "extra" not in kept[0]
    assert set(kept[0]) <= config.KEEP_FIELDS


def test_jsonl_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    records = [{"a": 1}, {"b": "二"}]
    write_jsonl(path, records)
    assert read_jsonl(path) == records


def test_read_jsonl_missing_file(tmp_path: Path) -> None:
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_parse_tarball_finds_skill_blobs() -> None:
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\ndescription: Foo\n---\n",
            "skills/foo/README.md": b"ignored",
            "skills/bar/baz/SKILL.md": b"---\ndescription: Baz\n---\n",
        }
    )
    blobs, contents, filtered = _parse_tarball(raw)
    assert blobs == {
        "foo": ("skills/foo", _git_blob_sha(b"---\ndescription: Foo\n---\n")),
        "baz": ("skills/bar/baz", _git_blob_sha(b"---\ndescription: Baz\n---\n")),
    }
    assert contents["skills/foo"] == "---\ndescription: Foo\n---\n"
    assert filtered == 0


def test_parse_tarball_skips_non_skill_files() -> None:
    raw = _make_tarball(
        {
            "skills/foo/README.md": b"readme",
            "not-a-skill.md": b"nope",
            "README.md": b"nope",
        }
    )
    blobs, _contents, filtered = _parse_tarball(raw)
    assert blobs == {}
    assert filtered == 0


def test_parse_tarball_filters_internal_paths() -> None:
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\ndescription: Real foo\n---\n",
            # 同名测试夹具：必须被过滤，不得按 tar 顺序覆盖真实技能。
            "tests/foo/SKILL.md": b"---\ndescription: Fixture foo\n---\n",
            "examples/demo/SKILL.md": b"---\ndescription: Demo\n---\n",
            ".github/skill/SKILL.md": b"---\ndescription: Config\n---\n",
            # 歧义词：作为中间目录段过滤，作为技能名保留。
            "e2e/helper/SKILL.md": b"---\ndescription: E2E helper\n---\n",
            "skills/e2e/SKILL.md": b"---\ndescription: E2E skill\n---\n",
            # 状态词目录：任意段命中即过滤。
            "skills/deprecated/old/SKILL.md": b"---\ndescription: Old\n---\n",
        }
    )
    blobs, contents, filtered = _parse_tarball(raw)
    assert filtered == 5
    assert set(blobs) == {"foo", "e2e"}
    assert blobs["foo"] == (
        "skills/foo",
        _git_blob_sha(b"---\ndescription: Real foo\n---\n"),
    )
    assert "tests/foo" not in contents
    assert contents["skills/e2e"] == "---\ndescription: E2E skill\n---\n"


def test_parse_tarball_filters_nonpublic_frontmatter() -> None:
    raw = _make_tarball(
        {
            "skills/hidden/SKILL.md": b"---\nname: h\ndescription: x\nhidden: true\n---\n",
            "skills/deprecated/SKILL.md": b"---\nname: d\ndeprecated: yes\n---\n",
            "skills/unlisted/SKILL.md": b"---\nname: u\npublic: false\n---\n",
            "skills/ok/SKILL.md": b"---\nname: ok\ndescription: public\n---\n",
            "skills/no-fm/SKILL.md": b"# no frontmatter\n",
        }
    )
    blobs, contents, filtered = _parse_tarball(raw)
    assert filtered == 3
    assert set(blobs) == {"ok", "no-fm"}
    assert contents["skills/ok"] == "---\nname: ok\ndescription: public\n---\n"


def test_is_nonpublic_frontmatter_markers() -> None:
    assert is_nonpublic_frontmatter("---\nhidden: true\n---\nbody")
    assert is_nonpublic_frontmatter("---\nprivate: yes\n---\nbody")
    assert is_nonpublic_frontmatter("---\ninternal: 1\n---\nbody")
    assert is_nonpublic_frontmatter("---\npublic: false\n---\nbody")
    # 显式声明公开 / 标记为假值 / 无相关字段：均保留。
    assert not is_nonpublic_frontmatter("---\nhidden: false\n---\nbody")
    assert not is_nonpublic_frontmatter("---\npublic: true\n---\nbody")
    assert not is_nonpublic_frontmatter("---\ndescription: x\n---\nbody")
    assert not is_nonpublic_frontmatter("no frontmatter")
    assert not is_nonpublic_frontmatter("---\ninvalid: [unclosed\n---")


def test_is_internal_skill_path_filters_internal_dirs() -> None:
    # 测试夹具 / 示例 / 模板 / 构建产物 / 依赖树（仅匹配中间目录段）
    assert config.is_internal_skill_path("tests/foo")
    assert config.is_internal_skill_path("skills/examples/foo")
    assert config.is_internal_skill_path("__tests__/foo")
    assert config.is_internal_skill_path("fixtures/foo")
    assert config.is_internal_skill_path("node_modules/pkg/skills/foo")
    assert config.is_internal_skill_path("vendor/foo")
    assert config.is_internal_skill_path("templates/foo")
    assert config.is_internal_skill_path("dist/foo")
    assert config.is_internal_skill_path("docs/foo")


def test_is_internal_skill_path_hidden_dirs() -> None:
    # 隐藏目录默认视为仓库配置；紧跟 skills 段的 agent 技能根与 .skills 保留。
    assert config.is_internal_skill_path(".github/skills/foo")
    assert config.is_internal_skill_path(".github/foo")
    assert config.is_internal_skill_path(".devcontainer/foo")
    assert config.is_internal_skill_path(".vscode/foo")
    assert not config.is_internal_skill_path(".claude/skills/foo")
    assert not config.is_internal_skill_path(".agents/skills/foo")
    assert not config.is_internal_skill_path(".kilocode/skills/foo")
    assert not config.is_internal_skill_path(".skills/foo")


def test_is_internal_skill_path_keeps_skill_dir_names() -> None:
    # 排除词只匹配中间目录段；技能自身目录名（最后一段）不受影响。
    assert not config.is_internal_skill_path("skills/foo")
    assert not config.is_internal_skill_path("skills/test-generator")
    assert not config.is_internal_skill_path("skills/testing")
    assert not config.is_internal_skill_path("skills/tests")
    assert not config.is_internal_skill_path("skills/template")
    assert not config.is_internal_skill_path("skills/e2e")
    assert not config.is_internal_skill_path("skills/spec")
    assert not config.is_internal_skill_path("claude-skills/foo")


def test_is_internal_skill_path_case_insensitive() -> None:
    assert config.is_internal_skill_path("Tests/foo")
    assert config.is_internal_skill_path("skills/EXAMPLES/foo")
    assert config.is_internal_skill_path("skills/DEPRECATED/foo")


def test_is_internal_skill_path_status_words_any_position() -> None:
    # 状态词匹配任意路径段（含技能自身目录名）：目录或技能名本身为
    # deprecated / hidden / private 等即宣示非公开。
    assert config.is_internal_skill_path("deprecated/foo")
    assert config.is_internal_skill_path("skills/deprecated/foo")
    assert config.is_internal_skill_path("internal/foo")
    assert config.is_internal_skill_path("skills/hidden")
    assert config.is_internal_skill_path("skills/private")
    assert config.is_internal_skill_path("skills/obsolete")
    assert config.is_internal_skill_path("private")
    # 对照：结构词不作用于技能自身目录名（存在真实技能叫这些名字）。
    assert not config.is_internal_skill_path("skills/templates")
    assert not config.is_internal_skill_path("skills/docs")
    assert not config.is_internal_skill_path("skills/test")


def test_git_blob_sha_matches_known_value() -> None:
    # The empty blob has a canonical sha1 in git ("blob 0\0").
    assert _git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    # Content-addressed: different bytes -> different sha.
    assert _git_blob_sha(b"a") != _git_blob_sha(b"b")


def test_extract_description_from_frontmatter() -> None:
    md = """---
name: find-skills
description: Discover and install agent skills
---

# Find Skills
"""
    assert extract_description(md) == "Discover and install agent skills"


def test_extract_description_multiline_block() -> None:
    md = """---
name: x
description: |
  First line
  Second line
---

Body
"""
    assert extract_description(md) == "First line\nSecond line"


def test_extract_description_missing_returns_empty() -> None:
    assert extract_description("no frontmatter here") == ""
    assert extract_description("---\nname: only-name\n---\nbody") == ""
    assert extract_description("---\ninvalid: [unclosed\n---") == ""


def test_build_skill_records_sorts_by_path_and_parses_locally() -> None:
    blobs = {
        "b": ("skills/b", "sha-b"),
        "a": ("skills/a", "sha-a"),
    }
    contents = {
        "skills/a": "---\ndescription: A\n---\n",
        "skills/b": "---\ndescription: B\n---\n",
    }
    assert build_skill_records(blobs, contents) == [
        {"path": "skills/a", "description": "A"},
        {"path": "skills/b", "description": "B"},
    ]


def test_build_skill_records_missing_content_yields_empty_description() -> None:
    assert build_skill_records({"a": ("skills/a", "sha-a")}, {}) == [
        {"path": "skills/a", "description": ""}
    ]


def test_iter_repo_dirs_accepts_underscores_in_repo_names(tmp_path: Path) -> None:
    # repo 名可含连续下划线（owner 名不可能）：映射按第一个分隔符分割，
    # 往返无损，这样的目录不得被静默排除。
    (tmp_path / "owner__my__repo").mkdir()
    (tmp_path / "owner__repo").mkdir()
    (tmp_path / "unrelated").mkdir()
    (tmp_path / "loose-file").write_text("x")
    assert config.iter_repo_dirs(tmp_path) == ["owner__my__repo", "owner__repo"]


def test_load_github_token_prefers_gh_pat(monkeypatch) -> None:
    monkeypatch.setenv("GH_PAT", "pat-token")
    monkeypatch.setenv("GITHUB_TOKEN", "actions-token")
    assert config.load_github_token() == "pat-token"


def test_load_github_token_falls_back_to_github_token(monkeypatch) -> None:
    monkeypatch.delenv("GH_PAT", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "actions-token")
    assert config.load_github_token() == "actions-token"


def test_load_github_token_reads_env_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GH_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text('GITHUB_TOKEN="from-file"\n')
    monkeypatch.setattr(config, "ROOT", tmp_path)
    assert config.load_github_token() == "from-file"
