# Fourseasquant 本机自动化维护

Fourseasquant 使用 macOS LaunchAgent（用户登录后的后台启动代理）每分钟唤醒一次轻量检查，再由应用按设置面板保存的北京时间决定是否运行每日任务。默认时间为 16:30，因此系统时区变化不会改变北京时间口径。联合入口先更新 AKShare 股票与五个指数 K 线，计算并暂存全市场个股技术评分，再进入策略、校验和事务发布；随后采集基本面资本行为，月末最后一个交易日再生成月度基本面快照。技术评分、策略快照和资本行为都采用完整性门槛；任何残缺批次都不会覆盖上一版完整结果。

## 首次安装

在项目根目录执行：

```bash
uv sync
uv run python scripts/launch_agent_cli.py install
uv run python scripts/launch_agent_cli.py status
```

安装命令会生成 `~/Library/LaunchAgents/com.fourseasquant.daily.plist`，再通过 `launchctl bootstrap` 启用。当前程序入口为 `fourseasquant.scheduled_pipeline`（联合定时流水线）。LaunchAgent 只负责每分钟唤醒；具体时间每次都从本机数据库读取，因此网站内修改自动更新时间后无需重新安装。由旧版本升级后需要重新执行一次 `install`，才能把本机计划切换到联合入口。

日常打开正式网站可运行：

```bash
npm start
```

正式启动会先让网站就绪，再在后台检查并补跑最近 45 个自然日内最新的缺失交易日。开发热更新命令 `npm run dev` 默认不执行启动补跑，避免每次代码重载产生业务副作用。

本机正式运行副本另安装 `com.fourseasquant.web` 常驻启动代理，用户登录后自动在 `127.0.0.1:8000` 提供网页和控制接口；进程退出时由 macOS 自动恢复。产业链页面固定为：

```text
http://127.0.0.1:8000/industry-chain-leaders
```

网站启动代理维护命令：

```bash
env PYTHONPATH=backend .venv/bin/python scripts/web_launch_agent_cli.py install
env PYTHONPATH=backend .venv/bin/python scripts/web_launch_agent_cli.py status
env PYTHONPATH=backend .venv/bin/python scripts/web_launch_agent_cli.py disable
env PYTHONPATH=backend .venv/bin/python scripts/web_launch_agent_cli.py enable
```

## 日常命令

```bash
# 立即按今天日期运行；仍会跳过非交易日
uv run python scripts/automation_cli.py run-now

# 按配置时间规则检查并运行，供 LaunchAgent 调用
uv run python scripts/automation_cli.py run-scheduled

# 检查并补跑最近一个应发布但缺失的交易日
uv run python scripts/automation_cli.py catch-up

# 查看、停用、重新启用计划
uv run python scripts/launch_agent_cli.py status
uv run python scripts/launch_agent_cli.py disable
uv run python scripts/launch_agent_cli.py enable

# 停用并删除 LaunchAgent 文件
uv run python scripts/launch_agent_cli.py uninstall
```

通过 `npm start` 启动的网站会检查最近缺失交易日并尝试补跑。测试或临时诊断时，可设置 `FOURSEASQUANT_ENABLE_STARTUP_CATCHUP=0` 关闭启动补跑。

## 运行规则

- 仅在内置上海证券交易所交易日历覆盖的 2023—2026 年运行。
- 周末和休市日直接跳过，不创建任务，也不发布伪快照。
- 同一交易日已有成功快照时，定时入口不会重复发布；网站手动运行可显式重算。定时任务与网站重试共用带 60 秒续租心跳的跨进程日期租约，同时只能有一个基本面流水线运行。
- 默认 16:30 首次尝试；数据不完整时每 30 分钟重试一次，固定时段为
  16:30、17:00、17:30、18:00、18:30、19:00、19:30、20:00、
  20:30、21:00、21:30。21:30 仍失败才发送最终失败通知；之后只能在
  网站内手动重试。每个时段最多启动一次，任务运行跨过某个时段时不会
  追补或并发启动；网站手动重试和启动补跑不占用自动时槽。若从设置面板
  修改首次更新时间，后续 30 分钟时槽会整体平移；为避免重试窗口跨日，
  首次更新时间不得晚于 18:59。当前正式设置 16:30 对应最终时槽 21:30。
- 资本行为每日更新；月度基本面只在每月最后一个交易日更新。
- 行情、技术结果和市场环境均成功后，流水线会幂等准备当日唯一核心
  策略的候选、基本面目标和舆论采集任务；策略准备失败独立留痕，
  不回滚前序已发布结果。
- 核心策略不会假定初始资金。首次必须在“核心交易策略”页面显式
  初始化模拟账户；之后常驻网站每 30 秒自动推进调查、模拟买卖和仓位
  延续，不需要每日点击。舆论达到终态后立即完成，持续未完成时在目标
  发布 60 分钟后按当前完整证据完成，缺失方向保持中性。
- DeepSeek 分类密钥属于一次性本机基础配置：在“舆论监测”页面保存后进入
  当前用户的 macOS 钥匙串，后台重启和每日任务自动复用；未配置时策略仍会
  在等待上限后以舆论中性继续，不会伪造分类结果。
- 休眠或停机后按账户初始化日起最早缺失交易日顺序恢复，不跨日跳过
  持仓管理。页面只保留“清空全部并重新初始化”这一人工生命周期操作。
- 网站基本面页提供“重新运行今日基本面任务”，重跑资本行为；月末交易日同时重跑月度快照。
- 成功和失败都会发送 macOS 通知。失败通知包含失败阶段。
- 通知失败只记录为附属日志，不会回滚已成功发布的快照。
- Mac 睡眠、关机或服务未启动导致错过计划时，下次正式网站启动会在最近 45 个自然日内倒序找到最新行情/策略缺口并补跑；每次启动最多补一个日期。若行情任务仍由其他进程执行，本次不会提前启动基本面。资本行为因上游高管交易窗口限制，只发布数据库最新正式行情日，不把历史缺段标记为完整。

## 日志与排错

```bash
# 任务结构化日志（自动轮转）
tail -n 50 logs/tasks.jsonl

# LaunchAgent 标准输出与错误
tail -n 50 logs/launchd.stdout.log
tail -n 50 logs/launchd.stderr.log

# 常驻网站标准输出与错误
tail -n 50 logs/web.stdout.log
tail -n 50 logs/web.stderr.log

# 检查生成文件语法和系统加载状态
plutil -lint ~/Library/LaunchAgents/com.fourseasquant.daily.plist
launchctl print gui/$(id -u)/com.fourseasquant.daily
```

若状态不存在，重新执行 `install`。若修改过项目路径、Python 虚拟环境或自动更新时间，也应重新安装。所有令牌只应配置在本机安全环境中；LaunchAgent 文件、网页和日志均不得保存 Tushare Token（Tushare 访问令牌）或其他秘密。

macOS 可能阻止后台进程访问 `Desktop`（桌面）等受隐私保护目录，表现为计划任务长期停在 Python 初始化且没有日志。遇到这种情况，应将实际运行副本放在允许后台访问的 `Documents`（文稿）目录后重新安装 LaunchAgent，或在“系统设置 → 隐私与安全性 → 完全磁盘访问权限”中明确授权所用 Python。本机当前计划任务使用 `/Users/lz666/Documents/quant/fourseasquant-t01` 运行副本；`/Users/lz666/Desktop/quant` 仍作为用户项目副本并与 GitHub 同步。
