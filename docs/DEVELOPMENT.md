# Skills Index — Developer Guide

English | [简体中文](DEVELOPMENT.zh-CN.md)

For developers: how to get it running, what the data flow looks like, how to use the commands, and where the rules live. If you only care about consuming the data, see the root [README.md](../README.md).

---

## Quick start

**Requirements**: Python >= 3.11 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                      # install dependencies
uv run skills-index update   # run the full pipeline in one command (incremental)

# tests and checks
uv run ruff check .
uv run mypy src/skills_index
uv run pytest
```

> Optional: set `GITHUB_TOKEN` (environment variable or `.env`) to lift the unauthenticated 60 requests/hour limit; `GH_PAT` (personal access token, 5000 requests/hour) is recommended and takes precedence over the Actions built-in token (1000 requests/hour). `SKILL.md` content travels via the codeload tarball and does not count against the quota.

## Data-flow overview

A three-step pipeline (`update` chains them into one command):

| Step | Command | What it does | Output |
| --- | --- | --- | --- |
| 1. fetch | `skills-index fetch` | Pull raw data from the skills.sh all-time leaderboard | `data/skills-sh/fetched-skills.jsonl` + `cache/by-source/*/` (directories created per source only) |
| 2. scan | `skills-index scan` | Incrementally scan GitHub repos for `SKILL.md` | `data/github/scanned-repos.jsonl` + `cache/by-source/*/scanned.jsonl` + `meta.json` |
| 3. index | `skills-index index` | Merge the previous two steps into the final index | `data/index/index.jsonl` + `index-meta.json` |
| 4. update | `skills-index update` | Steps 1–3 in one command (single entry point for local testing and CI) | all of the above |

- **Incremental mechanism**: `scan` first skips unchanged repos by `pushed_at`; for repos with new pushes, one Trees API call compares the blob shas of all `SKILL.md` files and skips the tarball download when nothing changed; after passing the tree precheck, contents are parsed locally from the full tarball (traffic goes through codeload and does not consume REST quota). Mirror repos whose content fingerprints are identical are **tombstoned** by the dedup logic and skipped directly in later runs, until either side receives a new push and both are re-adjudicated.
- **Failure safety**: in incremental `update` mode, when fetch has failed pages, stale-repo pruning (prune) is **skipped**, so repos that merely happen to fall on a failed page are not misjudged as vanished.
- **Filtering**: the pipeline applies multi-layer filtering (repo-level / skill-level / merge-level); the full rules are documented in [FILTERING.md](FILTERING.md).
- **Final index**: for the fields and consumption of `index.jsonl`, see [README.md](../README.md).

## Command reference

| Command | Description | Key arguments |
| --- | --- | --- |
| `skills-index fetch` | Pull skills.sh data | `--pages N` (0 = all) |
| `skills-index scan` | Incremental repo scan | `--force` full rescan; `--max-skill-count N` filter cap (default 500, 0 disables) |
| `skills-index index` | Merge into the final index | none |
| `skills-index update` | Run the whole pipeline in one step | `--pages N`; `--force` full rebuild; `--max-skill-count N` |

## Data layout

Published data and the incremental cache are two independent trees:

```
data/                                  # published data (data.tar.gz, for data consumers)
  run-summary.md                       # run report
  skills-sh/                           # step 1: skills.sh content
    fetched-skills.jsonl               #   summarized raw skills.sh data
  github/                              # step 2: repo scan content
    scanned-repos.jsonl                #   per-repo summary (original scan order)
  index/                               # step 3: final artifacts
    index.jsonl                        #   final index (one entry per skill)
    index-meta.json                    #   self-describing metadata (formatVersion / generatedAt / counts.total; CI backfills distCommit)

cache/                                 # incremental cache (cache.tar.gz, for the next CI run to restore)
  by-source/<owner>__<repo>/           # per-repo state; '__' is a lossless stand-in for '/'
    scanned.jsonl / meta.json
