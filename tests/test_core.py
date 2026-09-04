"""Tests for pure-logic helpers (no network, no git)."""

from __future__ import annotations

from skills_index import config
from skills_index.fetch import filter_github
from skills_index.github import extract_description, parse_frontmatter
from skills_index.io_utils import read_json, read_jsonl, write_json, write_jsonl


def test_is_github_source() -> None:
    assert config.is_github_source("owner/repo")
    assert config.is_github_source(" owner/repo ")
    assert not config.is_github_source("https://example.com/owner/repo")
    assert not config.is_github_source("not-a-source")
    assert not config.is_github_source("owner")


def test_jsonl_roundtrip(tmp_path) -> None:
    path = tmp_path / "x.jsonl"
    records = [{"a": 1, "b": "中文"}, {"a": 2}]
    write_jsonl(path, records)
    assert read_jsonl(path) == records
    assert read_jsonl(tmp_path / "nope.jsonl") == []


def test_json_roundtrip(tmp_path) -> None:
    path = tmp_path / "x.json"
    write_json(path, {"k": "v"})
    assert read_json(path) == {"k": "v"}
    assert read_json(tmp_path / "nope.json", default={}) == {}
    path.write_text("not json", encoding="utf-8")
    assert read_json(path, default={}) == {}


def test_parse_frontmatter() -> None:
    assert parse_frontmatter("---\nname: foo\ndescription: Bar\n---\nbody") == {
        "name": "foo",
        "description": "Bar",
    }
    # No frontmatter / unterminated / unparseable / non-mapping all yield {}.
    assert parse_frontmatter("plain text") == {}
    assert parse_frontmatter("---\nname: foo\nbody") == {}
    assert parse_frontmatter("---\n: :\n---\n") == {}
    assert parse_frontmatter("---\n- just\n- a list\n---\n") == {}


def test_extract_description() -> None:
    assert (
        extract_description("---\nname: foo\ndescription:  Does things \n---\n")
        == "Does things"
    )
    assert extract_description("---\nname: foo\n---\n") == ""
    assert extract_description("") == ""


def test_filter_github() -> None:
    skills = [
        {"source": "a/b"},
        {"source": "https://example.com/a/b"},
        {"source": "plain"},
        {"source": "c/d"},
    ]
    kept, dropped = filter_github(skills)
    assert [s["source"] for s in kept] == ["a/b", "c/d"]
    assert dropped == 2
