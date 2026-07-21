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

## 检查与测试

```zsh
npm run typecheck
npm test
```

`npm test` 会先构建前端，再使用临时数据库启动完整后端进程，并通过公开 HTTP 接口验证应用行为。

## 生产式本机预览

```zsh
npm run build
uv run uvicorn --app-dir backend fourseasquant.main:app --host 127.0.0.1 --port 8000
```

然后访问 `http://127.0.0.1:8000`。服务只绑定本机回环地址，不对局域网或公网开放。
