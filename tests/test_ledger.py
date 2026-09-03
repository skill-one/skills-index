"""Tests for the cross-run rev ledger (no network, no clock).

The ledger is the only piece that turns a content fingerprint into a date, so
these tests focus on its contract: `firstSeenAt` moves exactly when `rev` does,
never for anything else.
"""

from __future__ import annotations

from pathlib import Path

from skills_index.cache import RevLedger
from skills_index.io_utils import read_jsonl

DAY1 = "2026-08-30T00:00:00Z"
DAY2 = "2026-08-31T00:00:00Z"


def _rec(source: str = "o/r", path: str = "skills/a", rev: str = "aaa") -> dict:
    return {"source": source, "path": path, "rev": rev, "skillId": path.rsplit("/", 1)[-1]}


def _records() -> list[dict]:
    return [_rec(), _rec(path="skills/b", rev="bbb"), _rec(source="other/r", path="s/c")]


def _stamp(records: list[dict], ledger: RevLedger, now: str) -> dict[str, str]:
    ledger.stamp(records, now)
    return {(r["source"], r["path"]): str(r["firstSeenAt"]) for r in records}


def test_first_run_seeds_every_date(tmp_path: Path) -> None:
    """冷启动（账本不存在）：全部落在本轮时间，不报错。"""
    ledger = RevLedger.load(tmp_path / "absent.jsonl")

    dates = _stamp(_records(), ledger, DAY1)

    assert set(dates.values()) == {DAY1}
    assert len(ledger.entries) == 3


def test_unchanged_revs_keep_their_date(tmp_path: Path) -> None:
    """幂等：同样内容再跑一次（哪怕换了一天），日期一律不动。"""
    path = tmp_path / "ledger.jsonl"
    ledger = RevLedger.load(path)
    ledger.stamp(_records(), DAY1)
    ledger.save(path)

    again = RevLedger.load(path)
    records = _records()
    refreshed, total = again.stamp(records, DAY2)

    assert refreshed == 0
    assert total == 3
    assert {r["firstSeenAt"] for r in records} == {DAY1}


def test_only_the_changed_skill_gets_a_new_date(tmp_path: Path) -> None:
    """同仓库别处推送不会推进日期：只有 rev 变的那条前进。"""
    ledger = RevLedger.load(tmp_path / "ledger.jsonl")
    ledger.stamp(_records(), DAY1)

    changed = _records()
    changed[1]["rev"] = "bbb-edited"
    dates = _stamp(changed, ledger, DAY2)

    assert dates[("o/r", "skills/b")] == DAY2
    assert dates[("o/r", "skills/a")] == DAY1
    assert dates[("other/r", "s/c")] == DAY1


def test_repo_absent_this_run_keeps_its_rows(tmp_path: Path) -> None:
    """整仓本轮没扫到（失败/下架前）不带新信息：保留旧行，避免全仓技能被误标新。"""
    ledger = RevLedger.load(tmp_path / "ledger.jsonl")
    ledger.stamp(_records(), DAY1)

    refreshed, total = ledger.stamp([_rec()], DAY2)  # other/r 本轮缺席

    rows = {(r["source"], r["path"]): r["firstSeenAt"] for r in ledger.entries.values()}
    assert rows == {("o/r", "skills/a"): DAY1, ("other/r", "s/c"): DAY1}
    assert (refreshed, total) == (0, 2)


def test_skill_removed_from_scanned_repo_drops_its_row(tmp_path: Path) -> None:
    """仓库本轮扫到了、技能不见了 = 真下架：删行（将来回归按新版本记日期）。"""
    ledger = RevLedger.load(tmp_path / "ledger.jsonl")
    ledger.stamp(_records(), DAY1)

    # other/r 本轮缺席 -> 保留；o/r 扫到了但 skills/b 没了 -> 删除。
    scanned = [_rec(), _rec(source="other/r", path="s/c")]
    _refreshed, total = ledger.stamp(scanned, DAY2)

    rows = {(r["source"], r["path"]) for r in ledger.entries.values()}
    assert rows == {("o/r", "skills/a"), ("other/r", "s/c")}
    assert total == 2


def test_rev_rollback_resets_the_date(tmp_path: Path) -> None:
    """回滚到旧内容：账本只认「上一次记录的 rev」，日期按当前运行重记。"""
    ledger = RevLedger.load(tmp_path / "ledger.jsonl")
    ledger.stamp([_rec(rev="aaa")], DAY1)
    ledger.stamp([_rec(rev="new")], DAY2)

    dates = _stamp([_rec(rev="aaa")], ledger, DAY2)

    assert dates[("o/r", "skills/a")] == DAY2


def test_renamed_path_is_treated_as_new(tmp_path: Path) -> None:
    """path 是账本主键的一部分：改名（内容不变）宁可显示为新，也不沿用旧日期。"""
    ledger = RevLedger.load(tmp_path / "ledger.jsonl")
    ledger.stamp([_rec(path="skills/old-name")], DAY1)

    dates = _stamp([_rec(path="skills/new-name")], ledger, DAY2)

    assert dates[("o/r", "skills/new-name")] == DAY2


def test_records_without_rev_are_left_untouched(tmp_path: Path) -> None:
    """旧缓存没有 rev：不写 firstSeenAt，也不覆盖已有行——未知不等于相同。"""
    ledger = RevLedger.load(tmp_path / "ledger.jsonl")
    ledger.stamp([_rec()], DAY1)

    legacy = {"source": "o/r", "path": "skills/a"}
    refreshed, total = ledger.stamp([legacy], DAY2)

    assert "firstSeenAt" not in legacy
    assert (refreshed, total) == (0, 1)
    assert ledger.entries[("o/r", "skills/a")]["firstSeenAt"] == DAY1


def test_load_drops_malformed_rows(tmp_path: Path) -> None:
    """缺字段/坏行：忽略而非报错，等价于该技能没有历史。"""
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        '{"source":"o/r","path":"skills/a","rev":"aaa","firstSeenAt":"2026-01-01T00:00:00Z"}\n'
        '{"source":"o/r","path":"skills/no-rev","firstSeenAt":"2026-01-01T00:00:00Z"}\n'
        '{"path":"skills/no-source","rev":"bbb","firstSeenAt":"2026-01-01T00:00:00Z"}\n'
        '{"source":"o/r","path":"skills/no-date","rev":"ccc"}\n',
        encoding="utf-8",
    )

    ledger = RevLedger.load(path)

    assert list(ledger.entries) == [("o/r", "skills/a")]


def test_save_is_sorted_and_roundtrips(tmp_path: Path) -> None:
    """落盘按 (source, path) 排序：账本进 cache.tar.gz，稳定顺序才有可读 diff。"""
    path = tmp_path / "ledger.jsonl"
    records = [
        _rec(source="z/r", path="skills/a"),
        _rec(source="a/r", path="skills/z"),
        _rec(source="a/r", path="skills/a"),
    ]
    ledger = RevLedger.load(path)
    ledger.stamp(records, DAY1)
    ledger.save(path)

    keys = [(str(r["source"]), str(r["path"])) for r in read_jsonl(path)]
    assert keys == sorted(keys)
    assert RevLedger.load(path).entries == ledger.entries
