"""End-to-end tests for the codeload tarball scan path (no real network).

Verifies that `get_skill_contents` downloads a repo tarball exactly once and
caches the parsed result for the rest of the run.
"""

from __future__ import annotations

import io
import tarfile

from skills_index import github
from skills_index.github import get_skill_contents


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


def _reset_cache(monkeypatch) -> None:
    monkeypatch.setattr(github, "_tarball_scan", {})


def test_get_skill_contents_downloads_tarball_once(monkeypatch) -> None:
    _reset_cache(monkeypatch)
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


def test_get_skill_contents_caches_per_run(monkeypatch) -> None:
    _reset_cache(monkeypatch)
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": b"---\ndescription: Foo\n---\n",
            "skills/bar/SKILL.md": b"---\ndescription: Bar\n---\n",
        }
    )
    client = _FakeClient(raw)

    # A second call for the same source must reuse the cached tarball.
    first = get_skill_contents("owner/repo", "main", client=client)  # type: ignore[arg-type]
    second = get_skill_contents("owner/repo", "main", client=client)  # type: ignore[arg-type]

    assert first == second
    assert client.requests == ["https://codeload.github.com/owner/repo/tar.gz/main"]
