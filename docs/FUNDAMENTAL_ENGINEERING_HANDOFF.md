# 基本面评估工程 Agent 交接

## 1. 交接目标

本文件用于让新的 Codex 任务或 Agent（智能体）在同一个 Fourseasquant 仓库中定位现状，并开始基本面评估模块的需求访谈与工程设计。

当前只确认工程边界、已有衔接接口和首阶段产物。数据来源、评分维度、权重、证据等级、更新频率等实施决策尚未确认，新任务必须先调用 `$grill-me`（逐项追问）与用户完成需求访谈，不得根据本文件自行补齐。

## 2. 给新任务的第一条指令

可把以下内容原样发送给新的 Codex 任务：

```text
请在同一个 Fourseasquant 项目中开展基本面评估模块工作。

本机仓库：
/Users/lz666/Documents/quant/fourseasquant-t01

GitHub 仓库：
git@github.com:zongeli65-lang/Fourseasquant.git

已完成技术模块所在分支：
agent/akshare-real-market-data

交接基准提交：
ae072b16af0978ed1f284cb23094134d83f91108

开始前请依次完整阅读：
1. AGENTS.md
2. Mac开发环境_AGENT交接.md
3. docs/FUNDAMENTAL_ENGINEERING_HANDOFF.md
4. docs/TECHNICAL_LEADERSHIP_HANDOFF.md
5. contracts/sector-membership.schema.json
6. contracts/examples/sector-membership.example.json

先检查 git status、当前分支和提交，不得覆盖未提交修改。随后调用 $grill-me 技能向我逐项确认基本面模块的未决需求；一次只问一个问题，并给出推荐答案。不要在访谈完成前擅自确定数据源、评分体系或融合规则。
```

如果新任务打开的是另一个检出目录，应以
`agent/akshare-real-market-data` 分支或上述提交为基线。不得使用
`git reset --hard`（强制重置）等破坏性命令。

## 3. 已确认的产品边界

1. 基本面模块与技术模块位于同一个仓库，但保持独立模块边界。
2. 基本面模块可以读取股票、市场和个股技术结果，但不得改变技术评分算法、参数、版本或历史结果。
3. 股票的行业与概念归属均由基本面模块认定，不由技术策略认定，也不直接照搬第三方软件的板块涨跌幅。
4. 一只股票可以同时属于多个行业或概念。
5. 每条成员关系必须有版本、生效区间、变更原因和证据引用。
6. 首阶段需要同时考虑两类输出：
   - 提供给板块技术选举使用的成员关系；
   - 独立的个股基本面评分，为未来综合龙头融合层预留接口。
7. 财报、公司与行业研究，以及财经软件中的公司简报、快报，是基本面判断的重要材料。
8. 结构化结果写入现有 `data/fourseasquant.db`；原始财报、研报、简报等文件保存在单独的本机数据目录，数据库只记录路径、来源、日期、内容哈希和解析状态。
9. 原始资料、本机数据库、访问令牌和其他秘密不得提交到 GitHub。
10. 具体数据来源和基本面评分实施方案由新任务通过 `$grill-me` 继续向用户确认。

## 4. 四层职责

| 层 | 所有者 | 输入 | 输出 |
|---|---|---|---|
| 个股技术评分 | 技术模块 | 三市场量价与指数数据 | 不依赖板块的日频技术分 |
| 基本面板块与评分 | 本次新模块 | 财报、行研、简报、快报等证据 | 成员关系版本、独立基本面结果 |
| 板块技术选举 | 技术模块 | 成员关系、技术分、策略趋势 | 每个板块的技术龙头与前五候选 |
| 综合龙头融合 | 未来模块 | 技术结果与基本面结果 | 正式综合龙头 |

市场趋势由核心策略提供。基本面模块不得制造、推断或覆盖策略趋势；技术模块也不得替代基本面模块决定板块成员关系。

## 5. 股票范围与标识

- 技术候选范围已覆盖沪深主板、创业板和科创板。
- 股票代码使用六位字符串，例如 `600001`。
- 正式行情与技术结果已排除不合格证券；基本面模块仍需明确记录自身适用范围和缺失原因，不能把“没有资料”伪装成负面结论。
- 同一股票可关联多个 `sector_id`（板块标识）；行业和概念的分类体系、标识命名规则尚待确认。

