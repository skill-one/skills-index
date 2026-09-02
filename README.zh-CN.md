# Skills Index

[English](README.md) | 简体中文

[skills.sh](https://skills.sh) 技能的索引：在一处查看每个技能的 `source` / `skillId` / `installs` / `weeklyInstalls`（来自 skills.sh），以及所在仓库的 `stars`、经仓库扫描得到的 GitHub 仓库内相对路径 `path` 与技能说明 `description`。

**消费方无需 clone 仓库**，直接拉取发布好的索引快照即可。数据的产生方式见 [docs/DEVELOPMENT.zh-CN.md](docs/DEVELOPMENT.zh-CN.md)（面向开发者）。

---

## 数据是什么

最终索引 `index.jsonl` **以技能为单位平铺**，每行一个技能：

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
  "description": "Discover and install agent skills",
  "rev": "t1-8f3ac21d9b0c4e5f",
  "firstSeenAt": "2026-08-24T14:20:17Z"
}
```

| 字段              | 说明                                                                 |
| ----------------- | -------------------------------------------------------------------- |
| `source`          | GitHub 仓库，形如 `owner/repo`                                       |
| `skillId`         | 技能标识（技能目录名）                                               |
| `stars`           | 技能所在仓库的 star 数（仓库级：同一仓库的所有技能共享同一值）         |
| `installs`        | 总安装量（来自 skills.sh）                                           |
| `weeklyInstalls`  | 近 8 周周安装量（来自 skills.sh，按时间顺序）                        |
| `path`            | 技能在仓库内的相对路径（如 `skills/find-skills`）                    |
| `description`     | 技能说明（来自 `SKILL.md` frontmatter）                              |
| `rev`             | 技能**目录**的内容指纹：`t1-` + 截断 sha256，覆盖目录内每个文件的（路径、git mode、blob sha）。内容变则必变 |
| `firstSeenAt`     | 首次记录到该 `rev` 的那一轮索引的 UTC 时间，即"这个版本何时进入索引"   |

### 版本与更新

`rev` 是判断"已安装的技能是否过期"的**唯一**依据：安装时记下它，之后每次刷新做**相等**比较。它是摘要而非版本号——两个 `rev` 之间没有大小关系，因此不要写 `remote > local`，也不要自造 `v1/v2/v3` 计数（一旦上游回滚就露馅）。相等比较同时保证了零噪声：提交时间、作者、README 改动、同仓库其它技能的推送，都不会改变 `rev`。

`firstSeenAt` 面向用户：可读的"3 天前更新"，以及待更新列表的排序键。它只随 `rev` 一起前进，所以可以安全地当作版本日期展示。`rev` 本身只在用户需要精确指认某一份内容时才露出（详情行、tooltip 里的 `8f3ac21d9b0c4e5f`，或更新提示中的 `8f3ac21 → b7d1f04`）；"跳过此版本"要存下的也正是这个值。

指纹刻意不覆盖的东西：技能目录**之外**的文件（共享的 `../lib`、仓库级 `AGENTS.md`）以及归档里不存在的内容。作者在 frontmatter 里声明的 `version:` 同样不发布——约十分之一的技能写了它，且不保证随内容维护，因此无法充当变更探测器；想看改了什么请用提交历史链接：
`https://github.com/<source>/commits/HEAD/<path>`。

> 索引**不存储可直接访问的 `url`**；完整 GitHub 目录 URL 由 `source` + `path` 拼接得到：
> `https://github.com/<source>/tree/HEAD/<path>`（`HEAD` 恒指向默认分支，分支变更也不受影响）。
>
> skills.sh 未收录的技能同样会进入索引，只是 `installs` / `weeklyInstalls` 字段不出现。每条记录都携带 `stars`、`rev` 与 `firstSeenAt`。

## 发布的产物

每个 Release 包含：

