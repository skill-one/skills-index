# Skills Index

English | [简体中文](README.zh-CN.md)

A merged index of [skills.sh](https://skills.sh) skills, published as one JSONL file. skills.sh decides **which** skills exist and how popular they are; this project adds what it does not expose from each skill's GitHub repository: `path`, `description`, `lastCommitAt`, `stars`. Consumers never touch GitHub.

## The data

`index.jsonl` — one skill per line:

```json
{
  "skillId": "find-skills",
  "source": "vercel-labs/skills",
  "stars": 30359,
  "description": "Helps users discover and install agent skills when they ask questions like \"how do I do X\", \"find a skill for X\", \"is there a skill that can...\", or express interest in extending capabilities.",
  "installs": 3248317,
  "weeklyInstalls": [
    109085, 115475, 107969, 101120, 96861, 93130, 100221, 103058
  ],
  "path": "skills/find-skills",
  "lastCommitAt": "2026-09-04T07:10:31Z"
}
```

| Field            | From      | Meaning                                                                                                                                          |
| ---------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `skillId`        | skills.sh | Skill identifier (the skill's directory name)                                                                                                    |
| `source`         | skills.sh | GitHub repository, `owner/repo`                                                                                                                  |
| `stars`          | GitHub    | Star count of the repository (repo-level)                                                                                                        |
| `installs`       | skills.sh | Total installs                                                                                                                                   |
| `weeklyInstalls` | skills.sh | Last 8 weeks' installs, chronological                                                                                                            |
| `path`           | GitHub    | Skill directory, relative to the repo root                                                                                                       |
| `description`    | GitHub    | `description` from the `SKILL.md` frontmatter (may be empty)                                                                                     |
| `lastCommitAt`   | GitHub    | Committer date (UTC) of the most recent commit touching the skill directory — the skill's true last-updated time, read straight from git history |

- **Update detection** — show `lastCommitAt` as the version date; it moves exactly when the skill's content does. See what changed: `https://github.com/<source>/commits/HEAD/<path>`.
- **No `url` field** — compose `https://github.com/<source>/tree/HEAD/<path>` from `source` + `path`.
- **Inclusion** — every record is registered on skills.sh and confirmed by its repository; skills.sh alone decides.

## Get it

Releases are tagged `data-YYYYMMDDThhmmssZ` — pin a tag for production, `releases/latest` for a quick look:

```bash
curl -LO https://github.com/skill-one/skills-index/releases/download/data-20260904T032842Z/index.jsonl   # pinned
curl -LO https://github.com/skill-one/skills-index/releases/latest/download/index.jsonl                  # newest
```

Same tag, CORS-enabled CDN copy:

```
https://cdn.jsdelivr.net/gh/skill-one/skills-index@data-20260904T032842Z/index.jsonl
```

| Asset             | What it is                                               |
| ----------------- | -------------------------------------------------------- |
| `index.jsonl`     | The product                                              |
| `index-meta.json` | `formatVersion` / `generatedAt` / `counts.total` / `tag` |

The run report is the Release body itself — no separate asset.

## License

Code: [MIT](LICENSE). Published data: [CC0 1.0](LICENSE-DATA) (public domain). Each indexed skill remains under its upstream repository's own license.

## For developers

Pipeline, rules, contracts, CI: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
