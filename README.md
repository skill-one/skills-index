# Skills Index

English | [简体中文](README.zh-CN.md)

An index of [skills.sh](https://skills.sh) skills: see every skill's `source` / `skillId` / `installs` / `weeklyInstalls` (from skills.sh) in one place, together with the repository's `stars`, the repository-relative `path`, and the skill `description` obtained by scanning GitHub repositories.

**Consumers never need to clone anything** — just pull a published index snapshot. How the data is produced is documented in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (developer-facing).

---

## What the data is

The final index `index.jsonl` is **flattened per skill**, one skill per line:

```json
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "stars": 29929,
  "installs": 3005209,
  "weeklyInstalls": [
    113781, 109199, 109085, 115475, 107969, 101120, 96861, 93130
  ],
  "path": "skills/find-skills",
  "description": "Discover and install agent skills"
}
```

| Field             | Description                                                            |
| ----------------- | ---------------------------------------------------------------------- |
| `source`          | GitHub repository in `owner/repo` form                                 |
| `skillId`         | Skill identifier (the skill's directory name)                          |
| `stars`           | Star count of the skill's repository (repo-level: every skill from the same repo carries the same value) |
| `installs`        | Total installs (from skills.sh)                                        |
| `weeklyInstalls`  | Weekly installs over the last 8 weeks (from skills.sh, chronological)  |
| `path`            | Skill's path relative to the repository root (e.g. `skills/find-skills`) |
| `description`     | Skill description (from the `SKILL.md` frontmatter)                    |

> The index **does not store a ready-to-use `url`**; compose the full GitHub directory URL from `source` + `path`:
> `https://github.com/<source>/tree/HEAD/<path>` (`HEAD` always resolves to the default branch, so it is unaffected by branch changes).
>
> Skills not tracked by skills.sh are still included in the index; they simply lack the `installs` / `weeklyInstalls` fields. Every record carries `stars`.

## Published artifacts

Each Release contains:

| File                                | Description                                                                                                              |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `index.jsonl`                       | The merged final index (flattened per **skill**; **recommended for direct consumption**)                                  |
| `index-meta.json`                   | Self-describing index metadata: `formatVersion` (bumped when fields change), `generatedAt` (generation time), `counts.total`, and `distCommit` (the `dist`-branch commit carrying this snapshot's `index.jsonl`, backfilled by CI — see [CDN access](#access-via-cdn-directly-usable-in-browsers)) |
| `data.tar.gz`                       | Full data snapshot (internally organized into the `skills-sh/` / `github/` / `index/` directories; published data only, no pipeline-internal state) |
| `cache.tar.gz`                      | Incremental scan cache (per-repo fingerprints under `cache/by-source/`), used only by the next CI run to restore incremental state; data consumers don't need to download it |
| `fetched-skills.jsonl`              | Summarized raw skills.sh data (intermediate artifact)                                                                     |
| `scanned-repos.jsonl`               | Per-repository scan results in **original scan order** (the order fetch pulled them in, unsorted)                         |
| `scanned-repos-by-stars.jsonl`      | Scan results sorted by **star count, descending**                                                                         |
| `scanned-repos-by-skillcount.jsonl` | Scan results sorted by **installed skills count (`skillCount`), descending**                                              |
| `run-summary.md`                    | Run report (per-stage counts and timings)                                                                                 |

## How to fetch and use it

Each Release is tagged `data-YYYYMMDDThhmmssZ` (UTC timestamp, e.g. `data-20260820T143015Z`). **Production should pin a fixed tag** rather than `latest`: a fixed tag points to a definite snapshot, is reproducible, and `releases/download/<tag>/<file>` is a stable direct link that plays well with caches; `latest` is a 302 redirect whose target changes at any time, giving poor cache hit rates.

```bash
# Production: pin a fixed tag (recommended)
TAG=data-20260820T143015Z
curl -L -o index.jsonl \
  https://github.com/skill-one/skills-index/releases/download/$TAG/index.jsonl

# Pull the full data snapshot (also with a fixed tag, so it comes from the
# same snapshot as index.jsonl)
curl -L -o data.tar.gz \
  https://github.com/skill-one/skills-index/releases/download/$TAG/data.tar.gz
```

> To resolve the latest snapshot's tag without hardcoding a timestamp, use `gh` to look up the newest `data-` release; to lock onto a specific day, just hardcode that day's tag:
>
> ```bash
> TAG=$(gh release list --limit 100 --json tagName,createdAt \
>   --jq 'sort_by(.createdAt) | reverse | .[].tagName' \
>   | grep '^data-' | head -n 1)
> ```

```bash
# Local debugging / when you want the newest: use latest (note the 302
# redirect and poor cacheability). latest always points to a full data-
# snapshot: smoke alpha- releases are all marked prerelease and never
# become latest.
curl -L -o index.jsonl \
  https://github.com/skill-one/skills-index/releases/latest/download/index.jsonl
```

### Access via CDN (directly usable in browsers)

`index.jsonl` is also published to the orphan branch `dist`, so it can be consumed directly in browsers through CORS-enabled CDNs such as jsDelivr:

```
https://cdn.jsdelivr.net/gh/skill-one/skills-index@dist/index.jsonl
https://cdn.jsdelivr.net/gh/skill-one/skills-index@dist/index-meta.json
```

> The `dist` branch is force-pushed on every full release and always points to the latest snapshot from `main`; only `main` publishes — smoke tests never overwrite it.

For production caching, prefer **commit-addressed URLs**: read `distCommit` from `index-meta.json` and fetch the body through that immutable ref. `@dist` is a mutable ref (its CDN cache must be busted manually), while a commit sha never changes, so the URL is cacheable forever and can never serve a stale snapshot:

```
https://cdn.jsdelivr.net/gh/skill-one/skills-index@<distCommit>/index.jsonl
```

You can also download any historical snapshot on demand from the Releases page. The most recent 10 Releases are retained; older ones are cleaned up automatically.

---

## License

- **Code** (`src/`, `tests/`, CI workflows, etc.) is released under [MIT](LICENSE).
- **Published data artifacts** (`index.jsonl` / `index-meta.json` / `fetched-skills.jsonl` / `scanned-repos*.jsonl` / `data.tar.gz`) are released under [CC0 1.0](LICENSE-DATA) (public domain dedication): free to use, modify, and redistribute without attribution.
- Every skill included in the index remains subject to its upstream repository's own license; check the corresponding `source` repository before installing.

---

## For developers

How the data is produced and processed — quick start, data-flow overview, command reference, data layout, and CI notes — see **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**.
