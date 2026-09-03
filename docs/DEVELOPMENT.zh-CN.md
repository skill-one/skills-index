# Skills Index — 开发者文档

[English](DEVELOPMENT.md) | 简体中文

面向开发者：如何跑起来、数据流是什么、命令怎么用、规则在哪里。若只关心如何消费数据，见根目录 [README.zh-CN.md](../README.zh-CN.md)。

---

## 快速开始

**环境要求**：Python >= 3.11、[`uv`](https://docs.astral.sh/uv/)。

```bash
uv sync                      # 安装依赖
uv run skills-index update   # 一键跑完整流水线（增量）

# 测试与检查
uv run ruff check .
uv run mypy src/skills_index
uv run pytest
```

> 可选：设置 `GITHUB_TOKEN`（环境变量或 `.env`）消除未认证的 60 次/小时限制；推荐 `GH_PAT`（个人访问令牌，5000 次/小时，优先于 Actions 内置 token 的 1000 次/小时）。`SKILL.md` 内容走 codeload tarball，不计入配额。

## 数据流概览

三步流水线（`update` 把它们串成一条命令）：

| 步骤 | 命令 | 做什么 | 产出 |
| --- | --- | --- | --- |
| 1. fetch | `skills-index fetch` | 拉取 skills.sh「历史总榜」原始数据 | `data/skills-sh/fetched-skills.jsonl` + `cache/by-source/*/`（仅为每个 source 建目录） |
| 2. scan | `skills-index scan` | 增量扫描 GitHub 仓库里的 `SKILL.md` | `data/github/scanned-repos.jsonl` + `cache/by-source/*/scanned.jsonl` + `meta.json` |
| 3. index | `skills-index index` | 合并前两步生成最终索引，并经由跨运行 rev 账本为每个技能盖版本日期 | `data/index/index.jsonl` + `index-meta.json` + `cache/rev-ledger.jsonl` |
| 4. update | `skills-index update` | 1→3 一步完成（本地测试与 CI 统一入口） | 以上全部 |

- **增量机制**：`scan` 先按 `pushed_at` 跳过未变更仓库；有推送的仓库再用一次 Trees API 比对**每个公开技能目录的 git tree sha** 与缓存基准，未变则跳过 tarball 下载（tree sha 覆盖整棵目录——内容、文件名与权限位，只改附属脚本也无法隐藏，这正是过去只比 `SKILL.md` blob sha 的域所漏掉的）。任何一次成功扫描后都会记下新基准供下轮比对（每个重扫仓库一次请求；预检已经取到就复用）。内容全等的镜像仓库（按 per-skill `rev` 判定）由去重逻辑**墓碑化**，后续运行直接跳过，直到任一方有新推送再重新裁决。
- **版本字段**：`scan` 为每个技能算出 `rev`（对目录内排序后的 `(path, git mode, blob sha)` 三元组取 sha256，发布其前 16 位 hex），`index` 连同 `firstSeenAt` 一起发布——即"首次记录到该 rev 的那一轮时间"，由 `cache/rev-ledger.jsonl` 求得。账本每个发布技能一行（不存历史），体积与索引同阶；本轮缺席的仓库其行**保留**，因为一次扫描失败不带任何信息，不该把整仓日期重置。发布值刻意**不带算法标识**（消费方只把 `rev` 当等值判据，即便带上也无人解析），因此这份摘要是一份冻结契约：改动指纹域、规范化或哈希会改写全部发布值，属于破坏性变更——必须同轮 bump `SCHEMA_VERSION`（触发全量重建）与 `INDEX_FORMAT_VERSION`，并接受一次 `firstSeenAt` 重置。`tests/test_github_tarball.py` 钉住了固定输入的摘要值，无意的变动会在 CI 上失败。
- **失败安全**：`update` 增量模式下，fetch 有失败页时**跳过**过期仓库清理（prune），避免把"恰好落在失败页上的仓库"误判为已消失。
- **过滤**：流水线内置多层过滤（仓库级 / skill 级 / 合并级），完整规则见 [FILTERING.zh-CN.md](FILTERING.zh-CN.md)。
- **最终索引** `index.jsonl` 的字段与消费方式见 [README.zh-CN.md](../README.zh-CN.md)。

## 命令参考

| 命令 | 说明 | 关键参数 |
| --- | --- | --- |
| `skills-index fetch` | 拉取 skills.sh 数据 | `--pages N`（0 = 全量） |
| `skills-index scan` | 增量扫描仓库 | `--force` 全量重扫；`--max-skill-count N` 过滤上限（默认 500，0 关闭） |
| `skills-index index` | 合并生成索引 | 无 |
| `skills-index update` | 一步跑完整流水线 | `--pages N`；`--force` 全量重建；`--max-skill-count N` |
| `skills-index pull` | **完全独立的辅助命令**（不属于流水线）：下载最新 `data-` Release 的 `data.tar.gz` 快照到 `pulled/<tag>/` 并打印检查报告，全程不碰 `data/` 或 `cache/` | `--repo owner/repo` 或 GitHub URL（默认 `skill-one/skills-index`）；`--dest` 落地根目录（默认 `pulled/`） |

## 在本地检查已发布快照（`pull`）

`pull` 是一条**完全独立的辅助命令**，与 `fetch → scan → index → update` 流水线正交：它不读写 `data/` 或 `cache/`、不参与增量链路，也不引入任何第三方依赖（仅复用 `http` / `config` / `io_utils` 三层）。它的存在是为了让人先把 CI 实际发布出来的东西拿到本地看一眼，再去对接工具。

它做四件事：

1. 解析该仓库的**最新 `data-` GitHub Release**。冒烟（`alpha-`）Release 由 CI 标记为 prerelease，因此 `releases/latest` 永远指向最新的全量快照。
2. 通过 `data.tar.gz` 资产的公开 `browser_download_url` 下载（由对象存储匿名提供——公开仓库无需鉴权；下载流量不计入 REST 配额）。
3. **安全地**解包到 `pulled/<tag>/data/`，与发布布局一致。若同名 tag 目录已存在则先整体删除，保证重拉永远是一份忠实、完整的拷贝。路径穿越（`../`、绝对路径）成员与非常规条目（符号链接 / 设备文件）在写入任何字节前即被拒绝。解包后删除压缩包。
4. 打印一份检查报告：`index-meta.json`（`formatVersion` / `generatedAt` / `counts.total`）、`index.jsonl` 的**实际行数**（与 `counts.total` 不符时给出 `⚠`），以及 `data/` 下每个文件的大小。

`pulled/` 目录已被 git 忽略。

```bash
uv run skills-index pull                    # 本仓库最新快照 -> pulled/<tag>/
uv run skills-index pull --repo owner/repo  # 任意 GitHub 仓库的最新 data- Release
```

## 数据布局

发布数据与增量缓存是两棵独立的树：

```
data/                                  # 发布数据（data.tar.gz，面向数据消费者）
  run-summary.md                     # 本次运行报告
  skills-sh/                         # 第1步：skills.sh 内容
    fetched-skills.jsonl             #   skills.sh 原始数据汇总
  github/                            # 第2步：仓库扫描内容
    scanned-repos.jsonl              #   按仓库汇总（原始扫描顺序）
  index/                             # 第3步：最终产物
    index.jsonl                      #   最终索引（以 skill 为单位）
    index-meta.json                  #   索引自描述元数据（formatVersion / generatedAt / counts.total；distCommit 由 CI 回填）

cache/                                 # 增量缓存（cache.tar.gz，仅供下一轮 CI 恢复）
  rev-ledger.jsonl                   # 跨运行版本账本：每个发布技能一行（rev + firstSeenAt）
  by-source/<owner>__<repo>/         # per-repo 状态；双下划线是 '/' 的无损替换
    scanned.jsonl / meta.json
```

> Release 资产名是扁平的文件名（basename），因此上面的目录分层不影响
> `releases/download/<tag>/<文件名>` 的下载 URL；`data.tar.gz` / `cache.tar.gz`
> 内部分别是这两个树的布局。

- `cache/by-source/<owner>__<repo>/meta.json` 是带 `status` 标记的单一形态（见 `src/skills_index/cache.py`）：
  - `ok` —— 正常缓存：`branch` / `pushedAt` / `stars` / `skillCount` / `skillTreeShas`（该仓库公开技能目录的 `{path: git tree sha}` 基准，下轮与 Trees API 比对；取不到时可以留空，代价只是下次推送多下载一次 tarball）；
  - `filtered` —— 超过 skillCount 上限被排除：保留 `pushedAt` + `skillCount`，后续运行免 tarball 跳过，直至仓库有新推送再重新裁决；
  - `tombstoned` —— 被镜像去重淘汰：`dedupedInto` + `winnerPushedAt` + `pushedAt`，直至任一方有新推送。
  - `scanned.jsonl` 仅在 `ok` 状态存在，每条技能一行 `{path, rev, description}`；任何一次重写都会清理目录内契约之外的遗留文件。
- `cache/rev-ledger.jsonl` 由 **index** 步骤写入而非 `scan`，且刻意放在 `cache/` 根目录：`clean_workspace` 只清 `cache/by-source/`、`RepoCache` 只清仓库目录内的文件，账本因此两者都不受影响，同时仍会随 `cache.tar.gz` 一起打包恢复。

> 产物只存 `path` 不存 `url`；完整 GitHub 目录链接用 `source` + `path` 拼成 `https://github.com/<source>/tree/HEAD/<path>`。

## 代码结构

```
src/skills_index/
├── __init__.py   # 包版本号
├── cli.py        # argparse 入口：fetch / scan / index / update / pull 编排 + 运行报告
├── config.py     # 常量、路径（data/ 与 cache/ 注册表）、过滤规则、token 发现、source<->dir 映射（叶子模块）
├── http.py       # 轻量 httpx 封装：客户端、重试、GitHub 鉴权、限流退避、二进制下载
├── io_utils.py   # JSON / JSONL 读写辅助
├── cache.py      # 跨运行状态：RepoCache（per-repo 指纹）+ RevLedger（rev -> firstSeenAt）
├── github.py     # GitHub 接口：仓库元数据、Trees 预检、codeload tarball 解析、skill_rev
├── fetch.py      # 拉取 skills.sh -> 过滤 -> 按 source 分发；含 prune_stale_repos
├── scan.py       # 增量扫描（pushed_at / Trees 预检 / 指纹）+ 汇总 + 镜像去重
├── index.py      # 合并 fetched + scanned，盖 rev/firstSeenAt，生成 data/index.jsonl
└── pull.py       # 独立命令：下载并检查最新已发布 data- Release 快照（不耦合流水线）
```

依赖方向无环：`config` 是叶子，被所有模块依赖；`http` 仅依赖 `config`；`cache` 依赖 `config` / `io_utils`；`github` / `fetch` 依赖 `http` / `config` / `io_utils`；`scan` 与 `index` 额外依赖 `cache`（指纹 / rev 账本）；`pull` 仅复用 `http` / `config` / `io_utils`、不引入新依赖，且不被流水线任何模块引用（独立自持）；`cli` 只做编排，不实现业务逻辑。

## CI

数据由 GitHub Actions（`.github/workflows/daily.yml`）每日 UTC 0 点自动生成并发布为 Release：每次运行（含冒烟）先经 **lint + test 门禁**（ruff / mypy / pytest + 覆盖率，Python 3.11 与 3.13 双版本矩阵），通过后 `main` 全量 fetch、`test` 分支拉 1 页冒烟（冒烟 Release 标记为 prerelease，永不占用 `releases/latest`）；全量运行前从上一个 `data-` Release 恢复增量状态（`cache/by-source/` 指纹与根目录的 rev 账本，下载其 `cache.tar.gz`）。在 `main` 上，`index.jsonl` 与 `index-meta.json` 会在打包与发布**之前**先强推到孤儿 `dist` 分支：推送分两个 commit，CI 把首个 commit 的 sha（两次 commit 中 `index.jsonl` 字节完全一致）回填为元数据的 `distCommit`；由于这一步在打包之前，回填后的 meta 会经工作区副本一并进入 `data.tar.gz` 与 Release 资产，于是 Release 与 `dist` 两侧的 `index.jsonl` / `index-meta.json` **逐字节一致**，消费者也能用 immutable 的 commit 定址 URL 拉取正文。冒烟（`test` 分支）跳过 dist 推送，其 meta 自然省略 `distCommit`。`data/` 与 `cache/` 分开打包（`data.tar.gz`、`cache.tar.gz`）。Release 现在只带**精简的 5 件资产**——`index.jsonl`、`index-meta.json`、`data.tar.gz`、`cache.tar.gz`、`run-summary.md`——中间 jsonl（`fetched-skills.jsonl`、`scanned-repos.jsonl`）只随 `data.tar.gz` 发布、不再单独作为资产；保留最近 10 个 Release。