## 6. 已存在的成员关系契约

机器校验规范：

```text
contracts/sector-membership.schema.json
```

示例：

```text
contracts/examples/sector-membership.example.json
```

Python（编程语言）模型位于
`backend/fourseasquant/sector_leadership.py`：

### `SectorMembership`

| 字段 | 含义 |
|---|---|
| `sector_id` | 稳定的板块标识 |
| `sector_name` | 板块名称 |
| `code` | 六位股票代码 |
| `effective_from` | 关系生效日期 |
| `effective_to` | 可选的关系失效日期 |
| `is_active` | 是否有效 |
| `change_reason` | 新增、修订或撤销原因 |
| `evidence_refs` | 可追溯的证据引用列表 |

### `SectorMembershipSnapshot`

| 字段 | 含义 |
|---|---|
| `membership_version` | 成员关系版本 |
| `fundamental_version` | 产生该结果的基本面版本 |
| `effective_date` | 快照生效日期 |
| `published_at` | 发布时间，包含时区 |
| `complete` | 本批次是否完整 |
| `memberships` | 成员关系列表 |

现有模型会拒绝 `complete=false` 的残缺快照，并拒绝同一
`(sector_id, code, effective_from)` 的重复关系。

现有公开只读接口：

```text
GET /api/contracts/sector-membership
```

它只返回契约定义。目前没有正式的成员关系写入接口。是否增加内部
`POST`（提交）接口、命令行导入器或后台流水线，应在需求访谈后决定。

## 7. 已存在的技术衔接接口

技术结果只读接口：

```text
GET /api/technical-scores/status
GET /api/technical-scores/top?target_date=YYYY-MM-DD&limit=20
```

主要 Python 函数：

```text
technical_scoring.read_technical_score_status
technical_scoring.read_top_technical_scores
technical_scoring.score_technical_history

sector_leadership.save_membership_snapshot
sector_leadership.read_latest_membership_snapshot
sector_leadership.elect_sector_leaders
sector_leadership.publish_election_results
```

技术算法定义、参数与验证细节见
`docs/TECHNICAL_LEADERSHIP_HANDOFF.md`。基本面模块只应通过稳定接口读取技术结果，不应直接修改：

```text
backend/fourseasquant/technical_scoring.py
technical_score_versions
technical_daily_scores
technical_score_publications
```

## 8. 当前数据库衔接点

默认数据库：

```text
data/fourseasquant.db
```

数据库由 Git 忽略，不上传 GitHub。当前相关表：

| 表 | 用途 |
|---|---|
| `technical_score_versions` | 技术算法及参数版本 |
| `technical_daily_scores` | 每只股票的永久日频技术分 |
| `technical_score_publications` | 当前有效技术发布 |
| `sector_membership_snapshots` | 完整成员关系快照 |
| `sector_leader_election_results` | 板块技术选举结果 |

独立基本面评分的正式数据表和数据契约尚不存在。新任务应先完成访谈，再通过数据库迁移新增，不能把基本面字段塞入技术评分表。

建议保留以下工程原则，具体字段在访谈后确定：

- 每个结果都能定位算法或规则版本；
- 每个结论都能定位有效日期和证据；
- 修订追加新版本，旧结果永久保留；
- 计算批次与发布批次分离；
- 只有完整且通过校验的批次可以成为网站当前版本。

## 9. 原始证据的本机存储

已确认采用“文件与结构化结果分离”：

- 原始财报、研报、简报、快报保存在仓库外或 Git 忽略的本机数据目录；
- SQLite（本地数据库）仅保存文件路径、来源、发布日期或报告期、内容哈希、解析状态和结构化结论；
- 同一原始文件以内容哈希去重；
- 原文修订不能静默覆盖旧文件；
- 日志不得包含账号、密码、访问令牌、付费资料全文或其他秘密；
- 未明确授权前，不得把受版权或许可限制的原始资料上传 GitHub。

原始资料目录、备份方式和保留规则尚未确认，应由新任务询问用户。

## 10. 完整性与失败语义

必须沿用项目已有的原子发布原则：