| 文件                  | 说明                                                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `index.jsonl`         | 合并后的最终索引（以**技能**为单位平铺，**推荐直接消费这个**）                                                           |
| `index-meta.json`     | 索引自描述元数据：`formatVersion`（字段变更时递增）、`generatedAt`（生成时间）、`counts.total`，以及 `distCommit`（承载本快照 `index.jsonl` 的 `dist` 分支 commit，由 CI 回填——见 [通过 CDN 访问](#通过-cdn-访问浏览器可直接用)） |
| `data.tar.gz`         | 完整数据快照（内部按 `skills-sh/` / `github/` / `index/` 分目录，仅含发布数据，不含流水线内部状态）。同时携带中间产物 `fetched-skills.jsonl` 与 `scanned-repos.jsonl`——它们不再作为独立资产单独发布 |
| `cache.tar.gz`        | 增量状态：`cache/by-source/` 下的 per-repo 指纹 + rev 账本 `cache/rev-ledger.jsonl`（让 `firstSeenAt` 在内容未变时保持稳定）。仅供下一轮 CI 恢复；数据消费者无需下载 |
| `run-summary.md`      | 本次运行报告（各阶段计数与耗时）                                                                                         |

## 如何获取与使用

每个 Release 的 tag 形如 `data-YYYYMMDDThhmmssZ`（UTC 时间戳，例如 `data-20260820T143015Z`）。**生产环境应锁定到固定 tag**，而非 `latest`：固定 tag 指向确定快照，可复现且 `releases/download/<tag>/<file>` 是稳定直链、缓存友好；`latest` 是 302 重定向，目标随时变化、缓存命中率低。

```bash
# 生产环境：固定 tag（推荐）
TAG=data-20260820T143015Z
curl -L -o index.jsonl \
  https://github.com/skill-one/skills-index/releases/download/$TAG/index.jsonl

# 拉取完整数据快照（同样用固定 tag，保证与 index.jsonl 同源）
curl -L -o data.tar.gz \
  https://github.com/skill-one/skills-index/releases/download/$TAG/data.tar.gz
```

> 想拿到最新快照的 tag 而不写死时间戳，可用 `gh` 解析最新的 `data-` Release；若要锁定某一天，把 `TAG` 直接写死成当天 tag 即可：
>
> ```bash
> TAG=$(gh release list --limit 100 --json tagName,createdAt \
>   --jq 'sort_by(.createdAt) | reverse | .[].tagName' \
>   | grep '^data-' | head -n 1)
> ```

```bash
# 本地调试 / 想要最新：用 latest（注意 302 重定向、缓存不友好）。
# latest 永远指向完整的 data- 快照：冒烟 alpha- Release 均标记为
# prerelease，不会成为 latest。
curl -L -o index.jsonl \
  https://github.com/skill-one/skills-index/releases/latest/download/index.jsonl
```

### 通过 CDN 访问（浏览器可直接用）

`index.jsonl` 同时被发布到一个孤儿分支 `dist`，可通过 jsDelivr 等带 CORS 头的 CDN 在浏览器中直接访问：

```
https://cdn.jsdelivr.net/gh/skill-one/skills-index@dist/index.jsonl
https://cdn.jsdelivr.net/gh/skill-one/skills-index@dist/index-meta.json
```

> `dist` 分支每次全量发布时强推覆盖，始终指向最新一次 `main` 上的快照；仅 `main` 发布，冒烟测试不会覆盖它。

生产环境的缓存方案建议用 **commit 定址**：先读 `index-meta.json` 拿到 `distCommit`，再通过该 commit 拉取正文。`@dist` 是可变 ref（CDN 缓存需要手动打散），而 commit sha 永不变化，因此 URL 可永久缓存、也不会取到陈旧快照：

```
https://cdn.jsdelivr.net/gh/skill-one/skills-index@<distCommit>/index.jsonl
```

也可在仓库 Releases 页面选择任意历史快照按需下载。保留最近 10 个 Release，超出部分自动清理。

---

## 许可

- **代码**（`src/`、`tests/`、CI 工作流等）按 [MIT](LICENSE) 发布。
- **发布的数据产物**（`index.jsonl` / `index-meta.json` / `fetched-skills.jsonl` / `scanned-repos*.jsonl` / `data.tar.gz`）按 [CC0 1.0](LICENSE-DATA)（公有领域贡献）发布，可自由使用、修改与再分发，无需署名。
- 索引中收录的每个技能仍受其上游仓库自身许可约束；安装前请查阅对应 `source` 仓库。

---

## 面向开发者

数据的产生与处理流程：快速开始、数据流概览、命令参考、数据布局与 CI 说明，见 **[docs/DEVELOPMENT.zh-CN.md](docs/DEVELOPMENT.zh-CN.md)**。
