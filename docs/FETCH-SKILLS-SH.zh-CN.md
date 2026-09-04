# 从 skills.sh 获取全部技能

[English](FETCH-SKILLS-SH.md) | 简体中文

依据本文可从零复现 fetch 步骤 —— 全程不访问 GitHub。

## API

```
GET https://skills.sh/api/skills/all-time/{page}
```

- `page` 从 **0** 开始;响应体为 `{"skills": [...], "hasMore": bool, "total": int, "page": int}`。
- `hasMore` 为 true 则继续翻页。无需认证。每页间隔 0.3 s;失败页跳过并记录,连续 3 页失败中止。

## 字段(原样保留)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `source` | `string` | GitHub 仓库,形如 `owner/repo` |
| `skillId` | `string` | 技能标识(技能目录名) |
| `installs` | `int` | 累计安装量 |
| `weeklyInstalls` | `int[]` | 近 8 周安装量,按时间顺序 |
| `name` | `string` | 技能显示名(目前与 `skillId` 相同) |
| `isOfficial` | `boolean` | 官方技能标记(仅官方技能携带) |

> 过滤:仅保留 `source` 形如 `owner/repo`(含 `/`、非完整 URL)的记录。

## 输出与样例

不落盘:`run_fetch` 在内存中返回记录,`update` 直接把它交给 scan 步骤,scan 自行按 `source` 分组。每条记录字段原样保留,例如:

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
