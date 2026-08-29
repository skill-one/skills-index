"""Per-repo incremental cache under ``cache/by-source/<owner>__<repo>/``.

One directory per repository holds the only cross-run state the scan step
needs (see config.CACHE_DIR):

- ``meta.json``     state fingerprint. A single tagged shape: every record
                    carries ``schemaVersion`` / ``status`` / ``source`` /
                    ``lastScanned``, plus the fields of its status:
  - ``ok``          normal cache: ``branch`` / ``pushedAt`` / ``stars`` /
                    ``skillCount`` / ``skillShas`` ({path: blob sha} of the
                    repo's public SKILL.md files — the same domain the Trees
                    API pre-check compares against, and the fingerprint the
                    mirror dedup groups on).
  - ``filtered``    repo excluded by the skillCount cap: ``pushedAt`` /
                    ``skillCount`` are kept so later runs skip it without a
                    tarball download until a new push triggers a full
                    re-adjudication (a push may bring it back under the cap).
  - ``tombstoned``  dedup loser (byte-identical mirror of another repo):
                    ``dedupedInto`` / ``winnerPushedAt`` / ``pushedAt``,
                    skipped with zero network I/O until either side pushes.
- ``scanned.jsonl`` the repo's skill records; exists only for ``ok``.

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
    def skill_shas(self) -> dict[str, str]:
        """{SKILL.md path: blob sha} of the cached public skills."""
        shas = self.meta.get("skillShas")
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
        skill_shas: dict[str, str],
    ) -> None:
        """Persist a full scan result: scanned.jsonl + ``ok`` meta."""
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
            "skillShas": skill_shas,
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
