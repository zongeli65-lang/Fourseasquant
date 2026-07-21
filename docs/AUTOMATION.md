# Fourseasquant 本机自动化维护

Fourseasquant 使用 macOS LaunchAgent（用户登录后的后台启动代理）每分钟唤醒一次轻量检查，再由应用按设置面板保存的北京时间决定是否运行每日任务。默认时间为 16:30，因此系统时区变化不会改变北京时间口径。定时、手动、补跑均调用同一个 `execute_daily_task` 业务入口，不复制市场、策略、校验或发布逻辑。

## 首次安装

在项目根目录执行：

```bash
uv sync
uv run python scripts/launch_agent_cli.py install
uv run python scripts/launch_agent_cli.py status
```

安装命令会生成 `~/Library/LaunchAgents/com.fourseasquant.daily.plist`，再通过 `launchctl bootstrap` 启用。LaunchAgent 只负责每分钟唤醒；具体时间每次都从本机数据库读取，因此网站内修改自动更新时间后无需重新安装。

日常打开正式网站可运行：

```bash
npm start
```

正式启动会先让网站就绪，再在后台检查并补跑最近 45 个自然日内最新的缺失交易日。开发热更新命令 `npm run dev` 默认不执行启动补跑，避免每次代码重载产生业务副作用。

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
- 同一交易日已有成功快照时，定时入口不会重复发布；网站手动运行仍可显式重算。
- 成功和失败都会发送 macOS 通知。失败通知包含失败阶段。
- 通知失败只记录为附属日志，不会回滚已成功发布的快照。
- Mac 睡眠、关机或服务未启动导致错过计划时，下次正式网站启动会在最近 45 个自然日内倒序找到最新缺口并补跑；每次启动最多补一个日期。

## 日志与排错

```bash
# 任务结构化日志（自动轮转）
tail -n 50 logs/tasks.jsonl

# LaunchAgent 标准输出与错误
tail -n 50 logs/launchd.stdout.log
tail -n 50 logs/launchd.stderr.log

# 检查生成文件语法和系统加载状态
plutil -lint ~/Library/LaunchAgents/com.fourseasquant.daily.plist
launchctl print gui/$(id -u)/com.fourseasquant.daily
```

若状态不存在，重新执行 `install`。若修改过项目路径、Python 虚拟环境或自动更新时间，也应重新安装。所有令牌只应配置在本机安全环境中；LaunchAgent 文件、网页和日志均不得保存 Tushare Token（Tushare 访问令牌）或其他秘密。
