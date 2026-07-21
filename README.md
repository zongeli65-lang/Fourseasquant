# Fourseasquant

Fourseasquant 是仅在本机运行的 A 股市场与策略日频复盘应用。当前切片提供 React（网页界面框架）桌面仪表盘骨架、FastAPI（Python 接口框架）健康接口和 SQLite（本地数据库）初始化。

## 环境准备

项目依赖必须安装到项目目录，不要写入 macOS 系统 Python：

```zsh
uv sync
npm install
```

## 启动

```zsh
npm run dev
```

启动后访问：

- 仪表盘：`http://127.0.0.1:5173`
- 后端健康接口：`http://127.0.0.1:8000/api/health`

开发进程会初始化本地数据库。按 `Control-C` 可同时停止前端和后端。

## 每日快照

在仪表盘的“每日快照”区域选择目标日期，再点击“运行目标日期任务”。当前版本使用确定性模拟适配器生成最小快照，页面会分别显示目标日期、实际数据日期、任务状态和最近更新时间。

当前模拟阶段不需要 Tushare Token（Tushare 访问令牌）。只有后续明确选择 Tushare 作为真实市场数据源并开始实现相应适配器时，才需要在本机安全环境中配置令牌；令牌不得写入代码、网页、日志或 Git。

## 检查与测试

```zsh
npm run typecheck
npm test
```

`npm test` 会先构建前端，再使用独立临时数据库通过公开 HTTP 接口验证后端行为，最后复用本机 Google Chrome（谷歌浏览器）执行页面操作测试。

## 生产式本机预览

```zsh
npm run build
uv run uvicorn --app-dir backend fourseasquant.main:app --host 127.0.0.1 --port 8000
```

然后访问 `http://127.0.0.1:8000`。服务只绑定本机回环地址，不对局域网或公网开放。
