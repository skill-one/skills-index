# Skills Index — 开发者指南

[English](DEVELOPMENT.md) | 简体中文

如何跑起来、数据怎么流动、规则在哪里。只想消费数据请看 [README.zh-CN.md](../README.zh-CN.md)。

## 快速开始

**环境要求**:Python >= 3.11 与 [`uv`](https://docs.astral.sh/uv/)。

```bash
uv sync                                # 安装依赖
uv run skills-index update             # 完整管线(无状态)
uv run skills-index update --pages 1   # 冒烟:只取 1 页

uv run ruff check . && uv run mypy src/skills_index && uv run pytest
```

> 通过环境变量或 `.env` 设置 `GH_PAT`(5000 次/小时)或 `GITHUB_TOKEN`(Actions 内置,1000 次/小时)。`SKILL.md` 内容与提交历史走 git clone,不占 REST 配额。

## 数据流

`update` 串起三步 —— 数据在内存中传递,不产生中间文件:

| 步骤                                                                                                                | 输出                                   |
| ------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `fetch` —— 拉取 skills.sh 历史总榜                                                                                  | 内存(records)                          |
| `scan` —— 每仓库:刷新 `stars`;一次裸仓库部分克隆定位每个目标 `skillId`,提取 `path` / `description` / `lastCommitAt` | 内存(rows)                             |
| `index` —— 两侧按 `(source, skillId)` 合并                                                                          | `data/index.jsonl` + `index-meta.json` |

步骤契约:[FETCH-SKILLS-SH.zh-CN.md](FETCH-SKILLS-SH.zh-CN.md)(fetch API)与 [SCAN-REPO.zh-CN.md](SCAN-REPO.zh-CN.md)(匹配规则、克隆通道、`lastCommitAt`)。

规则:

- **收录** —— 仅由 skills.sh 决定:未收录的技能不可见;仓库不再确认的技能被丢弃。
- **无状态扫描** —— 每轮都是全量扫描:仓库元数据 + 每仓库一次裸仓库部分克隆,全部现取。无缓存可维护,也完全没有任何跨轮记忆。仓库 404 则其技能全部丢弃;瞬时失败本轮跳过该仓库。绝不编造,仅在确凿证据下移除。
- **匹配** —— `skillId` 命中字典序第一个 `<任意前缀>/<skillId>/SKILL.md`(见 [SCAN-REPO.zh-CN.md](SCAN-REPO.zh-CN.md))。
- **合并与排序** —— 记录保持 skills.sh 排名顺序(fetch 顺序破平局);跨仓库重复(`skillId` + 非空 `description` 都相同)只留 `installs` 最高的一份。
- **`lastCommitAt` 是事实数据** —— 技能目录最近一次 commit 的时间,每轮直接取自 git 历史(先浅克隆,必要时 unshallow 保证精确;见 [SCAN-REPO.zh-CN.md](SCAN-REPO.zh-CN.md))。管线从不盖章或继承它。

## 命令参考

| 命令     | 说明                               | 关键参数              |
| -------- | ---------------------------------- | --------------------- |
| `fetch`  | 拉取 skills.sh 数据(不访问 GitHub) | `--pages N`(0 = 全部) |
| `update` | 一步跑完整条管线                   | `--pages N`, `--tag`  |

## 数据布局

一棵树,以平铺的 GitHub Release 资产发布(不进 git):

```
data/                              # 每轮运行生成
  index.jsonl                      # 最终索引(每技能一条)
  index-meta.json                  # formatVersion / generatedAt / counts.total(+ tag,即发布 tag)
```

管线在 `data/` 之外不保存任何状态,轮与轮之间零记忆:`lastCommitAt` 每轮直接取自 git 历史。记录只存 `path`,不存 `url` —— 用 `https://github.com/<source>/tree/HEAD/<path>` 拼出。

## 代码结构

```
src/skills_index/
├── cli.py        # argparse 入口:编排 + 运行报告
├── config.py     # 常量、路径、token 发现(叶子模块)
├── http.py       # httpx 封装:重试、认证、限流退避
├── io_utils.py   # JSON / JSONL 读写
├── github.py     # 仓库元数据、裸仓库部分克隆:匹配、description、lastCommitAt
├── fetch.py      # skills.sh -> 内存 records
├── scan.py       # 每仓库全量扫描:仓库元数据 + 一次克隆,匹配、stars
├── index.py      # 合并 + 去重 -> index.jsonl
```

`config` 是叶子;`http` / `io_utils` 只依赖 `config`;`fetch` / `github` 依赖 `http`;`scan` 依赖 `github` / `http`;`index` 依赖 `io_utils`;`cli` 只做编排。

## CI

每日 UTC 0 点([.github/workflows/daily.yml](../.github/workflows/daily.yml)),先过 lint + test 门禁(ruff / mypy / pytest,Python 3.11 + 3.13):

- **`main`** —— 全量管线。完全无状态:`lastCommitAt` 每轮直接取自 git 历史,轮与轮之间零记忆。
- **`test`** —— 1 页冒烟,发为 prerelease(永不占 `releases/latest`);从零开始。
- **发布** —— `index.jsonl` + `index-meta.json` 以单个 commit 强推孤儿 `dist` 分支,release tag 创建在该 commit 上 —— 一个 tag 同时定址 Release 下载与 CDN。2 件资产;运行报告(捕获自 `update` stdout)是 Release body。保留最近 10 个 `data-` Release。
