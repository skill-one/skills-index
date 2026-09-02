"""Tests for the standalone `pull` helper (no real network)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

import skills_index.pull as pull
from skills_index.http import HttpError

# --- parse_repo -----------------------------------------------------------

def test_parse_repo_accepts_slug_and_urls() -> None:
    assert pull.parse_repo("skill-one/skills-index") == "skill-one/skills-index"
    assert (
        pull.parse_repo("git@github.com:skill-one/skills-index.git")
        == "skill-one/skills-index"
    )
    assert (
        pull.parse_repo("https://github.com/skill-one/skills-index.git")
        == "skill-one/skills-index"
    )
    # trailing slash / no .git also fine
    assert (
        pull.parse_repo("https://github.com/foo/bar/") == "foo/bar"
    )


def test_parse_repo_rejects_non_repo() -> None:
    for bad in ("", "not-a-repo", "https://example.com/a/b", "just/one/two/three"):
        with pytest.raises(ValueError):
            pull.parse_repo(bad)


# --- pick_download_url ----------------------------------------------------

def test_pick_download_url_prefers_browser_and_falls_back() -> None:
    release = {
        "assets": [
            {"name": "cache.tar.gz", "browser_download_url": "https://x/cache"},
            {"name": "data.tar.gz", "browser_download_url": "https://x/data",
             "url": "https://api/x/data"},
        ]
    }
    assert pull.pick_download_url(release, "data.tar.gz") == "https://x/data"

    # No browser_download_url -> fall back to the asset API url.
    release2 = {"assets": [{"name": "data.tar.gz", "url": "https://api/x/data"}]}
    assert pull.pick_download_url(release2, "data.tar.gz") == "https://api/x/data"


def test_pick_download_url_missing_raises() -> None:
    release = {"assets": [{"name": "cache.tar.gz", "browser_download_url": "u"}]}
    with pytest.raises(KeyError) as exc:
        pull.pick_download_url(release, "data.tar.gz")
    assert "cache.tar.gz" in str(exc.value)


# --- safe_extract ---------------------------------------------------------

def _make_data_tar(dest: Path) -> tarfile.TarInfo:
    """Write a data.tar.gz mirroring the published layout into `dest`."""
    index_jsonl = b'{"skillId":"a"}\n{"skillId":"b"}\n'
    meta = json.dumps(
        {"formatVersion": 4, "generatedAt": "2026-09-01T00:00:00Z",
         "counts": {"total": 2}}
    ).encode("utf-8")
    files = {
        "data/index/index.jsonl": index_jsonl,
        "data/index/index-meta.json": meta,
        "data/run-summary.md": b"# run\n",
    }
    tar_path = dest / "data.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for name, blob in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(blob)
            tf.addfile(info, io.BytesIO(blob))
    return tar_path


def test_safe_extract_writes_files(tmp_path: Path) -> None:
    tar_path = _make_data_tar(tmp_path)
    out = tmp_path / "snapshot"
    extracted = pull.safe_extract(tar_path, out)
    assert (out / "data/index/index.jsonl").read_bytes() == b'{"skillId":"a"}\n{"skillId":"b"}\n'
    assert sorted(extracted) == [
        "data/index/index-meta.json",
        "data/index/index.jsonl",
        "data/run-summary.md",
    ]


def test_safe_extract_rejects_traversal(tmp_path: Path) -> None:
    tar_path = tmp_path / "evil.tar.gz"
    blob = b"nope"
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(blob)
        tf.addfile(info, io.BytesIO(blob))
    with pytest.raises(RuntimeError, match="unsafe path"):
        pull.safe_extract(tar_path, tmp_path / "out")
    assert not (tmp_path.parent / "escape.txt").exists()


def test_safe_extract_rejects_absolute(tmp_path: Path) -> None:
    tar_path = tmp_path / "abs.tar.gz"
    blob = b"nope"
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="/tmp/abs-escape.txt")
        info.size = len(blob)
        tf.addfile(info, io.BytesIO(blob))
    with pytest.raises(RuntimeError, match="unsafe path"):
        pull.safe_extract(tar_path, tmp_path / "out")


def test_safe_extract_rejects_symlink(tmp_path: Path) -> None:
    tar_path = tmp_path / "link.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo(name="data/sneaky")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(RuntimeError, match="non-regular member"):
        pull.safe_extract(tar_path, tmp_path / "out")


# --- summarize ------------------------------------------------------------

def test_summarize_reports_meta_and_line_count(tmp_path: Path) -> None:
    _make_data_tar(tmp_path)
    pull.safe_extract(tmp_path / "data.tar.gz", tmp_path / "snapshot")
    summary = pull.summarize(tmp_path / "snapshot")
    assert summary["index_lines"] == 2
    assert summary["meta"]["formatVersion"] == 4
    assert summary["meta"]["counts"]["total"] == 2
    assert "data/index/index.jsonl" in summary["files"]


def test_summarize_flags_count_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    snap = tmp_path / "snapshot" / "data/index"
    snap.mkdir(parents=True)
    (snap / "index.jsonl").write_text('{"a":1}\n{"b":2}\n{"c":3}\n')
    (snap / "index-meta.json").write_text(json.dumps({"counts": {"total": 1}}))
    pull.summarize(tmp_path / "snapshot")
    out = capsys.readouterr().out
    assert "index.jsonl lines: 3" in out
    assert "MISMATCH" in out or "⚠" in out


# --- human size -----------------------------------------------------------

def test_human_size_units() -> None:
    assert pull._human_size(512) == "512B"
    assert pull._human_size(2048) == "2.0KB"
    assert pull._human_size(5 * 1024 * 1024) == "5.0MB"


# --- run_pull end-to-end (fake release + fake download) -------------------

def test_run_pull_downloads_extracts_and_summarizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real data.tar.gz built to stand in for the downloaded asset bytes.
    payload_src = tmp_path / "payload"
    payload_src.mkdir()
    _make_data_tar(payload_src)
    archive_bytes = (payload_src / "data.tar.gz").read_bytes()

    def fake_get_json(client: object, url: str) -> dict:
        assert url == "/repos/skill-one/skills-index/releases/latest"
        return {
            "tag_name": "data-20260901T000000Z",
            "published_at": "2026-09-01T00:00:05Z",
            "assets": [{"name": "data.tar.gz",
                        "browser_download_url": "https://obj/data.tar.gz"}],
        }

    def fake_download(url: str, dest: Path, *, client: object | None = None) -> int:
        assert url == "https://obj/data.tar.gz"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive_bytes)
        return len(archive_bytes)

    monkeypatch.setattr(pull, "get_json", fake_get_json)
    monkeypatch.setattr(pull, "download_file", fake_download)

    dest_root = tmp_path / "pulled"
    summary = pull.run_pull(
        "skill-one/skills-index", dest_root=dest_root, client=object()
    )

    snap = dest_root / "data-20260901T000000Z"
    assert summary["tag"] == "data-20260901T000000Z"
    assert summary["extracted_files"] == 3
    assert summary["index_lines"] == 2
    assert summary["meta"]["counts"]["total"] == 2
    # Archive is present only transiently; the extracted tree remains, zip gone.
    assert (snap / "data/index/index.jsonl").exists()
    assert not (snap / "data.tar.gz").exists()


def test_run_pull_repull_wipes_stale_tag_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload_src = tmp_path / "payload"
    payload_src.mkdir()
    _make_data_tar(payload_src)
    archive_bytes = (payload_src / "data.tar.gz").read_bytes()

    monkeypatch.setattr(
        pull, "get_json",
        lambda client, url: {"tag_name": "data-X",
                             "assets": [{"name": "data.tar.gz",
                                         "browser_download_url": "u"}]},
    )
    monkeypatch.setattr(
        pull, "download_file",
        lambda url, dest, *, client=None: (
            dest.parent.mkdir(parents=True, exist_ok=True),
            dest.write_bytes(archive_bytes),
            len(archive_bytes),
        )[-1],
    )
    dest_root = tmp_path / "pulled"
    snap = dest_root / "data-X" / "data"
    snap.mkdir(parents=True)
    (snap / "stale.txt").write_text("old")

    pull.run_pull("a/b", dest_root=dest_root, client=object())
    # The pre-existing tag dir was wiped, so the stale file is gone.
    assert not (snap / "stale.txt").exists()
    assert (snap / "index/index.jsonl").exists()


def test_run_pull_no_tag_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pull, "get_json", lambda client, url: {"assets": []})
    with pytest.raises(RuntimeError, match="no tag_name"):
        pull.run_pull("a/b", dest_root=tmp_path / "pulled", client=object())


def test_run_pull_missing_asset_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pull, "get_json",
        lambda client, url: {"tag_name": "data-Y", "assets": []},
    )
    with pytest.raises(KeyError):
        pull.run_pull("a/b", dest_root=tmp_path / "pulled", client=object())


def test_run_pull_download_error_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pull, "get_json",
        lambda client, url: {"tag_name": "data-Z",
                             "assets": [{"name": "data.tar.gz",
                                         "browser_download_url": "u"}]},
    )

    def boom(url: str, dest: Path, *, client: object | None = None) -> int:
        raise HttpError("404 on u", status=404)

    monkeypatch.setattr(pull, "download_file", boom)
    with pytest.raises(HttpError):
        pull.run_pull("a/b", dest_root=tmp_path / "pulled", client=object())


# --- main() error handling ------------------------------------------------

def test_pull_main_returns_1_on_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(repo: str, **kw: object) -> dict:
        raise ValueError("bad repo")

    monkeypatch.setattr(pull, "run_pull", boom)
    assert pull.main(["nonsense-not-a-repo"]) == 1
    assert "failed" in capsys.readouterr().err
