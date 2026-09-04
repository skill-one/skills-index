# Locating Skills in a Repo — How the Scan Works

English | [简体中文](SCAN-REPO.zh-CN.md)

skills.sh is the registry: it alone decides which skills exist (`source` + `skillId`). The scan answers what it does not expose — per repo, one git clone yields `path`, `description`, `lastCommitAt`, plus `stars` (repo metadata, refreshed every run).

## Match

```
<any prefix>/<skillId>/SKILL.md     # the only shape that matches
```

- `skillId` is the matched directory's basename; a bare root `SKILL.md` never matches (no parent directory to name a skill).
- All candidates sort lexicographically; the **first** wins. Deterministic, not authoritative: with several same-named directories, skills.sh's crawler may have registered another copy — no static rule can know, so first-match is the accepted definition.
- A matched `SKILL.md` publishes whatever `description` its frontmatter carries, including empty. Only "not found" removes: a `skillId` matching nothing is dropped; a 404 repo drops all its skills.

## Clone and history

One channel: a bare partial clone (`git clone --bare --filter=blob:limit=65536`) — the size cap keeps SKILL.md-class blobs local while excluding large assets, and the git protocol is not billed against the REST quota (never `git clone` into REST, no Trees API). Flow: `ls-tree -r HEAD` lists candidate paths → match → per hit, `git log -1 -- <dir>` gives the directory's true last commit time (`lastCommitAt`, normalized to UTC) and `git show HEAD:<dir>/SKILL.md` gives the frontmatter.

History is fetched shallow first (`--shallow-since=1.year`), with two exactness fallbacks:

- a repo with **no commit inside the window** fails the shallow request outright (`no commits selected`) — retried once with full history; the failed attempt transfers no objects, so it is cheap;
- a shallow boundary commit is grafted as a root commit that "created" every surviving path, so a `lastCommitAt` pointing at it may be fabricated — whenever a reported commit sits on the boundary (`.git/shallow`), the clone is unshallowed once and the extraction recomputed.

Either way every published date is the exact committer date, and since the values come straight from history, recomputing always yields the same result — there is no cache to maintain.

## What the scan no longer does

Inclusion moved to skills.sh, so the discovery machinery is gone: no discovery of unregistered skills, no installer placement rules (priority dirs / depth caps / hidden dirs), no content filtering (internal paths / frontmatter validity / non-public markers), no mirror tombstones or skill-count cap, no content fingerprint (`rev`) — git history answers "when did this change" directly. A repo contributes exactly the skillIds skills.sh lists; mirrors are left to the index step's (`skillId` + `description`) dedup.

The installer optimizes for recall; this project delegates inclusion to skills.sh and optimizes for simplicity.
