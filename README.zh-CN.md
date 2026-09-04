# Skills Index

[English](README.md) | 简体中文

[skills.sh](https://skills.sh) 技能的合并索引,发布为一个 JSONL 文件。skills.sh 决定**存在哪些**技能、它们有多流行;本项目从每个技能的 GitHub 仓库补齐它不暴露的部分:`path`、`description`、`lastCommitAt`、`stars`。消费方无需接触 GitHub。

## 数据

`index.jsonl` —— 每行一个技能:

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

| 字段             | 来源      | 含义                                                                                           |
| ---------------- | --------- | ---------------------------------------------------------------------------------------------- |
| `skillId`        | skills.sh | 技能标识(技能目录名)                                                                           |
| `source`         | skills.sh | GitHub 仓库,`owner/repo`                                                                       |
| `stars`          | GitHub    | 所在仓库 star 数(仓库级)                                                                       |
| `installs`       | skills.sh | 累计安装量                                                                                     |
| `weeklyInstalls` | skills.sh | 近 8 周安装量,按时间顺序                                                                       |
| `path`           | GitHub    | 技能目录,相对仓库根                                                                            |
| `description`    | GitHub    | `SKILL.md` frontmatter 的 `description`(可为空)                                                |
| `lastCommitAt`   | GitHub    | 最近一次触及该技能目录的 commit 的提交者时间(UTC)—— 即技能真实的最近更新时间,直接取自 git 历史 |

- **判断更新** —— 把 `lastCommitAt` 当版本日期展示;技能内容一变它必然前进。查看改了什么:`https://github.com/<source>/commits/HEAD/<path>`。
- **无 `url` 字段** —— 用 `source` + `path` 拼出 `https://github.com/<source>/tree/HEAD/<path>`。
- **收录** —— 每条记录都是 skills.sh 收录且其仓库确认存在的技能;收录与否仅由 skills.sh 决定。

## 获取

Release 以 `data-YYYYMMDDThhmmssZ` 打 tag —— 生产环境固定 tag,`releases/latest` 适合快速查看:

```bash
curl -LO https://github.com/skill-one/skills-index/releases/download/data-20260904T032842Z/index.jsonl   # 固定 tag
curl -LO https://github.com/skill-one/skills-index/releases/latest/download/index.jsonl                  # 最新
```

同一 tag,支持 CORS 的 CDN 副本:

```
https://cdn.jsdelivr.net/gh/skill-one/skills-index@data-20260904T032842Z/index.jsonl
```

| 资产              | 说明                                                     |
| ----------------- | -------------------------------------------------------- |
| `index.jsonl`     | 产品本体                                                 |
| `index-meta.json` | `formatVersion` / `generatedAt` / `counts.total` / `tag` |

运行报告就是 Release body 本身 —— 不另设资产。

## 许可

代码:[MIT](LICENSE)。发布数据:[CC0 1.0](LICENSE-DATA)(公有领域)。索引中的每个技能仍受其上游仓库自身许可约束。

## 面向开发者

管线、规则、契约、CI:见 [docs/DEVELOPMENT.zh-CN.md](docs/DEVELOPMENT.zh-CN.md)。
