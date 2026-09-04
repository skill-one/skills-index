# Skills Index — Developer Guide

English | [简体中文](DEVELOPMENT.zh-CN.md)

How to run it, how the data flows, where the rules live. For consuming the data, see the root [README.md](../README.md).

## Quick start

**Requirements**: Python >= 3.11 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                                # install dependencies
uv run skills-index update             # full pipeline (stateless)
uv run skills-index update --pages 1   # smoke: fetch a single page

uv run ruff check . && uv run mypy src/skills_index && uv run pytest
```

> Set `GH_PAT` (5000 req/h) or `GITHUB_TOKEN` (Actions built-in, 1000 req/h) via env or `.env`. `SKILL.md` content and commit history travel via git clone, off the REST quota.

## Data flow

`update` chains three steps — data passes in memory; no intermediate files:

| Step                                                                                                                                           | Output                                 |
| ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `fetch` — pull the skills.sh all-time ranking                                                                                                  | in memory (records)                    |
| `scan` — per repo: refresh `stars`; one bare partial clone locates every wanted `skillId` and extracts `path` / `description` / `lastCommitAt` | in memory (rows)                       |
| `index` — join both on `(source, skillId)`                                                                                                     | `data/index.jsonl` + `index-meta.json` |

Step contracts: [FETCH-SKILLS-SH.md](FETCH-SKILLS-SH.md) (fetch API) and [SCAN-REPO.md](SCAN-REPO.md) (matching, clone channel, `lastCommitAt`).

Rules:

- **Inclusion** — skills.sh alone decides: unregistered skills are invisible; skills no longer confirmed by their repo are dropped.
- **Stateless scan** — every run is a full scan: repo metadata + one bare partial git clone per repo, fetched fresh. There is no cache to maintain and no cross-run memory at all. A 404 repo drops all its skills; a transient failure skips the repo for this run. Never fabricate; remove only on definitive evidence.
- **Matching** — a `skillId` matches the first `<any prefix>/<skillId>/SKILL.md` in lexicographic order (see [SCAN-REPO.md](SCAN-REPO.md)).
- **Merge & ordering** — records keep the skills.sh ranking order (fetch order breaks ties); cross-repo duplicates (same `skillId` + same non-empty `description`) keep only the highest-`installs` copy.
- **`lastCommitAt` is factual data** — the skill directory's most recent commit time, read straight from git history each run (shallow clone first, unshallow fallback for exactness; see [SCAN-REPO.md](SCAN-REPO.md)). It is never stamped or inherited by the pipeline.

## Command reference

| Command  | Description                            | Key arguments         |
| -------- | -------------------------------------- | --------------------- |
| `fetch`  | Pull skills.sh data (no GitHub access) | `--pages N` (0 = all) |
| `update` | The whole pipeline in one step         | `--pages N`, `--tag`  |

## Data layout

One tree, published as flat GitHub Release assets (nothing is committed):

```
data/                              # what a run generates
  index.jsonl                      # final index (one entry per skill)
  index-meta.json                  # formatVersion / generatedAt / counts.total (+ tag, the release tag)
```

The pipeline keeps no state beyond `data/` and carries no memory between runs: `lastCommitAt` comes straight from git history each run. Records store `path`, never `url` — compose `https://github.com/<source>/tree/HEAD/<path>`.

## Code structure

```
src/skills_index/
├── cli.py        # argparse entry: orchestration + run report
├── config.py     # constants, paths, token discovery (leaf module)
├── http.py       # httpx wrapper: retries, auth, rate-limit backoff
├── io_utils.py   # JSON / JSONL helpers
├── github.py     # repo metadata, bare partial clone: matching, description, lastCommitAt
├── fetch.py      # skills.sh -> records in memory
├── scan.py       # per-repo full scan: repo metadata + one clone, matching, stars
├── index.py      # join + dedup -> index.jsonl
```

`config` is the leaf; `http` / `io_utils` depend only on `config`; `fetch` / `github` depend on `http`; `scan` on `github` / `http`; `index` on `io_utils`; `cli` only orchestrates.

## CI

Daily at 00:00 UTC ([.github/workflows/daily.yml](../.github/workflows/daily.yml)), after a lint + test gate (ruff / mypy / pytest, Python 3.11 + 3.13):

- **`main`** — full pipeline. Fully stateless: `lastCommitAt` comes straight from git history each run; nothing is carried between runs.
- **`test`** — 1-page smoke run, published as a prerelease (never occupies `releases/latest`); starts fresh.
- **Publish** — `index.jsonl` + `index-meta.json` are force-pushed to the orphan `dist` branch in a single commit, and the release tag is created on that commit — one tag addresses both the Release download and the CDN URL. 2 assets; the run report (captured from `update` stdout) is the Release body. The last 10 `data-` releases are retained.
