import { useEffect, useState } from "react";
import { MarketOverview, type MarketOverviewData } from "./MarketOverview";

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

type TaskStatus = "not_run" | "running" | "succeeded" | "failed";

type DashboardSnapshot = {
  source: string;
  label: string;
  seed: number;
  market_overview: MarketOverviewData;
};

type DashboardData = {
  target_date: string;
  actual_data_date: string | null;
  last_updated_at: string | null;
  task_status: TaskStatus;
  snapshot: DashboardSnapshot | null;
};

type DashboardState =
  | { kind: "loading" }
  | { kind: "ready"; data: DashboardData }
  | { kind: "error" };

async function fetchDashboard(
  targetDate: string,
  signal?: AbortSignal,
): Promise<DashboardData> {
  const response = await fetch(
    `/api/dashboard?target_date=${encodeURIComponent(targetDate)}`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(`仪表盘接口返回 ${response.status}`);
  }
  return (await response.json()) as DashboardData;
}

const dashboardSections = [
  ["板块与概念", "行业与概念排行榜及热力图将在后续切片接入"],
  ["策略表现", "净值、回撤与基准比较将在后续切片接入"],
  ["持仓与交易", "持仓、交易和收益贡献将在后续切片接入"],
  ["复盘记录", "每日笔记与标签将在后续切片接入"],
] as const;

const taskStatusLabels: Record<TaskStatus, string> = {
  not_run: "尚未运行",
  running: "正在运行",
  succeeded: "运行成功",
  failed: "运行失败",
};

function beijingDate(): string {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

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
  const [targetDate, setTargetDate] = useState(beijingDate);
  const [dashboard, setDashboard] = useState<DashboardState>({ kind: "loading" });
  const [isRunning, setIsRunning] = useState(false);

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

  useEffect(() => {
    const controller = new AbortController();

    async function loadDashboard() {
      setDashboard({ kind: "loading" });
      try {
        const data = await fetchDashboard(targetDate, controller.signal);
        setDashboard({ kind: "ready", data });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setDashboard({ kind: "error" });
      }
    }

    void loadDashboard();
    return () => controller.abort();
  }, [targetDate]);

  async function runDailyTask() {
    setIsRunning(true);
    try {
      const response = await fetch("/api/tasks/daily", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_date: targetDate }),
      });
      if (!response.ok) {
        throw new Error(`每日任务接口返回 ${response.status}`);
      }
      const data = await fetchDashboard(targetDate);
      setDashboard({ kind: "ready", data });
    } catch {
      setDashboard({ kind: "error" });
    } finally {
      setIsRunning(false);
    }
  }

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

      <section className="snapshot-panel" aria-labelledby="daily-snapshot-status">
        <div className="snapshot-heading">
          <div>
            <p className="section-kicker">每日快照</p>
            <h2 id="daily-snapshot-status">目标日期与发布状态</h2>
          </div>
          <label className="date-control">
            <span>目标日期</span>
            <input
              type="date"
              value={targetDate}
              onChange={(event) => setTargetDate(event.target.value)}
            />
          </label>
        </div>

        {dashboard.kind === "loading" && (
          <p className="snapshot-message">正在读取目标日期状态…</p>
        )}
        {dashboard.kind === "error" && (
          <p className="snapshot-message snapshot-message--error">
            无法读取或运行每日任务，请确认本地服务状态。
          </p>
        )}
        {dashboard.kind === "ready" && (
          <div className="snapshot-content">
            <dl className="snapshot-facts">
              <div>
                <dt>任务状态</dt>
                <dd data-testid="task-status">
                  {isRunning
                    ? taskStatusLabels.running
                    : taskStatusLabels[dashboard.data.task_status]}
                </dd>
              </div>
              <div>
                <dt>目标日期</dt>
                <dd>{dashboard.data.target_date}</dd>
              </div>
              <div>
                <dt>实际数据日期</dt>
                <dd data-testid="actual-data-date">
                  {dashboard.data.actual_data_date ?? "尚无已发布快照"}
                </dd>
              </div>
              <div>
                <dt>最近更新时间</dt>
                <dd>
                  {dashboard.data.last_updated_at
                    ? new Date(dashboard.data.last_updated_at).toLocaleString("zh-CN", {
                        timeZone: "Asia/Shanghai",
                        hour12: false,
                      })
                    : "—"}
                </dd>
              </div>
            </dl>
            <div className="snapshot-action">
              <p>
                {dashboard.data.snapshot?.label ?? "尚无已发布快照"}
              </p>
              <button
                type="button"
                disabled={isRunning}
                onClick={() => void runDailyTask()}
              >
                {isRunning ? "正在运行…" : "运行目标日期任务"}
              </button>
            </div>
          </div>
        )}
      </section>

      {dashboard.kind === "ready" && dashboard.data.snapshot && (
        <MarketOverview data={dashboard.data.snapshot.market_overview} />
      )}

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
