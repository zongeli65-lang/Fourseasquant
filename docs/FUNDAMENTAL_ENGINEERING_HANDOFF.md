# 个人基本面模块工程交接

## 1. 当前结果

第一批机械化内核已经落地，规则见[个人基本面机械规则](FUNDAMENTAL_MECHANICAL_RULES.md)，书本对应关系见[彼得·林奇规则追溯](FUNDAMENTAL_LYNCH_TRACEABILITY.md)。

已实现：

- 月度林奇核心数据计算；
- 主营业务毛利润贡献识别；
- 流通市值金额计算；全市场百分位等待完整横截面；
- 内部人士、注销回购和稀释的真金白银信号分；
- 独立“舆论监测”模块的 DeepSeek V4 Pro 五分类、点赞对数加权和平台独立汇总；
- 策略板块每日最多发布 10 只有序舆论目标，新浪股吧只对这些目标自动采集；
- 重点监测列表、三请求分片、后端整批低频轮转、任务心跳和进度可视化；
- 舆论批次不再由浏览器逐只派发，刷新或关闭页面不会中断；
- 无法稳定完成的受限来源采集窗口及接口已移除；
- 只按帖子编号进行技术去重，不进行内容去重；
- 财报网址与内容哈希复用；
- 月度快照只保存变化字段；
- 舆论内容引用不保存正文，只保存网址、摘要哈希、模型分类、置信度和版本；
- AKShare 新浪行业、概念板块及成分股的完整快照导入；
- `140 + 40 + 20`、最多 200 只的可复现基本面候选池；
- 巨潮高管交易、股本变化和回购注销证据的标准化、十二个月滚动聚合；
- 资本行为批次原子发布：失败批次保留诊断，但不会覆盖上一版完整结果；
- 月度基本面独立批次发布：未变化股票不写空快照，批次仍可证明完整覆盖；
- 巨潮官方回购注销公告 PDF 机械解析，金额与股数均核验后才计分；
- 官方 PDF 按内容哈希永久归档，单份解析失败降级为待核验证据；
- 每日资本行为、月末基本面快照联合定时任务及网站内独立重试；
- 定时任务与网站重试按交易日跨进程互斥；
- 资本行为状态、逐股证据 API（应用程序编程接口）和前端证据链；
- 舆论分类按内容哈希、模型和提示词版本复用；分类批次失败时不推进采集游标；
- 舆论按用户指定窗口每股最多 300 条、每主题最多 50 条回复并优先最新内容；
  达上限可发布但必须标记截断，DeepSeek 汇总不混入旧机械分类；
- 热榜和重点监测不再生成自动任务；手动单股调查保持独立；
- 数据库版本升级到 32；行情与资本行为定时任务使用独立、幂等的时槽登记。

## 2. 代码位置

| 文件 | 职责 |
|---|---|
| `backend/fourseasquant/fundamental_mechanical.py` | 月度四支柱计算 |
| `backend/fourseasquant/public_opinion.py` | 新舆论分类、点赞权重、完整性与最少样本门禁 |
| `backend/fourseasquant/public_opinion_deepseek.py` | DeepSeek V4 Pro 批量五分类、响应校验与重试 |
| `backend/fourseasquant/public_opinion_receivers.py` | 公开页面解析、限速、429/403 暂停和熔断 |
| `backend/fourseasquant/public_opinion_repository.py` | 重点监测、任务、内容引用和平台日汇总 |
| `backend/fourseasquant/public_opinion_worker.py` | 后端持久轮转、停止控制、进度状态和中断任务恢复 |
| `backend/fourseasquant/public_opinion_api.py` | 舆论总览、自动新浪任务、重点监测、批次控制和单任务执行接口 |
| `backend/fourseasquant/fundamental_discovery.py` | 东方财富、新浪板块与成分股标准化 |
| `backend/fourseasquant/fundamental_repository.py` | 证据、变化快照、讨论引用和板块候选快照保存 |
| `backend/fourseasquant/fundamental_capital_actions.py` | 资本行为事件标准化、时间截面过滤与十二个月聚合 |
| `backend/fourseasquant/cninfo_announcement.py` | 巨潮公告详情解析、官方 PDF 文本提取与金额/股数机械识别 |
| `backend/fourseasquant/fundamental_candidates.py` | 生成 `140 + 40 + 20` 候选池 |
| `backend/fourseasquant/fundamental_automation.py` | 资本行为重试、月末判断与失败批次登记 |
| `backend/fourseasquant/scheduled_pipeline.py` | 行情、策略、基本面联合定时入口 |
| `scripts/calculate_fundamental_monthly.py` | 月度结构化输入命令行入口 |
| `scripts/aggregate_discussion_day.py` | 每日讨论结构化输入命令行入口 |
| `scripts/import_fundamental_boards.py` | AKShare 新浪板块实时导入，并按最新正式行情股票集合过滤 |
| `scripts/import_fundamental_capital_actions.py` | 采集并原子发布真实资本行为批次 |
| `scripts/import_fundamental_monthly_akshare.py` | 生成最多 200 只的真实月度基本面快照 |
| `scripts/initialize_fundamental_data.py` | 首次初始化资本行为与月度快照 |