```

> Release asset names are flat basenames, so the directory layering above does not affect the `releases/download/<tag>/<filename>` download URLs; inside `data.tar.gz` / `cache.tar.gz`, the contents follow these two tree layouts respectively.

- The two sorted views `scanned-repos-by-stars.jsonl` / `scanned-repos-by-skillcount.jsonl` are generated **by CI at publish time** from `scanned-repos.jsonl` (see daily.yml); the core pipeline produces only the single scan-order copy.
- `cache/by-source/<owner>__<repo>/meta.json` is a single shape tagged by `status` (see `src/skills_index/cache.py`):
  - `ok` — a normal cache: `branch` / `pushedAt` / `stars` / `skillCount` / `skillShas` (the `{path: blob sha}` fingerprint of the repo's public skills, serving both as the Trees-precheck comparison domain and as the mirror-dedup fingerprint);
  - `filtered` — excluded for exceeding the skillCount cap: keeps `pushedAt` + `skillCount`; later runs skip it without a tarball until the repo receives a new push and is re-adjudicated;
  - `tombstoned` — eliminated by mirror dedup: `dedupedInto` + `winnerPushedAt` + `pushedAt`, until either side receives a new push.
  - `scanned.jsonl` exists only in the `ok` state; every rewrite cleans leftover files outside the contract out of the directory.

> Artifacts store only `path`, never `url`; compose the full GitHub directory link from `source` + `path` as `https://github.com/<source>/tree/HEAD/<path>`.

## Code structure

```
src/skills_index/
├── __init__.py   # package version
├── cli.py        # argparse entry: fetch / scan / index / update orchestration + run report
├── config.py     # constants, paths (data/ and cache/ registries), filter rules, token discovery, source<->dir mapping (leaf module)
├── http.py       # thin httpx wrapper: client, retries, GitHub auth, rate-limit backoff
├── io_utils.py   # JSON / JSONL read-write helpers
├── cache.py      # per-repo incremental cache (cache/by-source): RepoCache reads, writes for the three statuses, leftover cleanup
├── github.py     # GitHub interface: repo metadata, Trees precheck, codeload tarball parsing
├── fetch.py      # pull skills.sh -> filter -> distribute by source; includes prune_stale_repos
├── scan.py       # incremental scan (pushed_at / Trees precheck / fingerprints) + aggregation + mirror dedup
└── index.py      # merge fetched + scanned, generate data/index.jsonl
```

Dependencies are acyclic: `config` is the leaf, depended on by all other modules; `http` depends only on `config`; `cache` depends on `config` / `io_utils`; `github` / `fetch` depend on `http` / `config` / `io_utils`; `scan` additionally depends on `cache`; `cli` only orchestrates and implements no business logic.

## CI

Data is generated and published as a Release automatically every day at 00:00 UTC by GitHub Actions (`.github/workflows/daily.yml`): every run (including smoke) first passes a **lint + test gate** (ruff / mypy / pytest + coverage, on a Python 3.11 and 3.13 matrix); once through, `main` runs a full fetch and the `test` branch pulls 1 page as a smoke run (smoke releases are marked prerelease and never occupy `releases/latest`); before a full run, the `cache/by-source/` incremental fingerprints are restored from the previous `data-` release (downloading its `cache.tar.gz`); at publish time `data/` (`data.tar.gz`, pure published data) and `cache/` (`cache.tar.gz`, incremental cache) are packaged separately; the two sorted views of `scanned-repos` are generated by CI at the publish stage; `index.jsonl` and `index-meta.json` are additionally force-pushed to the `dist` branch for access via jsDelivr and other CDNs — the push happens in two commits so CI can backfill `distCommit` (the first commit's sha, whose `index.jsonl` bytes are identical) into the metadata, giving consumers an immutable, commit-addressed URL for the body; the most recent 10 Releases are retained.
