"""Cross-run cache state: per-repo scan fingerprints plus the rev ledger.

``cache/by-source/<owner>__<repo>/`` — one directory per repository holds the
state the scan step needs (see config.CACHE_DIR):

- ``meta.json``     state fingerprint. A single tagged shape: every record
                    carries ``schemaVersion`` / ``status`` / ``source`` /
                    ``lastScanned``, plus the fields of its status:
  - ``ok``          normal cache: ``branch`` / ``pushedAt`` / ``stars`` /
                    ``skillCount`` / ``skillTreeShas`` ({path: git tree sha} of
                    the repo's public skill directories — the same domain the
                    Trees API reports, and the fingerprint the mirror dedup
                    groups on).
  - ``filtered``    repo excluded by the skillCount cap: ``pushedAt`` /
                    ``skillCount`` are kept so later runs skip it without a
                    tarball download until a new push triggers a full
                    re-adjudication (a push may bring it back under the cap).
  - ``tombstoned``  dedup loser (byte-identical mirror of another repo):
                    ``dedupedInto`` / ``winnerPushedAt`` / ``pushedAt``,
                    skipped with zero network I/O until either side pushes.
- ``scanned.jsonl`` the repo's skill records (path, ``rev``, description);
                    exists only for ``ok``.

``cache/rev-ledger.jsonl`` (config.REV_LEDGER, see :class:`RevLedger`) — one
row per published skill holding its current ``rev`` and the run that first
recorded it, which is what lets the index step publish a ``firstSeenAt`` that
moves only when the content does.

Every rewrite purges files outside this contract, so leftovers from older
formats cannot accumulate.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .config import JSON, META_FILE, SCANNED_FILE, SCHEMA_VERSION, Record
from .io_utils import read_json, read_jsonl, write_json, write_jsonl


class RepoCache:
    """Read, inspect, and rewrite one repo dir's incremental cache."""

    def __init__(self, repo_dir: Path, source: str, meta: Record) -> None:
        self.repo_dir = repo_dir
        self.source = source
        self.meta = meta

    @classmethod
    def load(cls, repo_dir: Path, source: str) -> RepoCache:
        """Read the repo's meta.json (empty meta when absent or corrupt)."""
        return cls(repo_dir, source, read_json(repo_dir / META_FILE, default={}) or {})

    # --- meta views -----------------------------------------------------

    @property
    def status(self) -> str:
        """ok / filtered / tombstoned; ``""`` when the meta is missing or
        predates the tagged format (such caches are always rebuilt)."""
        return str(self.meta.get("status") or "")

    @property
    def pushed_at(self) -> str:
        return str(self.meta.get("pushedAt") or "")

    @property
    def skill_count(self) -> int:
        return int(self.meta.get("skillCount") or 0)

    @property
    def skill_tree_shas(self) -> dict[str, str]:
        """{skill dir: git tree sha} of the cached public skills."""
        shas = self.meta.get("skillTreeShas")
        if not isinstance(shas, dict):
            return {}
        return {str(k): str(v) for k, v in shas.items()}

    @property
    def has_data(self) -> bool:
        """True when the repo's scanned.jsonl is present (ok-status contract)."""
        return (self.repo_dir / SCANNED_FILE).exists()

    @property
    def schema_stale(self) -> bool:
        """True when the on-disk meta predates the current scan format."""
        return self.meta.get("schemaVersion") != SCHEMA_VERSION

    # --- mutations ------------------------------------------------------

    def purge_foreign_files(self) -> None:
        """Delete files outside the cache contract (older formats left e.g.
        a per-dir ``fetched.jsonl`` behind); every rewrite normalizes the
        directory so legacy junk cannot survive across runs."""
        if not self.repo_dir.exists():
            return
        for path in self.repo_dir.iterdir():
            if path.is_file() and path.name not in (META_FILE, SCANNED_FILE):
                path.unlink()

    def write_ok(
        self,
        *,
        branch: str,
        pushed: str,
        stars: int,
        now: str,
        skills: list[Record],
        skill_tree_shas: dict[str, str],
    ) -> None:
        """Persist a full scan result: scanned.jsonl + ``ok`` meta.

        `skill_tree_shas` may be empty when this run could not get a Trees
        baseline (cold cache, truncated tree, failed request). That costs the
        next push one redundant tarball download and nothing else: the empty map
        never matches a real tree, so the pre-check falls through and records a
        fresh baseline then.
        """
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        write_jsonl(self.repo_dir / SCANNED_FILE, skills)
        self.meta = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "ok",
            "source": self.source,
            "branch": branch,
            "pushedAt": pushed,
            "stars": stars,
            "lastScanned": now,
            "skillCount": len(skills),
            "skillTreeShas": skill_tree_shas,
        }
        self._write_meta()

    def write_filtered(self, *, pushed: str, now: str, skill_count: int) -> None:
        """Tombstone a repo excluded by the skillCount cap (scan data removed,
        the count fingerprint kept for future runs)."""
        self._drop_scanned()
        self.meta = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "filtered",
            "source": self.source,
            "pushedAt": pushed,
            "lastScanned": now,
            "skillCount": skill_count,
        }
        self._write_meta()

    def write_tombstone(
        self, *, winner: str, winner_pushed: str, pushed: str, now: str
    ) -> None:
        """Tombstone a mirror repo (fingerprint dedup loser); scan data is
        removed and no fingerprint is kept, so an invalidated tombstone always
        re-adjudicates through a full tarball scan."""
        self._drop_scanned()
        self.meta = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "tombstoned",
            "source": self.source,
            "dedupedInto": winner,
            "winnerPushedAt": winner_pushed,
            "pushedAt": pushed,
            "lastScanned": now,
        }
        self._write_meta()

    def refresh(self, *, pushed: str, stars: int, now: str) -> None:
        """Update bookkeeping fields in place on a cache hit: the fingerprint
        and skill data stay, while pushedAt / stars / lastScanned go fresh so
        the next run compares against the latest push and the summary carries
        current stars."""
        self.meta["pushedAt"] = pushed
        self.meta["stars"] = stars
        self.meta["lastScanned"] = now
        self._write_meta()

    def summarize(self) -> JSON:
        """Read the persisted skills into the per-repo summary record."""
        skills = read_jsonl(self.repo_dir / SCANNED_FILE)
        return {
            "source": self.source,
            "pushedAt": self.meta.get("pushedAt"),
            "stars": self.meta.get("stars"),
            "skillCount": self.meta.get("skillCount", len(skills)),
            "skills": [s["path"] for s in skills],
        }

    def remove(self) -> None:
        """Drop the repo's whole cache dir (used when the repo is gone)."""
        if self.repo_dir.exists():
            shutil.rmtree(self.repo_dir)

    # -- internals -------------------------------------------------------

    def _drop_scanned(self) -> None:
        (self.repo_dir / SCANNED_FILE).unlink(missing_ok=True)

    def _write_meta(self) -> None:
        self.purge_foreign_files()
        write_json(self.repo_dir / META_FILE, self.meta)


