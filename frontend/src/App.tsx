import { useEffect, useState } from "react";

type HealthStatus = {
  application: string;
  status: "ok" | "degraded";
  database: "ready" | "unavailable";
};

type ConnectionState =
  | { kind: "loading" }
  | { kind: "online"; health: HealthStatus }
  | { kind: "degraded"; health: HealthStatus }
  | { kind: "offline" };

const dashboardSections = [
  ["市场情绪", "指数、广度与成交状态将在每日快照中呈现"],
  ["板块与概念", "行业与概念排行榜及热力图将在后续切片接入"],
  ["策略表现", "净值、回撤与基准比较将在后续切片接入"],
  ["持仓与交易", "持仓、交易和收益贡献将在后续切片接入"],
  ["复盘记录", "每日笔记与标签将在后续切片接入"],
] as const;

function connectionCopy(connection: ConnectionState): {
  label: string;
  detail: string;
  tone: "pending" | "success" | "danger";
} {
  if (connection.kind === "loading") {
    return {
      label: "正在连接",
      detail: "正在检查本地服务与数据库",
      tone: "pending",
    };
  }
  if (connection.kind === "online") {
    return {
      label: "运行正常",
      detail: `后端与数据库均已就绪 · ${connection.health.application}`,
      tone: "success",
    };
  }
  if (connection.kind === "degraded") {
    return {
      label: "服务降级",
      detail:
        connection.health.database === "unavailable"
          ? "本地后端可达，但数据库当前不可用"
          : "本地后端可达，但服务报告降级状态",
      tone: "danger",
    };
  }
  return {
    label: "连接失败",
    detail: "无法访问本地服务，请确认开发进程正在运行",
    tone: "danger",
  };
}

export function App() {
  const [connection, setConnection] = useState<ConnectionState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    async function loadHealth() {
      try {
        const response = await fetch("/api/health", { signal: controller.signal });
        if (!response.ok) {
          throw new Error(`健康检查返回 ${response.status}`);
        }
        const health = (await response.json()) as HealthStatus;
        if (health.status !== "ok" || health.database !== "ready") {
          setConnection({ kind: "degraded", health });
          return;
        }
        setConnection({ kind: "online", health });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setConnection({ kind: "offline" });
      }
    }

    void loadHealth();
    return () => controller.abort();
  }, []);

  const status = connectionCopy(connection);

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">A 股市场与策略复盘</p>
          <h1>Fourseasquant</h1>
        </div>
        <div className="local-only">本机专用 · 日频</div>
      </header>

      <section className="status-panel" aria-labelledby="application-status">
        <div>
          <p className="section-kicker">系统</p>
          <h2 id="application-status">应用状态</h2>
          <p>{status.detail}</p>
        </div>
        <span className={`status-pill status-pill--${status.tone}`}>{status.label}</span>
      </section>

      <section className="section-grid" aria-label="仪表盘模块">
        {dashboardSections.map(([title, description]) => (
          <article className="module-card" key={title}>
            <span className="module-number">待接入</span>
            <h2>{title}</h2>
            <p>{description}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
