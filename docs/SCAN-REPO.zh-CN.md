# 在仓库中定位技能 — scan 的原理

[English](SCAN-REPO.md) | 简体中文

skills.sh 就是注册表:哪些技能存在(`source` + `skillId`)由它独家决定。scan 回答它不暴露的部分 —— 每仓库一次 git clone,得到 `path`、`description`、`lastCommitAt`,以及 `stars`(仓库元数据,每轮刷新)。

## 匹配

```
<任意前缀>/<skillId>/SKILL.md     # 唯一能命中的形态
```

- `skillId` 即命中目录的目录名;仓库根的裸 `SKILL.md` 永不命中(没有可命名技能的父目录)。
- 全部候选按字典序排序,**第一个**胜出。确定性,但不权威:仓库存在多个同名目录时,skills.sh 的爬虫可能登记的是另一份 —— 任何静态规则都无法知晓,首匹配即本项目认可的取义。
- 命中的 `SKILL.md`,frontmatter 的 `description` 是什么就发布什么,包括空值。只有"没找到"才移除:`skillId` 一无所获即丢弃;仓库 404 则全部技能退出。

## 克隆与历史

唯一通道:裸仓库部分克隆(`git clone --bare --filter=blob:limit=65536`)—— 体积上限让 SKILL.md 级别的 blob 随克隆就位、排除大资产;git 协议不占 REST 配额(不走 REST 取内容,不用 Trees API)。流程:`ls-tree -r HEAD` 列出候选路径 → 匹配 → 每个命中用 `git log -1 -- <dir>` 取该目录真实的最近提交时间(`lastCommitAt`,归一化为 UTC),用 `git show HEAD:<dir>/SKILL.md` 取 frontmatter。

历史先浅取(`--shallow-since=1.year`),配两道精确性回退:

- 窗口内**一个 commit 都没有**的仓库,浅请求直接失败(`no commits selected`)—— 去掉浅化参数重试一次全历史;失败的浅请求不传任何对象,代价近零;
- 浅边界 commit 被嫁接成"创建了树里一切现存文件"的根 commit,指向它的 `lastCommitAt` 可能是伪造的 —— 只要某行的命中 commit 落在边界上(`.git/shallow`),就对该仓库 `--unshallow` 一次并重算。

无论走哪条路,发布的都是精确的提交者时间;取值直接来自历史,重算恒得同值 —— 无缓存可维护。

## scan 不再做的事

收录决策已交给 skills.sh,整套"发现"机制移除:不发现未收录技能;不要安装器的位置规则(优先目录 / 深度上限 / 隐藏目录);不做内容过滤(内部路径 / frontmatter 有效性 / 非公开标记);不做镜像墓碑与 skillCount 上限;也不算内容指纹(`rev`)—— "内容何时变的"由 git 历史直接回答。每个仓库只贡献 skills.sh 列出的 `skillId`,镜像交给 index 步骤的(`skillId` + `description`)去重。

安装器求**召回**;本项目把收录交给 skills.sh,求**简单**。
