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
| 3. index | `skills-index index` | Merge the previous two steps into the final index, and stamp each skill's version date through the cross-run rev ledger | `data/index/index.jsonl` + `index-meta.json` + `cache/rev-ledger.jsonl` |
| 4. update | `skills-index update` | Steps 1–3 in one command (single entry point for local testing and CI) | all of the above |

- **Incremental mechanism**: `scan` first skips unchanged repos by `pushed_at`; for repos with new pushes, one Trees API call compares the **git tree sha of every public skill directory** against the cached baseline and skips the tarball download when nothing changed (a tree sha covers the whole directory — content, file names and modes — so a change confined to a bundled script cannot hide, which the previous SKILL.md-sha domain got wrong). After any successful scan, the baseline is recorded for the next run (one request per scanned repo, reused from the pre-check when it already ran). Contents are then parsed locally from the full tarball (traffic goes through codeload and does not consume REST quota). Mirror repos whose per-skill `rev` maps are identical are **tombstoned** by the dedup logic and skipped directly in later runs, until either side receives a new push and both are re-adjudicated.
- **Version fields**: `scan` derives each skill's `rev` (sha256 over the directory's sorted `(path, git mode, blob sha)` triples, published as the first 16 hex chars), and `index` publishes it together with `firstSeenAt` — the timestamp of the run that first recorded that rev, resolved through `cache/rev-ledger.jsonl`. The ledger holds one row per published skill (never a history), so its size tracks the index; rows of repositories missing from a run are kept, because a failed scan carries no information and must not reset a whole repo's dates. The published value deliberately carries **no algorithm tag** (consumers treat `rev` as an opaque equality judge and would never read one), which makes the digest a frozen contract: changing the domain, the normalization or the hash rewrites every published value and is a breaking change — bump `SCHEMA_VERSION` (one full rebuild) and `INDEX_FORMAT_VERSION` in the same step, and accept the one-time `firstSeenAt` reset. `tests/test_github_tarball.py` pins the digest of a fixed fixture, so an unintended change fails CI.
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
| `skills-index pull` | **Standalone helper** (not part of the pipeline): download the latest published `data-` release's `data.tar.gz` snapshot into `pulled/<tag>/` and print an inspection report — never touches `data/` or `cache/` | `--repo owner/repo` or GitHub URL (default `skill-one/skills-index`); `--dest` output root (default `pulled/`) |

## Inspecting a published snapshot locally (`pull`)

`pull` is a **fully standalone helper**, orthogonal to the `fetch → scan → index → update` pipeline: it never reads or writes `data/` or `cache/`, never feeds the incremental chain, and adds no third-party dependency (it reuses the `http` / `config` / `io_utils` layers only). It exists so a person can grab exactly what CI published and eyeball it locally before pointing a tool at it.

What it does:

1. Resolves the repo's **latest `data-` GitHub Release**. CI marks smoke (`alpha-`) releases as prerelease, so `releases/latest` always means the newest full snapshot.
2. Downloads the bundled `data.tar.gz` asset via its public `browser_download_url` (served anonymously by an object host — no auth needed for this public repo; the download is not billed to the REST quota).
3. **Safely** extracts it into `pulled/<tag>/data/`, mirroring the published layout. Any pre-existing same-tag directory is wiped first, so a re-pull is always a faithful, complete copy. Path-traversal (`../`, absolute) members and non-regular entries (symlinks / devices) are rejected before any byte is written. The archive is removed after extraction.
4. Prints an inspection report: `index-meta.json` (`formatVersion` / `generatedAt` / `counts.total`), the **actual** `index.jsonl` line count (with a `⚠` when it disagrees with `counts.total`), and every file under `data/` with its size.

The `pulled/` tree is git-ignored.

```bash
uv run skills-index pull                    # latest snapshot of this repo -> pulled/<tag>/
uv run skills-index pull --repo owner/repo  # any GitHub repo's latest data- release
```

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
  rev-ledger.jsonl                     # cross-run version ledger: one row per published skill (rev + firstSeenAt)
  by-source/<owner>__<repo>/           # per-repo state; '__' is a lossless stand-in for '/'
    scanned.jsonl / meta.json
