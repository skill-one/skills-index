"""Join skills.sh data (identity, popularity) with GitHub enrichment into index.jsonl."""

from __future__ import annotations

import datetime
import sys

from .config import INDEX_FORMAT_VERSION, INDEX_JSONL, INDEX_META_JSON, JSON, Record
from .io_utils import write_json, write_jsonl

# Column order for the emitted index.jsonl records.
_INDEX_FIELD_ORDER = (
    "skillId",
    "source",
    "stars",
    "description",
    "installs",
    "weeklyInstalls",
    "path",
    "lastCommitAt",
)

# skills.sh fields carried by `fetch` in memory but never published.
_INDEX_UNPUBLISHED = ("name", "isOfficial")


def _ordered(rec: Record) -> Record:
    """Return `rec` with keys ordered for index.jsonl output."""
    out: Record = {}
    for k in _INDEX_FIELD_ORDER:
        if k in rec:
            out[k] = rec[k]
    for k, v in rec.items():
        if k not in out and k not in _INDEX_UNPUBLISHED:
            out[k] = v
    return out


def _dedup_skills(records: list[Record]) -> tuple[list[Record], int]:
    """Drop cross-repo duplicates: same skillId + same non-empty description.

    skillId 单独相同不足以判定重复（同名不同实现真实存在）；叠加
    description 完全一致才视为同一技能的镜像，保留 installs 更高者（记录
    顺序破平局，即 skills.sh 排名靠前者）。description 为空不参与去重。
    Returns ``(kept, dropped_count)``.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for i, rec in enumerate(records):
        sid = str(rec.get("skillId", ""))
        desc = str(rec.get("description", "") or "").strip()
        if sid and desc:
            groups.setdefault((sid, desc), []).append(i)
    drop: set[int] = set()
    for idxs in groups.values():
        if len(idxs) < 2:
            continue
        winner = max(idxs, key=lambda i: (records[i].get("installs") or 0, -i))
        drop.update(i for i in idxs if i != winner)
    if not drop:
        return records, 0
    return [r for i, r in enumerate(records) if i not in drop], len(drop)


def _key(rec: Record) -> tuple[str, str]:
    return (str(rec.get("source", "")), str(rec.get("skillId", "")))


def run_index(
    fetched: list[Record],
    scanned: list[JSON],
    *,
    now: str | None = None,
    tag: str = "",
) -> tuple[list[Record], dict[str, JSON]]:
    """Merge fetched + scanned data into index.jsonl.

    - join on (source, skillId): skills.sh supplies identity and popularity,
      the scan supplies path / description / lastCommitAt / stars;
    - fetched skills with no located counterpart are dropped: the repo no
      longer confirms them (definitive evidence only);
    - records keep the skills.sh ranking order; cross-repo duplicates (same
      skillId + same non-empty description) keep only the highest-installs
      copy;
    - `lastCommitAt` is factual data taken verbatim from the scan (the
      skill directory's most recent commit time), never stamped by the run.

    `now` (index-meta.json `generatedAt`) and `tag` (the release tag
    recorded in index-meta.json) are injectable for testability.
    Returns ``(index_records, summary)``.
    """
    now = now or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    fetched_by_key: dict[tuple[str, str], Record] = {}
    rank: dict[tuple[str, str], int] = {}
    for i, rec in enumerate(fetched):
        k = _key(rec)
        if k not in fetched_by_key:
            fetched_by_key[k] = rec
            rank[k] = i

    matched: list[tuple[int, Record]] = []
    for rec in scanned:
        k = _key(rec)
        base = fetched_by_key.get(k)
        if base is not None:  # defensive: the scan only locates fetched skills
            matched.append((rank[k], {**base, **rec}))
    matched.sort(key=lambda t: t[0])
    result = [_ordered(rec) for _, rec in matched]

    result, deduped = _dedup_skills(result)

    meta: dict[str, JSON] = {
        "formatVersion": INDEX_FORMAT_VERSION,
        "generatedAt": now,
        "counts": {"total": len(result)},
    }
    if tag:
        meta["tag"] = tag
    write_jsonl(INDEX_JSONL, result)
    write_json(INDEX_META_JSON, meta)

    summary: dict[str, JSON] = {
        "fetched": len(fetched),
        "confirmed": len(matched),
        "not_in_repo": len(fetched) - len(matched),
        "deduped": deduped,
        "index": len(result),
    }
    print(
        f"[index] {len(matched)} confirmed, dropped {summary['not_in_repo']} "
        f"not-in-repo, deduped {deduped} cross-repo "
        f"-> {len(result)} in {INDEX_JSONL}",
        file=sys.stderr,
    )
    return result, summary
