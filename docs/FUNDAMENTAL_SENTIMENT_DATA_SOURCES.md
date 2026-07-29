# 舆情数据接收器数据源核查

## 1. 核查日期与结论

- 核查日期：2026-07-25
- 目标：为个人基本面第四支柱寻找可以每天自动接收的股票热度与用户讨论情绪数据。
- 当前结论：热度聚合数据可以先接入；帖子级利好／利空数据尚无已经确认可长期自动采集的免费授权来源。

旧的东方财富与雪球 50/50 综合模型已经移除。新工程按平台分别保存机械方向，不生成跨平台总分；东方财富公开列表和详情页已经完成无登录真实结构验证。

## 2. 数据需求

### 2.1 热度最低输入

- 股票代码；
- 统计日期或时间；
- 讨论量、关注指数或平台排名；
- 数据源和采集时间。

### 2.2 情绪方向最低输入

- 平台帖子唯一编号；
- 股票代码；
- 发布时间；
- 帖子标题或正文；
- 原创／转载标志；
- 点赞数；
- 原帖网址。

如果没有帖子文本或平台提供的授权情绪结果，只能计算热度，不能生成利好／利空方向。

## 3. 数据源核查

### 3.1 雪球

雪球网页能够展示股票讨论、发布时间、转发数、评论数和点赞数，但不能据此认定允许自动采集。

雪球现行服务协议明确禁止未经授权使用网络爬虫、抓取工具访问、抓取、存储、缓存、检索或索引雪球网站或服务内容；协议还专门限制将雪球内容用于 AI 系统。雪球 `robots.txt` 同时禁止通用爬虫访问 `/v5/stock/`、`/stock/`、`/query/` 等路径。

结论：

- 直接编写雪球帖子爬虫：不进入正式工程方案；
- AKShare 中存在雪球关注和讨论排行榜函数，但其底层访问雪球 `/service/v5/stock/screener/screen`，不能因为经过 AKShare 包装就忽略雪球的授权限制；
- 如未来取得雪球书面许可或正式数据接口，再实现雪球适配器。

一手来源：

- [雪球服务协议](https://xueqiu.com/about/terms)
- [雪球 robots.txt](https://xueqiu.com/robots.txt)
- [AKShare 雪球热度排行源码](https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_hot_xq.py)

### 3.2 东方财富公开聚合数据

AKShare 当前提供以下东方财富公开聚合入口：

- 个股人气排名及历史排名；
- 热门关键词；
- 千股千评中的用户关注指数；
- 市场参与意愿。

这些数据适合构建第一版“东方财富热度接收器”，因为无需保存用户帖子正文。但这些聚合字段不能提供：

- 帖子级原创／转载判断；
- 帖子文本；
- 每帖点赞数；
- 可由本工程复算的利好／利空方向。

东方财富现行用户服务协议覆盖股吧等 WEB 服务，声明平台内容相关知识产权归东方财富或权利人所有，并限制未经许可的使用及非东方财富授权方式。因此，公开网页可以人工浏览不等于已经取得批量帖子采集授权。

结论：

- 东方财富公开聚合指标：可作为热度接收器候选，实施前仍应控制频率、保存来源和失败状态；
- 东方财富股吧帖子抓取：在未取得明确授权前不作为正式情绪接收器；
- 热门关键词不能直接当作利好或利空。

一手来源：

- [东方财富用户服务协议](https://about.eastmoney.com/home/protocol)
- [东方财富法律声明](https://about.eastmoney.com/home/disclaimer)
- [东方财富股吧人气榜](https://guba.eastmoney.com/rank/)
- [东方财富千股千评](https://data.eastmoney.com/stockcomment/)
- [AKShare 东方财富人气榜源码](https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_hot_rank_em.py)
- [AKShare 东方财富千股千评源码](https://github.com/akfamily/akshare/blob/main/akshare/stock_feature/stock_comment_em.py)

### 3.3 东方财富 Choice

Choice 官方资料确认提供量化接口和资讯数据，并支持 macOS 下的 Python、Java 和 C++。历史产品资料也明确展示过基于股吧社区帖的舆情热点功能。

但公开文档尚不能确认量化接口是否向个人授权返回本工程需要的帖子编号、正文、原创标志和点赞数。因此不能先写死接收器字段。

结论：

- 属于优先联系的授权数据源；
- 必须向 Choice 客户经理确认“股吧用户帖子级数据或股票级授权情绪结果”的接口权限、字段、更新频率、费用和落库许可；
- 如果只提供平台计算后的舆情指数，应作为 `vendor_sentiment`（供应商情绪）单独保存，不能冒充本工程的点赞加权帖子情绪。

一手来源：

- [Choice 官方用户指南](https://choice.eastmoney.com/choicewebfile/UserGuide.pdf)
- [Choice 数据学院](https://choice.eastmoney.com/School/ChooseCategoryVideo?category=0&module=13)

### 3.4 同花顺 iFinD

iFinD 官方数据接口支持 Python 和 HTTP 接口，也提供免费账号及正式账号的数据量规则。但其公开网页没有证明免费版或正式版一定包含用户讨论帖子级数据。

结论：

- 可以作为第二授权询价对象；
- 必须在官方“超级命令”或由客户经理确认具体舆情指标、返回字段和许可；
- 在字段未确认前，不得把“资讯”“公告”或“智能选股”当成用户评论舆情。

一手来源：

- [同花顺数据接口基础概念](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/faq.html)
- [同花顺数据接口使用流程](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/deploy.html)
- [同花顺数据接口权限说明](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/permission.html)

### 3.5 Tushare 新闻快讯

Tushare 提供主流新闻网站的新闻快讯数据，但这属于新闻资讯，不是用户评论区或讨论平台。

结论：可作为未来新闻风险模块的数据源，不能进入当前用户舆情支柱。

一手来源：

- [Tushare 新闻快讯接口](https://tushare.pro/document/2?doc_id=143)

## 4. 推荐接收器分层

| 层级 | 数据源 | 可以生成 | 当前状态 |
|---|---|---|---|
| L1 | 东方财富公开人气榜、用户关注指数 | 热度、排名、变化 | 可先做技术验证 |
| L2 | Choice 或 iFinD 授权聚合舆情 | 平台授权情绪或热度 | 需确认字段和费用 |
| L3 | 无需登录且可公开访问的帖子级数据 | 本工程机械有利／不利、点赞加权 | 东方财富主题已验证 |
| 手动 | 雪球公开内容 | 用户指定股票和日期后单独执行 | 不登录、不复用用户账户 |

## 5. 对现有规则的影响

正式决策是可插拔且分平台发布：东方财富、同花顺、雪球各自计算；只有单个平台目标范围完整、且当日至少 10 条有效内容时才发布该平台方向。跨平台只允许显示一致性标签，不允许形成统一数值。
