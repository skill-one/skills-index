"""End-to-end tests for the codeload tarball scan path (no real network).

Verifies that `get_skill_contents` parses a repo tarball in one download and
publishes a whole-directory `rev` per skill, and that `get_skill_tree_shas`
returns the {skill dir: git tree sha} map the scan step's tree pre-check
compares against. Stdlib only (`tarfile`, `re`) — no new dependencies.
"""

from __future__ import annotations

import io
import re
import tarfile

from skills_index.github import get_skill_contents, get_skill_tree_shas

REV_RE = re.compile(r"^t1-[0-9a-f]{16}$")

_SKILL = b"---\nname: foo\ndescription: Foo\n---\n"


def _make_tarball(
    files: dict[str, bytes], modes: dict[str, int] | None = None
) -> bytes:
    """Build a repo tarball; `modes` overrides member permissions (by name)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(f"repo-sha/{name}")
            info.size = len(data)
            info.mode = (modes or {}).get(name, 0o644)
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


def _scan(
    files: dict[str, bytes], modes: dict[str, int] | None = None
) -> tuple[dict[str, str], dict[str, str], int]:
    client = _FakeClient(_make_tarball(files, modes))
    return get_skill_contents("owner/repo", "main", client=client)  # type: ignore[arg-type]


def test_get_skill_contents_downloads_tarball_once() -> None:
    raw = _make_tarball(
        {
            "skills/foo/SKILL.md": _SKILL,
            # 同名测试夹具：过滤后不得覆盖真实技能
            "tests/foo/SKILL.md": b"---\ndescription: Fixture\n---\n",
            "skills/bar/SKILL.md": b"---\nname: bar\ndescription: Bar\n---\n",
        }
    )
    client = _FakeClient(raw)
    revs, contents, filtered = get_skill_contents("owner/repo", "main", client=client)  # type: ignore[arg-type]

    assert client.requests == ["https://codeload.github.com/owner/repo/tar.gz/main"]
    assert set(revs) == {"skills/foo", "skills/bar"}
    assert REV_RE.fullmatch(revs["skills/foo"])
    assert contents["skills/foo"] == _SKILL.decode()
    assert contents["skills/bar"] == "---\nname: bar\ndescription: Bar\n---\n"
    assert filtered == 1


def test_rev_covers_every_file_in_the_skill_directory() -> None:
    """目录内任何文件变（内容 / 新增 / 改名 / 删除）都必须换 rev；
    目录外的文件必须无关。"""
    base = {"skills/foo/SKILL.md": _SKILL, "skills/foo/scripts/run.sh": b"echo a\n"}
    rev = _scan(base)[0]["skills/foo"]

    assert _scan({**base, "skills/foo/scripts/run.sh": b"echo b\n"})[0]["skills/foo"] != rev
    assert _scan({**base, "skills/foo/assets/logo.svg": b"x\n"})[0]["skills/foo"] != rev
    # 改名（内容不变）也是变更：安装方拿到的路径变了。
    assert _scan({**base, "skills/foo/scripts/run2.sh": b"echo a\n"})[0]["skills/foo"] != rev
    # 删除附属文件同样是变更。
    assert _scan({"skills/foo/SKILL.md": _SKILL})[0]["skills/foo"] != rev
    # rev 只覆盖技能目录：仓库根与兄弟技能的改动不影响它。
    assert _scan({**base, "README.md": b"hello\n"})[0]["skills/foo"] == rev
    siblings = {**base, "skills/bar/SKILL.md": b"---\nname: b\ndescription: B\n---\n"}
    assert _scan(siblings)[0]["skills/foo"] == rev


def test_rev_is_stable_and_path_relative() -> None:
    """同一份内容无论枚举顺序、无论在哪个仓库/路径，都得到同一个 rev。"""
    files = {"skills/foo/SKILL.md": _SKILL, "skills/foo/scripts/run.sh": b"echo a\n"}
    first = _scan(files)[0]
    again = _scan(dict(reversed(list(files.items()))))[0]
    assert first == again

    moved = _scan(
        {"other/place/foo/SKILL.md": _SKILL, "other/place/foo/scripts/run.sh": b"echo a\n"}
    )[0]
    assert moved["other/place/foo"] == first["skills/foo"]


def test_rev_follows_the_exec_bit() -> None:
    """chmod +x 决定脚本能否被执行，属于内容变更（git mode 进入指纹）。"""
    files = {"skills/foo/SKILL.md": _SKILL, "skills/foo/run.sh": b"echo a\n"}
    plain = _scan(files)[0]["skills/foo"]
    execable = _scan(files, modes={"skills/foo/run.sh": 0o755})[0]["skills/foo"]
    assert plain != execable
    # git 只记录可执行位，其余权限位不影响内容。
    assert _scan(files, modes={"skills/foo/run.sh": 0o600})[0]["skills/foo"] == plain


def test_nested_skill_md_counts_as_payload_of_the_outer_unit() -> None:
    """嵌套 SKILL.md 不独立成技能，但其内容属于外层单元的指纹域。"""
    outer = {"skills/foo/SKILL.md": _SKILL}
    rev = _scan(outer)[0]["skills/foo"]
    with_payload = {
        **outer,
        "skills/foo/nested/SKILL.md": b"---\nname: n\ndescription: N\n---\n",
    }
    revs, contents = _scan(with_payload)[:2]
    assert revs["skills/foo"] != rev
    assert set(contents) == {"skills/foo"}


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


def test_get_skill_tree_shas_keeps_only_public_skill_dirs() -> None:
    """返回 {技能目录: git tree sha}（与缓存 skillTreeShas 同域）：内部路径被过
    滤，agent 技能标准位置（.claude/skills 等）保留，非技能目录的 tree 条目忽略。"""
    payload = {
        "truncated": False,
        "tree": [
            {"type": "blob", "path": "skills/a/SKILL.md", "sha": "sha-a"},
            {"type": "tree", "path": "skills/a", "sha": "tree-a"},
            {"type": "tree", "path": "skills/cat", "sha": "tree-cat"},
            {"type": "blob", "path": "README.md", "sha": "sha-readme"},
            # 内部路径（测试夹具 / 配置目录）：不进入比对域。
            {"type": "blob", "path": "tests/x/SKILL.md", "sha": "sha-t"},
            {"type": "tree", "path": "tests/x", "sha": "tree-t"},
            {"type": "blob", "path": ".github/skills/y/SKILL.md", "sha": "sha-g"},
            {"type": "tree", "path": ".github/skills/y", "sha": "tree-g"},
            # agent 工具的公开技能标准位置：保留。
            {"type": "blob", "path": ".claude/skills/z/SKILL.md", "sha": "sha-c"},
            {"type": "tree", "path": ".claude/skills/z", "sha": "tree-c"},
        ],
    }
    client = _FakeJsonClient(payload)

    shas = get_skill_tree_shas("owner/repo", "main", client=client)  # type: ignore[arg-type]

    assert shas == {"skills/a": "tree-a", ".claude/skills/z": "tree-c"}
    assert client.requests == ["/repos/owner/repo/git/trees/main?recursive=1"]


def test_get_skill_tree_shas_excludes_nested_skill_dirs() -> None:
    """嵌套 SKILL.md 是父技能单元的 payload，不独立成条目；分类目录（非技能
    单元）下的技能正常保留，与 tarball 扫描域一致。"""
    payload = {
        "truncated": False,
        "tree": [
            {"type": "blob", "path": "skills/a/SKILL.md", "sha": "sha-a"},
            {"type": "tree", "path": "skills/a", "sha": "tree-a"},
            {"type": "blob", "path": "skills/a/nested/SKILL.md", "sha": "sha-n"},
            {"type": "tree", "path": "skills/a/nested", "sha": "tree-n"},
            {"type": "blob", "path": "skills/cat/b/SKILL.md", "sha": "sha-b"},
            {"type": "tree", "path": "skills/cat/b", "sha": "tree-b"},
            {"type": "blob", "path": "tests/x/SKILL.md", "sha": "sha-t"},
            {"type": "tree", "path": "tests/x", "sha": "tree-t"},
        ],
    }
    client = _FakeJsonClient(payload)

    shas = get_skill_tree_shas("owner/repo", "main", client=client)  # type: ignore[arg-type]

    assert shas == {"skills/a": "tree-a", "skills/cat/b": "tree-b"}


def test_get_skill_tree_shas_truncated_returns_none() -> None:
    """树截断（>100k 条目）时返回 None，调用方回退到 tarball 全量路径。"""
    payload = {
        "truncated": True,
        "tree": [{"type": "blob", "path": "skills/a/SKILL.md", "sha": "x"}],
    }
    client = _FakeJsonClient(payload)

    assert get_skill_tree_shas("owner/repo", "main", client=client) is None  # type: ignore[arg-type]
