# 过滤机制说明

流水线的全部过滤规则，分三层：**仓库级**（整个仓库丢弃）、**仓库内 skill 级**（单个 SKILL.md 丢弃）、**索引合并级**（合并时剔除/去重）。实现位置以 `源文件::函数` 标注，配置项集中在 `src/skills_index/config.py`。

## 总览

```
skills.sh API ──▶ [fetch] ──▶ [scan] ──▶ [index] ──▶ index.jsonl
                   F1 F2      S1 S2 S3    I1 I2 I3
                             F3 F4 F5
```

| 编号 | 过滤 | 位置 | 一句话规则 |
| --- | --- | --- | --- |
| F1 | 排行榜入口门槛 | `fetch` | 必须出现在 skills.sh all-time 榜单 |
| F2 | 非 GitHub 源 | `fetch::filter_github` | source 不是 `owner/repo` 形式即丢弃 |
| F3 | 仓库已不存在 | `scan::_scan_one_repo` | GitHub 404 → 删除该仓库全部缓存数据 |
| F4 | 高技能数仓库 | `scan::_scan_one_repo` | skillCount > 500（聚合型仓库）→ 丢弃并删缓存 |
| F5 | 内容指纹镜像去重 | `scan::_dedup_repos` | 整棵技能树 blob sha 全等 → 只留星数最高者 |
| S1 | 文件名约定 | `github::_parse_tarball` | 只收集 `…/SKILL.md`，其余文件一律忽略 |
| S2 | 内部路径过滤 | `config::is_internal_skill_path` | SKILL.md 位于仓库内部目录 → 丢弃 |
| S3 | 非公开 frontmatter | `github::is_nonpublic_frontmatter` | 作者声明 hidden/private/… → 丢弃 |
| I1 | 孤儿技能 | `index::run_index` | 仓库有、榜单无 → 保留，元数据置空（`installs: 0` / `weeklyInstalls: []`） |
| I2 | 榜单失配 | `index::run_index` | 榜单有、仓库扫描没有 → 剔除 |
| I3 | 跨仓库技能去重 | `index::_dedup_skills` | skillId + description 双匹配 → 保留 installs 最高者 |

最终索引以**仓库扫描为基准**：收录范围由扫描结果决定（扫描已过 F1–F5 / S1–S3），skills.sh 榜单只挂载 `installs` 等元数据，未收录技能以空元数据入索引（I1）；榜单有、仓库无的技能被 I2 剔除——索引中的每个技能都有当前真实存在的仓库路径背书。

---

## 一、仓库级过滤

### F1 排行榜入口门槛（隐式）

`fetch` 只从 skills.sh `all-time` 榜单拉取，不在榜单的仓库不进流水线。无显式调节项。

### F2 非 GitHub 源

- **规则**：`fetch::filter_github` 用 `config::is_github_source`（正则 `^[^/\s]+/[^/\s]+$`）校验，非 `owner/repo` 形式整条丢弃。
- **同时**：只保留 `KEEP_FIELDS`（`source` / `skillId` / `installs` / `weeklyInstalls`），不落盘任何 URL 字段。
- **计数**：`dropped_non_github`。

### F3 仓库已不存在（404）

- **规则**：GitHub 返回 404（已删除/改名/转私有）→ 删除 `by-source/<owner>__<repo>/` 全部缓存，仓库及其技能从索引消失。
- **判定**：`github::_is_missing_repo` 沿异常链查 404，与网络错误/5xx/限流区分——后者只跳过本次，不删数据。
- **计数**：`repos_gone`。

### F4 高技能数仓库（聚合商过滤）

- **规则**：`skillCount` > `MAX_SKILL_COUNT`（默认 **500**）→ 整仓库丢弃并删缓存。
- **可调**：`--max-skill-count N` 覆盖，`0` 关闭。
- **覆盖增量分支**：`pushed_at` 未变化的仓库用缓存 `meta.json` 的 `skillCount` 复查，规则收紧后仍会被过滤。
- **计数**：`repos_filtered` / `repos_filtered_high_skill`。

### F5 内容指纹镜像去重

- **规则**：`scan::_dedup_repos` 把每个仓库的 `{path: blob_sha}` 技能树序列化为指纹；指纹全等 = 未分叉镜像，组内只留星数最高者，其余删缓存。
- **边界**：`skillCount == 0` 的仓库无指纹，不参与去重。
- **计数**：`repos_deduped`。

---

## 二、仓库内 skill 级过滤

发生在 `github::_parse_tarball`，顺序即优先级 S1 → S2 → S3，任一命中即丢弃（S2/S3 共用计数 `skills_filtered_nonpublic`）。

### S1 文件名约定

