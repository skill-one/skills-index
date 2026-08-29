# Filtering Rules

English | [简体中文](FILTERING.zh-CN.md)

All filtering rules of the pipeline, in three layers: **repo-level** (drop the whole repository), **in-repo skill-level** (drop a single SKILL.md), and **index-merge-level** (exclude/dedupe during the merge). Implementation locations are annotated as `file::function`, and the configuration lives in `src/skills_index/config.py`.

## Overview

```
skills.sh API ──▶ [fetch] ──▶ [scan] ──▶ [index] ──▶ index.jsonl
                   F1 F2      S1 S2 S3    I1 I2 I3
                             F3 F4 F5
```

| ID  | Filter                          | Location                              | Rule in one line                                                                                              |
| --- | ------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| F1  | Leaderboard entry gate          | `fetch`                               | Must appear on the skills.sh all-time leaderboard                                                             |
| F2  | Non-GitHub source               | `fetch::filter_github`                | Drop unless source matches `owner/repo`                                                                       |
| F3  | Repository gone                 | `scan::_scan_one_repo`                | GitHub 404 → drop all cached data for the repo                                                                |
| F4  | High skill-count repos          | `scan::_scan_one_repo`                | skillCount > 500 (aggregator repos) → drop and tombstone the cache                                            |
| F5  | Content-fingerprint mirror dedup | `scan::_dedup_repos`                 | Identical skill-tree blob shas → keep only the highest-starred one                                            |
| S1  | Filename convention             | `github::_parse_tarball`              | Collect only `…/SKILL.md`; ignore every other file                                                            |
| S2  | Internal path filter            | `config::is_internal_skill_path`      | SKILL.md sits in an internal directory → drop                                                                 |
| S3  | Non-public frontmatter          | `github::is_nonpublic_frontmatter`    | Author declares hidden/private/… → drop                                                                       |
| I1  | Orphan skills                   | `index::run_index`                    | In repo, not on leaderboard → keep, metadata blanked (`installs: 0` / `weeklyInstalls: []`)                   |
| I2  | Leaderboard mismatch            | `index::run_index`                    | On leaderboard, absent from repo scan → drop                                                                  |
| I3  | Cross-repo skill dedup          | `index::_dedup_skills`                | skillId + description both match → keep the one with the most installs                                        |

The final index is **anchored on the repo scan**: inclusion is decided by the scan results (which have already passed F1–F5 / S1–S3); the skills.sh leaderboard only attaches metadata such as `installs`; skills absent from the leaderboard enter the index with empty metadata (I1); leaderboard entries missing from the repo scan are removed by I2 — every skill in the index is backed by a repository path that currently exists.

---

## 1. Repo-level filters

### F1 Leaderboard entry gate (implicit)

`fetch` pulls only from the skills.sh `all-time` leaderboard; repos absent from the leaderboard never enter the pipeline. No explicit knob.

### F2 Non-GitHub sources

- **Rule**: `fetch::filter_github` validates with `config::is_github_source` (regex `^[^/\s]+/[^/\s]+$`) and drops any entry not in `owner/repo` form.
- **Also**: only `KEEP_FIELDS` (`source` / `skillId` / `installs` / `weeklyInstalls`) are kept; no URL fields are ever persisted.
- **Counter**: `dropped_non_github`.

### F3 Repository gone (404)

- **Rule**: GitHub returns 404 (deleted/renamed/made private) → delete the entire `cache/by-source/<owner>__<repo>/` cache; the repo and its skills disappear from the index.
- **Detection**: `github::_is_missing_repo` walks the exception chain for 404 and distinguishes it from network errors/5xx/rate limiting — those only skip the current run and never delete data.
- **Counter**: `repos_gone`.

### F4 High skill-count repos (aggregator filter)

- **Rule**: `skillCount` > `MAX_SKILL_COUNT` (default **500**) → drop the whole repo and **tombstone** its cache (delete `scanned.jsonl`, keep `pushedAt` + `skillCount` in `meta.json`): later runs skip it without a tarball while the repo has no new push, and re-adjudicate fully once it does (it may have come back under the cap).
- **Tunable**: override with `--max-skill-count N`; `0` disables.
- **Covers the incremental branch**: `ok`-cached repos whose `pushed_at` hasn't changed are re-checked against the cached `meta.json` `skillCount`, so tightening the rule still filters them (converted to tombstones).
- **Counter**: `repos_filtered` / `repos_filtered_high_skill`.

### F5 Content-fingerprint mirror dedup

- **Rule**: `scan::_dedup_repos` serializes each repo's `{path: blob_sha}` skill tree into a fingerprint; identical fingerprints mean an un-forked mirror, and within a group only the highest-starred repo survives while the rest are **tombstoned** (`status: tombstoned`): `scanned.jsonl` is deleted and `meta.json` rewritten with a `dedupedInto` marker (including both sides' `pushedAt` snapshots); its skills no longer enter the index; later runs skip both repos outright while neither has a new push (zero network requests); if either side gets a push or the winner disappears, both are rescanned and re-adjudicated.
- **Edge**: repos with `skillCount == 0` have no fingerprint and do not participate in dedup.
- **Counter**: `repos_deduped` (only losers newly deduped this round; tombstone skips count toward `repos_skipped`, logged with the `[dedup-skip]` marker).

