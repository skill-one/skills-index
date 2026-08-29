# Skills Index

[English](README.md) | 简体中文

[skills.sh](https://skills.sh) 技能的索引：在一处查看每个技能的 `source` / `skillId` / `installs` / `weeklyInstalls`（来自 skills.sh），以及经仓库扫描得到的 GitHub 仓库内相对路径 `path` 与技能说明 `description`。

**消费方无需 clone 仓库**，直接拉取发布好的索引快照即可。数据的产生方式见 [docs/DEVELOPMENT.zh-CN.md](docs/DEVELOPMENT.zh-CN.md)（面向开发者）。

---

## 数据是什么

最终索引 `index.jsonl` **以技能为单位平铺**，每行一个技能：

```json
{
  "source": "vercel-labs/skills",
  "skillId": "find-skills",
  "installs": 3005209,
  "weeklyInstalls": [
    113781, 109199, 109085, 115475, 107969, 101120, 96861, 93130
  ],
  "path": "skills/find-skills",
  "description": "Discover and install agent skills"
}
```

| 字段              | 说明                                                                 |
| ----------------- | -------------------------------------------------------------------- |
| `source`          | GitHub 仓库，形如 `owner/repo`                                       |
| `skillId`         | 技能标识（技能目录名）                                               |
| `installs`        | 总安装量（来自 skills.sh）                                           |
| `weeklyInstalls`  | 近 8 周周安装量（来自 skills.sh，按时间顺序）                        |
| `path`            | 技能在仓库内的相对路径（如 `skills/find-skills`）                    |
| `description`     | 技能说明（来自 `SKILL.md` frontmatter）                              |

> 索引**不存储可直接访问的 `url`**；完整 GitHub 目录 URL 由 `source` + `path` 拼接得到：
> `https://github.com/<source>/tree/HEAD/<path>`（`HEAD` 恒指向默认分支，分支变更也不受影响）。
>
> skills.sh 未收录的技能同样会进入索引，只是 `installs` / `weeklyInstalls` 字段不出现。

## 发布的产物

每个 Release 包含：

| 文件                                | 说明                                                                                                                     |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `index.jsonl`                       | 合并后的最终索引（以**技能**为单位平铺，**推荐直接消费这个**）                                                           |
| `index-meta.json`                   | 索引自描述元数据：`generatedAt`（生成时间）、`counts`、`formatVersion`（字段变更时递增）                                 |
| `data.tar.gz`                       | 完整数据快照（内部按 `skills-sh/` / `github/` / `index/` 分目录，仅含发布数据，不含流水线内部状态）                    |
| `cache.tar.gz`                      | 增量扫描缓存（`cache/by-source/` 下的 per-repo 指纹），仅供下一轮 CI 恢复增量状态；数据消费者无需下载                   |
| `fetched-skills.jsonl`              | skills.sh 原始数据汇总（中间产物）                                                                                       |
| `scanned-repos.jsonl`               | 按仓库汇总的扫描结果，**原始扫描顺序**（fetch 拉取到的顺序，未排序）                                                     |
| `scanned-repos-by-stars.jsonl`      | 按 **star 数降序**排列的扫描结果                                                                                         |
| `scanned-repos-by-skillcount.jsonl` | 按**安装 skills 技能数（`skillCount`）降序**排列的扫描结果                                                               |
| `run-summary.md`                    | 本次运行报告（各阶段计数与耗时）                                                                                         |

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

也可在仓库 Releases 页面选择任意历史快照按需下载。保留最近 10 个 Release，超出部分自动清理。

---

## 许可

- **代码**（`src/`、`tests/`、CI 工作流等）按 [MIT](LICENSE) 发布。
- **发布的数据产物**（`index.jsonl` / `index-meta.json` / `fetched-skills.jsonl` / `scanned-repos*.jsonl` / `data.tar.gz`）按 [CC0 1.0](LICENSE-DATA)（公有领域贡献）发布，可自由使用、修改与再分发，无需署名。
- 索引中收录的每个技能仍受其上游仓库自身许可约束；安装前请查阅对应 `source` 仓库。

---

## 面向开发者

数据的产生与处理流程：快速开始、数据流概览、命令参考、数据布局与 CI 说明，见 **[docs/DEVELOPMENT.zh-CN.md](docs/DEVELOPMENT.zh-CN.md)**。