# A ledger row is addressed by the skill it describes.
LedgerKey = tuple[str, str]


class RevLedger:
    """Cross-run memory of each published skill's content fingerprint.

    One row per ``(source, path)``: the skill's current ``rev`` and
    ``firstSeenAt``, the UTC timestamp of the run that first recorded that rev.
    The size is one row per published skill (never a history), so it does not
    grow with the number of upstream releases.

    :meth:`stamp` is what turns a rev into a date without any network access: a
    skill whose rev matches the ledger inherits the recorded date, a skill whose
    rev differs (or that was never seen) gets this run's timestamp. That makes
    the published date move *exactly* when the content changes — unlike the
    repository's ``pushedAt``, which advances on commits touching other skills.
    A rollback to previous content resets the date to the current run: the row
    only knows the rev it last recorded, and "first seen by this index" stays
    true either way.
    """

    def __init__(self, entries: dict[LedgerKey, Record]) -> None:
        self.entries = entries

    @classmethod
    def load(cls, path: Path) -> RevLedger:
        """Read a ledger file; missing, corrupt or malformed rows yield an
        empty ledger, which simply makes this run seed every date."""
        out: dict[LedgerKey, Record] = {}
        for rec in read_jsonl(path):
            key = (str(rec.get("source") or ""), str(rec.get("path") or ""))
            rev = str(rec.get("rev") or "")
            first = str(rec.get("firstSeenAt") or "")
            if key[0] and key[1] and rev and first:
                out[key] = {"source": key[0], "path": key[1], "rev": rev,
                            "firstSeenAt": first}
        return cls(out)

    def stamp(self, records: list[Record], now: str) -> tuple[int, int]:
        """Attach ``firstSeenAt`` to every rev-carrying record.

        Returns ``(refreshed, total)``: how many rows were new or changed, and
        the resulting ledger size. Records without a ``rev`` (a cache written
        before the rev format) are left untouched — an unknown content is not
        the same content, so they must neither inherit nor overwrite a date.

        Rows of repositories absent from this run are kept: a failed scan
        carries no information about the repo's skills, and dropping their rows
        would make the next successful run report every one of them as newly
        published.
        """
        fresh: dict[LedgerKey, Record] = {}
        refreshed = 0
        for rec in records:
            rev = str(rec.get("rev") or "")
            key = (str(rec.get("source") or ""), str(rec.get("path") or ""))
            if not rev or not key[0] or not key[1]:
                continue
            prev = self.entries.get(key)
            if prev is not None and prev["rev"] == rev:
                first = str(prev["firstSeenAt"])
            else:
                first = now
                refreshed += 1
            rec["firstSeenAt"] = first
            fresh[key] = {"source": key[0], "path": key[1], "rev": rev,
                          "firstSeenAt": first}

        scanned = {key[0] for key in fresh}
        kept = {
            key: row
            for key, row in self.entries.items()
            if key[0] not in scanned and key not in fresh
        }
        self.entries = {**fresh, **kept}
        return refreshed, len(self.entries)

    def save(self, path: Path) -> None:
        """Persist the ledger sorted by (source, path), so an accidental edit
        of the cached file produces a reviewable diff instead of churn."""
        rows = [self.entries[k] for k in sorted(self.entries)]
        write_jsonl(path, rows)