---

## 2. In-repo skill-level filters

These run in `github::_parse_tarball`; the order is the priority S1 → S2 → S3, and any hit drops the file (S2/S3 share the `skills_filtered_nonpublic` counter).

### S1 Filename convention

Collect only files whose path ends with `/SKILL.md` (a skill must live inside its own directory, e.g. `skills/foo/SKILL.md`). A lone `SKILL.md` at the repository root is deliberately not collected.

### S2 Internal path filter (`config::is_internal_skill_path`)

Checks the directory containing the SKILL.md; **whole-segment exact comparison, case-insensitive** (`testing` ≠ `test`):

- **Status words (any path segment, including the skill directory name)**: `deprecated` / `hidden` / `private` / `internal` / `obsolete` (`SKILL_EXCLUDE_ANY_DIRS`).
- **Structural words (intermediate segments only, never the last segment)**: `test` / `tests` / `__tests__` / `spec` / `e2e` / `example` / `examples` / `sample` / `samples` / `demo` / `demos` / `fixture` / `fixtures` / `mock` / `mocks` / `stub` / `stubs` / `template` / `templates` / `scaffold` / `boilerplate` / `doc` / `docs` / `dist` / `build` / `out` / `node_modules` / `vendor` / `third_party` (`SKILL_EXCLUDE_DIRS`; the last segment is the skill directory name and is therefore exempt).
- **Hidden directories (starting with `.`)**: excluded by default; standard public skill locations such as `.claude/skills/…`, `.agents/skills/…`, `.skills/…` are exempt; `.github` is never exempt.

### S3 Non-public frontmatter (`github::is_nonpublic_frontmatter`)

Parses the YAML frontmatter of SKILL.md; drops in the following cases:

- `public: false`; or
- any of these fields is truthy (`true` / `yes` / `1`, etc.): `deprecated` / `hidden` / `private` / `internal` / `obsolete` (`HIDDEN_FRONTMATTER_MARKERS`).

Counter-examples (all kept): `hidden: false`, `public: true`, no related fields, missing frontmatter, or frontmatter that fails to parse.

---

## 3. Index-merge-level filters (`index::run_index`)

The two upstream artifacts are joined on `(source, skillId)` (`skillId` is derived from the last path segment of `path`), anchored on the scan results.

### I1 Orphan skills (in repo, not on the leaderboard) → kept

`(source, directory name)` missing from the skills.sh leaderboard → still written to index.jsonl, but the `installs` / `weeklyInstalls` fields **do not appear**; appended at the end (entries with leaderboard data come first, in rank order). Counter: `scan_only`.

### I2 Leaderboard mismatch (on the leaderboard, absent from the repo) → dropped

Listed on the leaderboard but not found by the repo scan (deleted/renamed/moved) → dropped, guaranteeing every skill is backed by a real repository path. Counter: `not_in_repo`.

### I3 Cross-repo skill dedup (`index::_dedup_skills`)

Identical `skillId` and `description` (non-empty) → treated as mirrors/copies of the same skill; only the entry with the most `installs` survives within the group (ties keep the higher-ranked one). An empty `description` never participates in dedup. Counter: `deduped_skills`.

---

## 4. Configuration (`src/skills_index/config.py`)

| Constant                       | Default              | Purpose                                                                     |
| ------------------------------ | -------------------- | --------------------------------------------------------------------------- |
| `MAX_SKILL_COUNT`              | `500`                | F4 cap; overridden by `--max-skill-count N`, `0` disables                    |
| `SKILL_EXCLUDE_DIRS`           | structural-word set  | S2 excluded words for intermediate segments                                 |
| `SKILL_EXCLUDE_ANY_DIRS`       | status-word set      | S2 excluded words for any segment (including the skill name)                 |
| `HIDDEN_FRONTMATTER_MARKERS`   | status-word tuple    | S3 non-public frontmatter markers                                            |
| `SCHEMA_VERSION`               | `5`                  | Bump when filter rules change → forces a one-time full rescan of existing caches |

> Bump `SCHEMA_VERSION` after changing any filtering rule: in incremental mode, old caches were produced under the old rules, and only a version change forces a rebuild.

## 5. Observability: run-summary counter mapping

How the fields in `data/run-summary.md` after each `update` map to the rules:

| run-summary field                              | Corresponding filter                                        |
| ---------------------------------------------- | ----------------------------------------------------------- |
| `dropped_non_github`                           | F2                                                          |
| `repos_gone`                                   | F3                                                          |
| `repos_filtered` / `repos_filtered_high_skill` | F4                                                          |
| `repos_deduped`                                | F5 (shown only on hits)                                     |
| `skills_filtered_nonpublic`                    | S2 + S3 (the volume actually parsed from tarballs this run) |
| `scan_only`                                    | I1 (a retention count, not a filter count)                  |
| `not_in_repo`                                  | I2                                                          |
| `deduped_skills`                               | I3 (shown only on hits)                                     |

The scan summary line's breakdown check `skipped + updated + failed + gone + filtered == repos_total` (✓/⚠) verifies that repo-level filtering is fully accounted for; losers newly deduped this round are counted into `skipped`/`updated` first and then removed from the summary, with `repos_deduped` as their post-hoc breakdown; skips of historical tombstones count directly into `skipped` — none of these add extra terms to that identity.
