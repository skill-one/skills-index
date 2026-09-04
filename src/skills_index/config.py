"""Project-wide constants, paths, and shared types."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

# JSON payloads are dynamically shaped; we do not over-constrain them.
JSON = Any

# The pipeline is fully stateless: stages pass data in memory — there are no
# intermediate files — and every run recomputes everything from the two
# upstream sources. `lastCommitAt` comes straight from git history each run,
# so there is no cross-run memory at all.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA_DIR = ROOT / "data"

# The product
INDEX_JSONL = DATA_DIR / "index.jsonl"
INDEX_META_JSON = DATA_DIR / "index-meta.json"

#--- External endpoints ---
SKILLS_API = "https://skills.sh/api/skills/all-time"
GITHUB_API = "https://api.github.com"

# Version of the published index format (index.jsonl + index-meta.json).
# v7: rebuilt around skills.sh as the sole registry — only registered skills
#   are indexed (no scan-only entries) and every record carries all fields.
#   index-meta.json swapped `distCommit` for `tag` (the release tag, which
#   names the dist-branch commit carrying this snapshot and addresses both
#   the Release-download and CDN URLs).
# v8: dropped `name` / `isOfficial` from published records (name duplicates
#   skillId; isOfficial flags nothing actionable).
# v9: scan switched from codeload tarball to one bare partial git clone per
#   repo; `rev` (content fingerprint) removed; `firstSeenAt` (run stamp,
#   inherited from the previous published index) replaced by `lastCommitAt`
#   — the skill directory's true most-recent commit time, straight from
#   `git log` each run. The pipeline is fully stateless again.
INDEX_FORMAT_VERSION = 9

# A GitHub source is `owner/repo` (contains a slash, is not a full URL).
GITHUB_SOURCE = re.compile(r"^[^/\s]+/[^/\s]+$")


def is_github_source(source: str) -> bool:
    return bool(GITHUB_SOURCE.match(source.strip()))


def load_github_token() -> str:
    """Return a GitHub token: prefer `GH_PAT`, then `GITHUB_TOKEN`, then `.env`.

    `GH_PAT` is a personal access token (5000 req/h) recommended for CI; the
    Actions-provided `GITHUB_TOKEN` is capped at 1000 req/h per repository.
    """
    for var in ("GH_PAT", "GITHUB_TOKEN"):
        token = os.environ.get(var, "").strip()
        if token:
            return token
    env_file = ROOT / ".env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            for var in ("GH_PAT=", "GITHUB_TOKEN="):
                if line.startswith(var):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


# A minimal JSON-able record alias used by IO helpers.
Record = dict[str, Any]