1. 新批次先暂存、校验，再发布。
2. 任一来源、解析、关联、评分或校验阶段失败时，不得用残缺结果覆盖上一版。
3. 网站继续读取最近一次完整成功版本，并显示本次失败阶段和时间。
4. 当天成员关系更新失败时，技术选举可沿用最近一次完整版本，但必须标记 `membership_stale=true`（成员关系已陈旧）。
5. 从未存在完整成员关系快照时，不得生成模拟板块龙头、伪造板块排行或用第三方板块指数代替。
6. 对证据不足、数据缺失和无法判断，应使用明确状态表达，不得默认为零分或负面结论。

## 11. 新任务必须继续追问的事项

以下问题故意保持未决。新任务应使用 `$grill-me` 一次只问一个问题，并给出推荐答案；不要一次抛出整张问卷。

1. 第一阶段允许使用哪些数据来源，优先级、许可范围和登录方式是什么。
2. 用户会提供哪些本机文件，文件格式、更新方式和历史覆盖范围是什么。
3. 行业与概念使用什么分类体系，`sector_id` 如何保持稳定。
4. 如何定义“属于某板块”，证据达到什么标准才可正式生效。
5. 财报、行研、公司简报和快报之间如何定证据等级；冲突时如何裁决。
6. 独立基本面评分包含哪些维度、分值、权重和缺失值处理。
7. 财报期间、公告日、追溯调整和历史重算如何处理，避免使用未来信息。
8. 自动更新频率、人工复核流程和正式发布门槛是什么。
9. 结论置信度、待确认状态和撤销机制如何表达。
10. 网站第一阶段需要显示哪些基本面证据与评分；哪些只保留在后台。
11. 基本面结果如何为未来融合层提供稳定接口，但不提前决定融合权重。

## 12. 建议的首阶段交付顺序

以下是工程顺序，不代表替用户决定业务规则：

1. 运行 `$grill-me`，形成用户确认的基本面需求规格。
2. 盘点可用数据和许可边界，制作少量真实样本。
3. 固化板块分类、成员关系证据和版本规则。
4. 为独立基本面评分设计新契约、数据库迁移和只读接口。
5. 建立原始证据登记、解析、校验、暂存和原子发布流水线。
6. 先发布一批完整成员关系，再调用现有技术选举进行历史回放。
7. 在网站中展示来源、有效日期、版本、陈旧状态和失败状态。
8. 通过测试和人工抽查后，再扩大股票与历史范围。

## 13. 开工与验证命令

先核验工作区：

```zsh
cd /Users/lz666/Documents/quant/fourseasquant-t01
git status --short --branch
git branch --show-current
git rev-parse HEAD
```

安装和测试依赖必须留在项目目录，不写入 macOS 系统 Python：

```zsh
uv sync
npm install
npm run typecheck
npm test
```

需要检查现有接口时：

```zsh
npm start
```

网站地址：

```text
http://127.0.0.1:8000
```

所有命令均应先查看仓库脚本和当前环境；版本、服务状态与 GitHub 登录状态属于易变化事实，不能只相信旧交接记录。

## 14. 当前基线状态

截至交接基准提交：

- 正式行情窗口：2025-07-24 至 2026-07-23；
- 预热行情起点：2025-04-25；
- 有合格历史的证券：4,982 只；
- 永久技术日频结果：1,193,306 条；
- 技术算法版本：`technical-v1`；
- 五个指数各保留 302 个交易日；
- 当前页面只展示真实个股技术分，在缺少基本面成员关系时不会生成模拟板块技术龙头。

这些数字是交接时快照，新任务开始时应重新查询，不得当作永久常量。

## 15. 验收底线

基本面首阶段至少应满足：

- 不修改或覆盖任何技术算法、参数和结果；
- 成员关系可多重归属、可追溯、可按日期重放；
- 独立基本面结果使用独立契约和独立表；
- 任一结论都能定位版本、有效期和证据；
- 残缺批次不会成为当前版本；
- 失败后仍可读取最近一次完整成功结果；
- 历史修订保留旧版本，不发生静默覆盖；
- 原始资料、数据库和秘密不进入 Git；
- 没有完整数据时明确显示等待或失败，不生成模拟结论；
- 新增代码通过类型检查、自动化测试和真实样本人工核验。
