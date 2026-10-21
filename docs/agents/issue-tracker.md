# Issue tracker: GitHub

本项目的规格和任务发布到 GitHub Issues：LeoAKALiu/field-inspect。
本地规格副本用于审阅和交接；发布后记录对应 Issue URL，任务状态以 GitHub 为准。

## 操作约定

- 优先使用支持相应操作的 GitHub 连接器；需要使用 gh 时，显式指定 --repo LeoAKALiu/field-inspect。
- 创建、读取、列出、更新、评论、标记和关闭任务均操作该仓库的 Issue。
- 多行正文使用连接器结构化参数或 gh 的 --body-file，保留真实换行，不把正文插入 shell 命令。
- 创建前检查是否已有同一规格或任务，已有条目按当前内容更新，避免重复发布。
- 技能要求“publish to the issue tracker”时，创建或更新 GitHub Issue；要求“fetch the relevant ticket”时，读取指定 Issue 及必要讨论。
- 状态标签使用 triage-labels.md 的映射。to-spec 完成确认后直接使用 ready-for-agent，无需再经过 triage。
- 一个规格可以作为父 Issue；实现任务各自为独立子 Issue，正文说明所属规格。
- 阻塞关系使用 GitHub 原生 Issue dependencies。写入依赖时区分 Issue 编号与数据库 ID，不把编号当数据库 ID。
- 原生子任务或依赖接口不可用时，在正文保留 Part of #N、Blocked by: #N，并在父 Issue 维护任务清单。
- 领取任务前读取其阻塞项；仅在所有阻塞项完成后推进。wayfinder 的决定任务也使用同一父子和阻塞关系。

## Pull requests as a triage surface

PRs as a request surface: no.

## 公开边界

该仓库为公开仓库。Issue 只包含可公开的技术需求、决定和结果；客户资料、私有标定、原始巡检包、凭据、本机私有目录及未核对授权资产保持私有。
发布技术规格不等于获准推送私有 Git 历史或部署服务。执行时继续遵守 AGENTS.md 与 PROGRESS.md 记录的用户约束和阶段授权。
