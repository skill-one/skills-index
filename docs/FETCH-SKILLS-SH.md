# Fetching All Skills from skills.sh

English | [简体中文](FETCH-SKILLS-SH.zh-CN.md)

This document reproduces the fetch step from scratch — no GitHub access involved.

## API

```
GET https://skills.sh/api/skills/all-time/{page}
```

- `page` starts at **0**; the response body is `{"skills": [...], "hasMore": bool, "total": int, "page": int}`.
- Continue while `hasMore` is true. No auth. Pages are spaced 0.3 s; a failing page is skipped and recorded, 3 consecutive failures abort the run.

## Fields (kept verbatim)

| Field            | Type      | Description                                           |
| ---------------- | --------- | ----------------------------------------------------- |
| `source`         | `string`  | GitHub repo, `owner/repo`                             |
| `skillId`        | `string`  | Skill identifier (skill directory name)               |
| `installs`       | `int`     | Cumulative installs                                   |
| `weeklyInstalls` | `int[]`   | Last 8 weeks' installs, chronological                 |
| `name`           | `string`  | Display name (currently identical to `skillId`)       |
| `isOfficial`     | `boolean` | Official-skill flag (present only on official skills) |

> Filter: only `owner/repo` sources (contains `/`, not a full URL) are kept.

## Output and sample

No file is written: `run_fetch` returns the records in memory, and `update` passes them straight to the scan step, which groups skillIds by `source` itself. Each record keeps the fields verbatim, e.g.:

```json
{
  "skillId": "find-skills",
  "source": "vercel-labs/skills",
  "name": "find-skills",
  "installs": 3248317,
  "weeklyInstalls": [
    109085, 115475, 107969, 101120, 96861, 93130, 100221, 103058
  ],
  "isOfficial": true
}
```
