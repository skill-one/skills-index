"""Tests for the pure-logic helpers (no network required)."""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import Path

from skills_index import config
from skills_index.fetch import filter_github
from skills_index.github import (
    _git_blob_sha,
    _parse_tarball,
    extract_description,
    is_invalid_frontmatter,
    is_nonpublic_frontmatter,
)
from skills_index.io_utils import read_jsonl, write_jsonl
from skills_index.scan import build_skill_records

# Published rev shape: algorithm tag + truncated sha256 (see config.REV_*).
REV_RE = re.compile(r"^t1-[0-9a-f]{16}$")


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


def test_parse_tarball_finds_skill_revs() -> None:
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\nname: foo\ndescription: Foo\n---\n",
            "skills/foo/README.md": b"ignored",
            "skills/bar/baz/SKILL.md": b"---\nname: baz\ndescription: Baz\n---\n",
        }
    )
    revs, contents, filtered = _parse_tarball(raw)
    assert set(revs) == {"skills/foo", "skills/bar/baz"}
    assert all(REV_RE.fullmatch(rev) for rev in revs.values())
    # 目录内非 SKILL.md 文件同样属于该单元的指纹域。
    only_skill = _parse_tarball(
        _make_tarball(
            {
                "skills/foo/SKILL.md": b"---\nname: foo\ndescription: Foo\n---\n",
                "skills/bar/baz/SKILL.md": b"---\nname: baz\ndescription: Baz\n---\n",
            }
        )
    )[0]
    assert only_skill["skills/foo"] != revs["skills/foo"]
    assert contents["skills/foo"] == "---\nname: foo\ndescription: Foo\n---\n"
    assert filtered == 0


def test_parse_tarball_skips_non_skill_files() -> None:
    raw = _make_tarball(
        {
            "skills/foo/README.md": b"readme",
            "not-a-skill.md": b"nope",
            "README.md": b"nope",
        }
    )
    revs, _contents, filtered = _parse_tarball(raw)
    assert revs == {}
    assert filtered == 0


def test_parse_tarball_filters_internal_paths() -> None:
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\nname: foo\ndescription: Real foo\n---\n",
            # 同名测试夹具：必须被过滤，不得按 tar 顺序覆盖真实技能。
            "tests/foo/SKILL.md": b"---\ndescription: Fixture foo\n---\n",
            "examples/demo/SKILL.md": b"---\ndescription: Demo\n---\n",
            ".github/skill/SKILL.md": b"---\ndescription: Config\n---\n",
            # 歧义词：作为中间目录段过滤，作为技能名保留。
            "e2e/helper/SKILL.md": b"---\ndescription: E2E helper\n---\n",
            "skills/e2e/SKILL.md": b"---\nname: e2e\ndescription: E2E skill\n---\n",
            # 状态词目录：任意段命中即过滤。
            "skills/deprecated/old/SKILL.md": b"---\ndescription: Old\n---\n",
        }
    )
    revs, contents, filtered = _parse_tarball(raw)
    assert filtered == 5
    assert set(revs) == {"skills/foo", "skills/e2e"}
    assert "tests/foo" not in contents
    assert contents["skills/e2e"] == "---\nname: e2e\ndescription: E2E skill\n---\n"


def test_parse_tarball_filters_nonpublic_frontmatter() -> None:
    raw = _make_tarball(
        {
            "skills/hidden/SKILL.md": b"---\nname: h\ndescription: x\nhidden: true\n---\n",
            "skills/deprecated/SKILL.md": b"---\nname: d\ndeprecated: yes\n---\n",
            "skills/unlisted/SKILL.md": b"---\nname: u\npublic: false\n---\n",
            "skills/ok/SKILL.md": b"---\nname: ok\ndescription: public\n---\n",
        }
    )
    revs, contents, filtered = _parse_tarball(raw)
    assert filtered == 3
    assert set(revs) == {"skills/ok"}
    assert contents["skills/ok"] == "---\nname: ok\ndescription: public\n---\n"


