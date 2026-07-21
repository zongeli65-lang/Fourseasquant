# 问题追踪：GitHub

本仓库的问题和产品需求文档使用 GitHub Issues 管理。所有操作使用 `gh` 命令行工具，并由仓库的 Git 远程地址自动识别 `zongeli65-lang/Fourseasquant`。

## 常用操作

- 创建问题：`gh issue create --title "..." --body "..."`
- 查看问题：`gh issue view <编号> --comments`
- 列出问题：`gh issue list --state open`
- 评论问题：`gh issue comment <编号> --body "..."`
- 添加标签：`gh issue edit <编号> --add-label "..."`
- 删除标签：`gh issue edit <编号> --remove-label "..."`
- 关闭问题：`gh issue close <编号> --comment "..."`

多行问题正文应使用 heredoc（多行输入重定向）。读取问题时同时获取正文、标签与评论。

## 拉取请求是否作为需求入口

否。

外部拉取请求不进入问题分流队列。若以后希望把外部拉取请求视为功能请求，可把本项改为“是”。

## 技能操作约定

- 当技能要求“发布到问题追踪器”时，创建 GitHub Issue。
- 当技能要求“读取相关任务”时，运行 `gh issue view <编号> --comments`。
- 问题和拉取请求共享编号；编号类型不明确时，先尝试 `gh pr view <编号>`，失败后再使用 `gh issue view <编号>`。

## Wayfinder 决策探索约定

- 决策地图使用一个带 `wayfinder:map` 标签的 GitHub Issue。
- 子任务优先使用 GitHub 子问题；不可用时，使用任务清单并在子问题顶部写明所属地图。
- 子任务标签使用 `wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling` 或 `wayfinder:task`。
- 阻塞关系优先使用 GitHub 原生问题依赖；不可用时，在正文顶部记录 `Blocked by: #<编号>`。
- 领取任务时将问题分配给当前用户。
- 完成决策后，在问题中记录答案、关闭问题，并把结论链接追加到地图。
