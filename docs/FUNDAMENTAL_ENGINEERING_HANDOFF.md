# 个人基本面模块工程交接

## 1. 当前结果

第一批机械化内核已经落地，规则见[个人基本面机械规则](FUNDAMENTAL_MECHANICAL_RULES.md)，书本对应关系见[彼得·林奇规则追溯](FUNDAMENTAL_LYNCH_TRACEABILITY.md)。

已实现：

- 月度林奇核心数据计算；
- 主营业务毛利润贡献识别；
- 流通市值全市场百分位；
- 内部人士、注销回购和稀释的真金白银信号分；
- 东方财富股吧、雪球讨论的固定词典情绪、点赞对数加权和平台合并；
- 只按帖子编号进行技术去重，不进行内容去重；
- 财报网址与内容哈希复用；
- 月度快照只保存变化字段；
- 单帖引用不保存正文，并自动清理三十天以前的记录；
- 东方财富行业、概念板块及成分股的完整快照导入；
- 数据库版本升级到 12。

## 2. 代码位置

| 文件 | 职责 |
|---|---|
| `backend/fourseasquant/fundamental_mechanical.py` | 月度四支柱计算 |
| `backend/fourseasquant/discussion_sentiment.py` | 每日讨论分类、热度和平台合并 |
| `backend/fourseasquant/fundamental_discovery.py` | 东方财富板块与成分股标准化 |
| `backend/fourseasquant/fundamental_repository.py` | 证据、变化快照、讨论引用和板块快照保存 |
| `scripts/calculate_fundamental_monthly.py` | 月度结构化输入命令行入口 |
| `scripts/aggregate_discussion_day.py` | 每日讨论结构化输入命令行入口 |
| `scripts/import_fundamental_boards.py` | AKShare 东方财富板块实时导入 |

## 3. 调用方法

导入东方财富板块和成分股：

```zsh
npm run data:fundamental-boards
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
| `fundamental_monthly_snapshots` | 月度变化字段和来源网址 |
| `discussion_daily_aggregates` | 平台每日汇总 |
| `discussion_post_references` | 最近三十天单帖引用，不含正文 |
| `fundamental_board_snapshots` | 完整板块及成分股快照 |

## 5. 尚未实现

- 巨潮财报字段到月度输入模型的实时采集器；
- 东方财富股吧和雪球逐帖公开内容采集器；
- 同花顺板块成分股采集器：当前项目 AKShare 1.18.70 只有同花顺板块目录和简介，没有成分股接口；
- 东方财富与同花顺热门股票榜的统一候选池；
- 月末定时任务和每日舆情定时任务；
- 网页展示。

这些缺口不能用模拟数据冒充。下一阶段应优先完成巨潮结构化输入适配器和公开讨论采集器，再接定时任务。

## 6. 安全和版权

- 不读取或提交密钥、登录凭据和浏览器会话；
- 不绕过登录、验证码或反爬限制；
- 不保存讨论正文和完整财报正文；
- 不把第三方板块、热度或舆情写成基本面事实；
- 不自动调用 Agent。