def test_parse_tarball_filters_invalid_frontmatter() -> None:
    """frontmatter 缺必备字段（非空 name + description）的 SKILL.md 视为无效
    文件：无法被 agent 发现/触发，不进入索引（对齐 agents-skills 判定）。"""
    raw = _make_tarball(
        {
            "skills/no-fm/SKILL.md": b"# no frontmatter\n",
            "skills/broken-fm/SKILL.md": b"---\ninvalid: [unclosed\n---\n",
            "skills/no-name/SKILL.md": b"---\ndescription: d\n---\n",
            "skills/no-desc/SKILL.md": b"---\nname: n\n---\n",
            "skills/empty-name/SKILL.md": b'---\nname: ""\ndescription: d\n---\n',
            "skills/blank-desc/SKILL.md": b'---\nname: n\ndescription: "  "\n---\n',
            "skills/list-name/SKILL.md": b"---\nname: [a, b]\ndescription: d\n---\n",
            "skills/ok/SKILL.md": b"---\nname: ok\ndescription: fine\n---\n",
        }
    )
    revs, _contents, filtered = _parse_tarball(raw)
    assert filtered == 7
    assert set(revs) == {"skills/ok"}


def test_parse_tarball_nested_skill_md_is_payload() -> None:
    """技能目录是自包含单元：其子树里的 SKILL.md 是该单元的 payload，
    不是独立候选（agent 一层发现，嵌套技能无法被独立触发），静默跳过、
    不计入过滤计数。认领是结构性的：父单元即使被内容过滤丢弃也拥有子树。"""
    ok = b"---\nname: n\ndescription: d\n---\n"
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": ok,
            "skills/foo/nested/SKILL.md": ok,  # foo 的 payload
            "skills/foo/nested/deep/SKILL.md": ok,  # 同上（foo 的认领覆盖整棵子树）
            "skills/category/bar/SKILL.md": ok,  # category 非技能单元，正常收录
            "skills/bad/SKILL.md": b"---\ndescription: no name\n---\n",  # S4 丢弃但认领
            "skills/bad/child/SKILL.md": ok,  # payload → 不单独收录
            "skills/hidden/SKILL.md": b"---\nname: h\ndescription: x\nhidden: true\n---\n",
            "skills/hidden/child/SKILL.md": ok,  # payload → 不单独收录
            "tests/x/SKILL.md": ok,  # S2 丢弃但认领
            "tests/x/y/SKILL.md": ok,  # payload → 不重复计数
        }
    )
    revs, contents, filtered = _parse_tarball(raw)
    assert set(revs) == {"skills/foo", "skills/category/bar"}
    assert "skills/foo/nested" not in contents
    assert filtered == 3  # bad(S4) + hidden(S3) + tests/x(S2)；嵌套 payload 不计


def test_parse_tarball_siblings_under_category_dirs_are_kept() -> None:
    """下探只在技能单元边界停止：无 SKILL.md 的分类目录继续下探。"""
    ok = b"---\nname: n\ndescription: d\n---\n"
    raw = _make_tarball(
        {
            "skills/a/b/c/SKILL.md": ok,
            "skills/a/b/d/SKILL.md": ok,
            "other/e/SKILL.md": ok,
        }
    )
    revs, _contents, filtered = _parse_tarball(raw)
    assert set(revs) == {"skills/a/b/c", "skills/a/b/d", "other/e"}
    assert filtered == 0


def test_is_invalid_frontmatter_requires_name_and_description() -> None:
    # 无 frontmatter / YAML 解析失败：无效。
    assert is_invalid_frontmatter("# no frontmatter\n")
    assert is_invalid_frontmatter("---\ninvalid: [unclosed\n---\n")
    # 缺任一必备字段 / 空白值 / 非字符串值：无效。
    assert is_invalid_frontmatter("---\ndescription: d\n---\n")
    assert is_invalid_frontmatter("---\nname: n\n---\n")
    assert is_invalid_frontmatter('---\nname: ""\ndescription: d\n---\n')
    assert is_invalid_frontmatter('---\nname: n\ndescription: "  "\n---\n')
    assert is_invalid_frontmatter("---\nname: [a]\ndescription: d\n---\n")
    # name + description 均为非空字符串：有效。
    assert not is_invalid_frontmatter("---\nname: n\ndescription: d\n---\n")


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
    revs = {"skills/b": "t1-b", "skills/a": "t1-a"}
    contents = {
        "skills/a": "---\ndescription: A\n---\n",
        "skills/b": "---\ndescription: B\n---\n",
    }
    assert build_skill_records(revs, contents) == [
        {"path": "skills/a", "rev": "t1-a", "description": "A"},
        {"path": "skills/b", "rev": "t1-b", "description": "B"},
    ]


def test_build_skill_records_missing_content_yields_empty_description() -> None:
    assert build_skill_records({"skills/a": "t1-a"}, {}) == [
        {"path": "skills/a", "rev": "t1-a", "description": ""}
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