```

> Release asset names are flat basenames, so the directory layering above does not affect the `releases/download/<tag>/<filename>` download URLs; inside `data.tar.gz` / `cache.tar.gz`, the contents follow these two tree layouts respectively.

- `cache/by-source/<owner>__<repo>/meta.json` is a single shape tagged by `status` (see `src/skills_index/cache.py`):
  - `ok` — a normal cache: `branch` / `pushedAt` / `stars` / `skillCount` / `skillTreeShas` (the `{path: git tree sha}` baseline of the repo's public skill directories, compared against the Trees API on the next run; it may be empty when that request failed, which only costs one redundant tarball download);
  - `filtered` — excluded for exceeding the skillCount cap: keeps `pushedAt` + `skillCount`; later runs skip it without a tarball until the repo receives a new push and is re-adjudicated;
  - `tombstoned` — eliminated by mirror dedup: `dedupedInto` + `winnerPushedAt` + `pushedAt`, until either side receives a new push.
  - `scanned.jsonl` exists only in the `ok` state and carries one `{path, rev, description}` record per skill; every rewrite cleans leftover files outside the contract out of the directory.
- `cache/rev-ledger.jsonl` is written by the **index** step, not by `scan`, and lives at the cache root on purpose: `clean_workspace` wipes only `cache/by-source/` and `RepoCache` purges only files inside a repo directory, so the ledger survives both while still being packaged into `cache.tar.gz`.

> Artifacts store only `path`, never `url`; compose the full GitHub directory link from `source` + `path` as `https://github.com/<source>/tree/HEAD/<path>`.

## Code structure

```
src/skills_index/
├── __init__.py   # package version
├── cli.py        # argparse entry: fetch / scan / index / update / pull orchestration + run report
├── config.py     # constants, paths (data/ and cache/ registries), filter rules, token discovery, source<->dir mapping (leaf module)
├── http.py       # thin httpx wrapper: client, retries, GitHub auth, rate-limit backoff, binary download
├── io_utils.py   # JSON / JSONL read-write helpers
├── cache.py      # cross-run state: RepoCache (per-repo fingerprints) + RevLedger (rev -> firstSeenAt)
├── github.py     # GitHub interface: repo metadata, Trees precheck, codeload tarball parsing, skill_rev
├── fetch.py      # pull skills.sh -> filter -> distribute by source; includes prune_stale_repos
├── scan.py       # incremental scan (pushed_at / Trees precheck / fingerprints) + aggregation + mirror dedup
├── index.py      # merge fetched + scanned, stamp rev/firstSeenAt, generate data/index.jsonl
└── pull.py       # standalone: download + inspect the latest published data- release snapshot (no pipeline coupling)
```

Dependencies are acyclic: `config` is the leaf, depended on by all other modules; `http` depends only on `config`; `cache` depends on `config` / `io_utils`; `github` / `fetch` depend on `http` / `config` / `io_utils`; `scan` and `index` additionally depend on `cache` (fingerprints / rev ledger); `pull` reuses only `http` / `config` / `io_utils`, adds no new dependency, and is imported by nothing in the pipeline (it stands alone); `cli` only orchestrates and implements no business logic.

## CI

Data is generated and published as a Release automatically every day at 00:00 UTC by GitHub Actions (`.github/workflows/daily.yml`): every run (including smoke) first passes a **lint + test gate** (ruff / mypy / pytest + coverage, on a Python 3.11 and 3.13 matrix); once through, `main` runs a full fetch and the `test` branch pulls 1 page as a smoke run (smoke releases are marked prerelease and never occupy `releases/latest`); before a full run, the incremental state (`cache/by-source/` fingerprints and the root-level rev ledger) is restored from the previous `data-` release (downloading its `cache.tar.gz`). On `main`, `index.jsonl` and `index-meta.json` are force-pushed to the orphan `dist` branch **before** packaging and publishing: the push happens in two commits so CI can backfill `distCommit` (the first commit's sha, whose `index.jsonl` bytes are identical) into the metadata — and because this runs before packaging, the backfilled meta also flows into `data.tar.gz` and the Release asset, so the Release and `dist` copies of `index.jsonl` / `index-meta.json` are byte-identical and consumers get an immutable, commit-addressed URL. Smoke (`test` branch) runs skip the dist push, so their meta simply omits `distCommit`. `data/` and `cache/` are packaged separately (`data.tar.gz`, `cache.tar.gz`). The Release carries a **slim set of 5 assets** — `index.jsonl`, `index-meta.json`, `data.tar.gz`, `cache.tar.gz`, `run-summary.md` — while the intermediate jsonl (`fetched-skills.jsonl`, `scanned-repos.jsonl`) ship only inside `data.tar.gz`, not as separate assets; the most recent 10 Releases are retained.
