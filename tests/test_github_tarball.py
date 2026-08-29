"""End-to-end tests for the codeload tarball scan path (no real network).

Verifies that `get_skill_contents` parses a repo tarball in one download and
that `get_tree_shas` returns the {skill dir: blob sha} domain the scan step's
tree pre-check compares against.
"""

from __future__ import annotations

import io
import tarfile

from skills_index.github import get_skill_contents, get_tree_shas


def _make_tarball(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(f"repo-sha/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """Records requested URLs and serves a canned tarball."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self.requests: list[str] = []

    def get(self, url: str) -> _FakeResponse:
        self.requests.append(url)
        return _FakeResponse(self._content)


def test_get_skill_contents_downloads_tarball_once(monkeypatch) -> None:
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\ndescription: Foo\n---\n",
            # 同名测试夹具：过滤后不得覆盖真实技能
            "tests/foo/SKILL.md": b"---\ndescription: Fixture\n---\n",
            "skills/bar/SKILL.md": b"---\ndescription: Bar\n---\n",
        }
    )
    client = _FakeClient(raw)
    blobs, contents, filtered = get_skill_contents("owner/repo", "main", client=client)  # type: ignore[arg-type]

    assert client.requests == ["https://codeload.github.com/owner/repo/tar.gz/main"]
    assert set(blobs) == {"foo", "bar"}
    assert blobs["foo"][0] == "skills/foo"
    assert contents["skills/foo"] == "---\ndescription: Foo\n---\n"
    assert contents["skills/bar"] == "---\ndescription: Bar\n---\n"
    assert filtered == 1


class _FakeJsonResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        pass


class _FakeJsonClient:
    """Serves a canned JSON payload and records requested URLs."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.requests: list[str] = []

    def get(self, url: str) -> _FakeJsonResponse:
        self.requests.append(url)
        return _FakeJsonResponse(self._payload)


def test_get_tree_shas_keeps_only_public_skill_dirs(monkeypatch) -> None:
    """返回 {技能目录: blob sha}（与缓存 skillShas 同域），内部路径被过滤，
    agent 技能标准位置（.claude/skills 等）保留。"""
    payload = {
        "truncated": False,
        "tree": [
            {"type": "blob", "path": "skills/a/SKILL.md", "sha": "sha-a"},
            {"type": "blob", "path": "README.md", "sha": "sha-readme"},
            {"type": "tree", "path": "skills/a"},
            {"type": "blob", "path": "skills/b/SKILL.md", "sha": "sha-b"},
            # 内部路径（测试夹具 / 配置目录）：不进入比对域。
            {"type": "blob", "path": "tests/x/SKILL.md", "sha": "sha-t"},
            {"type": "blob", "path": ".github/skills/y/SKILL.md", "sha": "sha-g"},
            # agent 工具的公开技能标准位置：保留。
            {"type": "blob", "path": ".claude/skills/z/SKILL.md", "sha": "sha-c"},
        ],
    }
    client = _FakeJsonClient(payload)

    shas = get_tree_shas("owner/repo", "main", client=client)  # type: ignore[arg-type]

    assert shas == {
        "skills/a": "sha-a",
        "skills/b": "sha-b",
        ".claude/skills/z": "sha-c",
    }
    assert client.requests == ["/repos/owner/repo/git/trees/main?recursive=1"]


def test_get_tree_shas_truncated_returns_none(monkeypatch) -> None:
    """树截断（>100k 条目）时返回 None，调用方回退到 tarball 全量路径。"""
    payload = {
        "truncated": True,
        "tree": [{"type": "blob", "path": "skills/a/SKILL.md", "sha": "x"}],
    }
    client = _FakeJsonClient(payload)

    assert get_tree_shas("owner/repo", "main", client=client) is None  # type: ignore[arg-type]