只收集路径以 `/SKILL.md` 结尾的文件（技能必须位于自己的目录内，如 `skills/foo/SKILL.md`）。仓库根级单独的 `SKILL.md` 有意不收集。

### S2 内部路径过滤（`config::is_internal_skill_path`）

对 SKILL.md 所在目录检查，**整段精确比较、大小写不敏感**（`testing` ≠ `test`）：

- **状态词（任意路径段，含技能目录名）**：`deprecated` / `hidden` / `private` / `internal` / `obsolete`（`SKILL_EXCLUDE_ANY_DIRS`）。
- **结构词（仅中间目录段，不含最后一段）**：`test` / `tests` / `__tests__` / `spec` / `e2e` / `example` / `examples` / `sample` / `samples` / `demo` / `demos` / `fixture` / `fixtures` / `mock` / `mocks` / `stub` / `stubs` / `template` / `templates` / `scaffold` / `boilerplate` / `doc` / `docs` / `dist` / `build` / `out` / `node_modules` / `vendor` / `third_party`（`SKILL_EXCLUDE_DIRS`，最后一段是技能目录名故豁免）。
- **隐藏目录（`.` 开头）**：默认排除；`.claude/skills/…`、`.agents/skills/…`、`.skills/…` 等公开技能标准位置豁免；`.github` 恒不豁免。

### S3 非公开 frontmatter（`github::is_nonpublic_frontmatter`）

解析 SKILL.md 的 YAML frontmatter，以下情况丢弃：

- `public: false`；
- 以下字段为真值（`true` / `yes` / `1` 等）：`deprecated` / `hidden` / `private` / `internal` / `obsolete`（`HIDDEN_FRONTMATTER_MARKERS`）。

反例（均保留）：`hidden: false`、`public: true`、无相关字段、frontmatter 缺失或解析失败。

---

## 三、索引合并级过滤（`index::run_index`）

前两步产物按 `(source, skillId)` 连接（`skillId` 从 `path` 末段目录名推导），以扫描结果为基准。

### I1 孤儿技能（仓库有、榜单无）→ 保留

`(source, 目录名)` 不在 skills.sh 榜单 → 仍写入 index.jsonl，`installs` 置 `0`、`weeklyInstalls` 置 `[]`，追加在末尾（有榜单数据的按排名在前）。计数：`scan_only`。

### I2 榜单失配（榜单有、仓库无）→ 剔除

榜单收录但仓库扫描找不到（已删除/改名/移走）→ 剔除，保证每个技能都有真实仓库路径背书。计数：`not_in_repo`。

### I3 跨仓库技能去重（`index::_dedup_skills`）

`skillId` 且 `description`（非空）完全一致 → 视为同一技能的镜像/拷贝，组内只保留 `installs` 最高者（相同则保留排名靠前者）。`description` 为空不参与去重。计数：`deduped_skills`。

---

## 四、配置项（`src/skills_index/config.py`）

| 常量 | 默认值 | 作用 |
| --- | --- | --- |
| `MAX_SKILL_COUNT` | `500` | F4 上限；`--max-skill-count N` 覆盖，`0` 关闭 |
| `SKILL_EXCLUDE_DIRS` | 结构词集合 | S2 中间目录段排除词 |
| `SKILL_EXCLUDE_ANY_DIRS` | 状态词集合 | S2 任意段排除词（含技能名） |
| `HIDDEN_FRONTMATTER_MARKERS` | 状态词元组 | S3 frontmatter 非公开标记 |
| `SCHEMA_VERSION` | `4` | 过滤规则变更时递增 → 触发存量缓存一次性全量重扫 |

> 修改任何过滤规则后应递增 `SCHEMA_VERSION`：增量模式下旧缓存按旧规则生成，只有版本号变化才会强制重建。

## 五、观测：run-summary 计数对照

每次 `update` 后 `data/run-summary.md` 的字段与规则的对应：

| run-summary 字段 | 对应过滤 |
| --- | --- |
| `dropped_non_github` | F2 |
| `repos_gone` | F3 |
| `repos_filtered` / `repos_filtered_high_skill` | F4 |
| `repos_deduped` | F5（命中时才显示） |
| `skills_filtered_nonpublic` | S2 + S3（本次实际解析 tarball 的量） |
| `scan_only` | I1（保留计数，非过滤量） |
| `not_in_repo` | I2 |
| `deduped_skills` | I3（命中时才显示） |

Scan 汇总行的 breakdown check `skipped + updated + failed + gone + filtered == repos_total`（✓/⚠）用于校验仓库级过滤未漏计；`repos_deduped` 是 `skipped`/`updated` 的事后细分，不参与该恒等式。