## 3. 调用方法

导入新浪行业、概念板块和成分股：

```zsh
npm run data:fundamental-boards
```

初始化完整资本行为与月度快照：

```zsh
npm run data:fundamental-init
```

只更新资本行为：

```zsh
npm run data:fundamental-capital
```

根据结构化真实财务输入计算月度结果：

```zsh
npm run fundamental:monthly -- /绝对路径/monthly-input.json
```

聚合单个平台、单只股票、单日讨论：

```zsh
npm run fundamental:sentiment -- /绝对路径/discussion-input.json
```

运行基本面单项测试：

```zsh
uv run pytest -q \
  tests/test_fundamental_mechanical.py \
  tests/test_discussion_sentiment.py \
  tests/test_fundamental_repository.py \
  tests/test_fundamental_discovery.py
```

## 4. 数据库表

| 表 | 内容 |
|---|---|
| `fundamental_parsed_evidence` | 来源网址、内容哈希和结构化结果 |
| `personal_fundamental_monthly_snapshots` | 个人基本面月度变化字段和来源网址 |
| `personal_fundamental_monthly_batches` | 月度预期/完成股票、规则版本和原子发布状态 |
| `discussion_daily_aggregates` | 平台每日汇总 |
| `discussion_post_references` | 最近三十天单帖引用，不含正文 |
| `public_opinion_watchlist` | 用户明确加入或停止的重点监测股票 |
| `public_opinion_collection_jobs` | 自动和手动采集任务、日期分段、游标与状态 |
| `strategy_opinion_target_snapshots` | 策略发布的每日有序舆论调查代码，最多 10 只 |
| `public_opinion_content_references` | 不含正文的舆论引用、分类和证据哈希 |
| `public_opinion_daily_aggregates` | 各平台独立日汇总，不含跨平台总分 |
| `fundamental_board_candidate_snapshots` | 完整板块候选及成分股快照 |
| `capital_action_batches` | 每次资本采集的覆盖范围、成功/失败状态、时间和错误 |
| `capital_action_events` | 规范化事件、原始结构化载荷、来源和内容哈希 |
| `capital_action_publications` | 每个日期最后一次完整批次的原子发布指针 |
| `fundamental_update_attempts` | 月末基本面成功/失败阶段、时间与错误摘要 |
| `fundamental_update_claims` | 定时任务和网站重试的跨进程日期租约 |

## 5. 尚未实现

- 新浪股吧已作为自动舆论主源；东方财富只负责热榜发现；
- 同花顺不再作为自动评论来源，除非未来出现可验证的免登录完整接口；
- 受限来源不提供采集入口；未来只有取得正式授权接口后才重新评估；
- 同花顺板块成分股采集器：当前项目 AKShare 1.18.70 只有同花顺板块目录和简介，没有完整成分股接口；
- 东方财富与同花顺热门股票榜的统一候选池；
- 由基本面研究模块提供的版本化行业/概念成员关系；该数据到位前不编造同行百分位。
- 全部正常交易 A 股的历史流通市值横截面；接入前候选股流通市值百分位保持空值，不能用 200 只候选冒充全市场。

真实东方财富板块抽查在 2026-07-24 被本机当前网络代理断开，因此正式初始化改用同属 AKShare 的新浪行业和概念接口。2026-07-24 已实测全量导入 259 个候选板块、14,305 条有效成员关系和 4,972 只唯一股票；所有成员都属于当日 4,978 只正式沪深主板、创业板或科创板股票集合。页面默认读取最新可用候选池来源。

这些缺口不能用模拟数据冒充。资本行为和月度采集已经接入真实
AKShare（A 股数据接口库）入口，但正式使用前仍应运行初始化命令并检查
完整批次覆盖率。

巨潮高管增减持接口的时间窗口相对采集运行日，不能证明任意历史目标日前
十二个月的完整性。因此资本行为发布命令只允许目标日期等于数据库最新正式
行情日；历史策略补算不会把不完整资本行为冒充完整输入。未来如要补算历史
资本行为，必须换成可明确传入起止日期的官方来源。

采集运行日若晚于目标日，高管增减持项保持未知且不计分，对应金额、比例和
调整分均保存为 `null`（空值），前端显示“无法确定”，不能显示为零。资本事件 API 只
发布目标日前十二个月；更早股本记录仅作为稀释基线。月度发布门禁按
`as_of_date + rules_version`（目标日期＋规则版本）共同判断，规则升级不会
误用旧版本完成状态。

## 6. 安全和版权

- DeepSeek 密钥可由舆论页面保存到当前用户的 macOS 钥匙串，后端重启后
  自动恢复；也可通过环境变量 `DEEPSEEK_API_KEY` 提供。接口不回传密钥，
  且密钥不进入数据库、浏览器存储、日志、聊天或提交；
- 不读取或提交登录凭据和浏览器会话；
- 不绕过登录、验证码或反爬限制；
- 不保存讨论正文和完整财报正文；
- 不把第三方板块、热度或舆情写成基本面事实；
- 舆论分类会调用固定的 DeepSeek API；基本面机械结果和其他模块不因此调用 Agent。
