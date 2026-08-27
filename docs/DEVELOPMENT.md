# Skills Index — 开发者文档

面向开发者：如何跑起来、数据流是什么、命令怎么用、规则在哪里。若只关心如何消费数据，见根目录 [README.md](../README.md)。

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
| 1. fetch | `skills-index fetch` | 拉取 skills.sh「历史总榜」原始数据 | `data/fetched-skills.jsonl` + `data/by-source/*/fetched.jsonl` |
| 2. scan | `skills-index scan` | 增量扫描 GitHub 仓库里的 `SKILL.md` | `data/scanned-repos*.jsonl` + `data/by-source/*/scanned.jsonl` + `meta.json` |
| 3. index | `skills-index index` | 合并前两步，生成最终索引 | `data/index.jsonl` |
| 4. update | `skills-index update` | 1→3 一步完成（本地测试与 CI 统一入口） | 以上全部 |

- **增量机制**：`scan` 按 `pushed_at` + 文件级 blob sha 指纹，只重扫有变化的部分，未变化复用本地缓存。
- **过滤**：流水线内置多层过滤（仓库级 / skill 级 / 合并级），完整规则见 [FILTERING.md](FILTERING.md)。
- **最终索引** `index.jsonl` 的字段与消费方式见 [README.md](../README.md)。

## 命令参考

| 命令 | 说明 | 关键参数 |
| --- | --- | --- |
| `skills-index fetch` | 拉取 skills.sh 数据 | `--pages N`（0 = 全量） |
| `skills-index scan` | 增量扫描仓库 | `--force` 全量重扫；`--max-skill-count N` 过滤上限（默认 500，0 关闭） |
| `skills-index index` | 合并生成索引 | 无 |
| `skills-index update` | 一步跑完整流水线 | `--pages N`；`--force` 全量重建；`--max-skill-count N` |

## 数据布局

```
data/
  fetched-skills.jsonl               # 第1步：skills.sh 原始数据汇总
  scanned-repos.jsonl                # 第2步：按仓库汇总（原始扫描顺序）
  scanned-repos-by-stars.jsonl       # 第2步：按 star 降序
  scanned-repos-by-skillcount.jsonl  # 第2步：按 skillCount 降序
  index.jsonl                        # 第3步：最终索引（以 skill 为单位）
  by-source/<owner>__<repo>/         # 双下划线是 '/' 的无损替换
    fetched.jsonl / scanned.jsonl / meta.json
```

> 产物只存 `path` 不存 `url`；完整 GitHub 目录链接用 `source` + `path` 拼成 `https://github.com/<source>/tree/HEAD/<path>`。

## 代码结构

模块职责与依赖方向见 [src/README.md](../src/README.md)。

## CI

数据由 GitHub Actions（`.github/workflows/daily.yml`）每日 UTC 0 点自动生成并发布为 Release：`main` 全量 fetch、`test` 分支拉 1 页冒烟；全量运行前从上一个 `data-` Release 恢复 `by-source/` 增量指纹；`index.jsonl` 额外强推到 `dist` 分支供 jsDelivr 等 CDN 访问；保留最近 10 个 Release。
