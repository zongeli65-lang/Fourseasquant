import { useEffect, useState } from "react";
import {
  StrategyPerformance,
  type StrategyPerformanceData,
} from "./StrategyPerformance";
import { ReviewNotes } from "./ReviewNotes";
import { PortfolioReview, type PortfolioReviewData } from "./PortfolioReview";
import { SettingsPanel } from "./SettingsPanel";
import { RealMarketDashboard } from "./RealMarketDashboard";
import { KlineExplorer, type InstrumentSelection } from "./KlineExplorer";
import { TechnicalLeadershipPanel } from "./TechnicalLeadershipPanel";

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
  strategy_performance: StrategyPerformanceData;
  portfolio_review: PortfolioReviewData;
};

type DashboardData = {
  target_date: string;
  actual_data_date: string | null;
  last_updated_at: string | null;
  task_status: TaskStatus;
  failure: {
    stage: string;
    stage_label: string;
    failed_at: string;
    error_summary: string;
  } | null;
  snapshot: DashboardSnapshot | null;
};

type TaskHistoryItem = {
  id: number;
  trigger_method: string;
  target_date: string;
  started_at: string;
  finished_at: string | null;
  stage_label: string;
  status: string;
  error_summary: string | null;
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [taskHistory, setTaskHistory] = useState<TaskHistoryItem[]>([]);
  const [selectedInstrument, setSelectedInstrument] = useState<InstrumentSelection>(() => {
    const params = new URLSearchParams(window.location.search);
    const instrument = params.get("instrument");
    const name = params.get("instrument_name");
    if (instrument) {
      const [instrumentType, code] = instrument.split(":");
      if ((instrumentType === "stock" || instrumentType === "index") && code) {
        return { instrumentType, code, name: name || code };
      }
    }
    return { instrumentType: "index", code: "sh000001", name: "上证指数" };
  });

  function selectInstrument(selection: InstrumentSelection) {
    setSelectedInstrument(selection);
    const url = new URL(window.location.href);
    url.searchParams.set("instrument", `${selection.instrumentType}:${selection.code}`);
    url.searchParams.set("instrument_name", selection.name);
    window.history.replaceState(null, "", url);
    window.setTimeout(() => {
      document.querySelector("[data-testid='kline-explorer']")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }

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

  async function refreshTaskHistory() {
    const response = await fetch("/api/tasks/history?limit=10");
    if (response.ok) {
      setTaskHistory((await response.json()) as TaskHistoryItem[]);
    }
  }

  useEffect(() => {
    void refreshTaskHistory().catch(() => undefined);
  }, []);

  async function runDailyTask(retry = false) {
    setIsRunning(true);
    try {
      const response = await fetch(retry ? "/api/tasks/daily/retry" : "/api/tasks/daily", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_date: targetDate }),
      });
      if (!response.ok) {
        throw new Error(`每日任务接口返回 ${response.status}`);
      }
    } catch {
      // 失败状态已由后端记录；下面统一重新读取，继续保留最近成功快照。
    } finally {
      try {
        const data = await fetchDashboard(targetDate);
        setDashboard({ kind: "ready", data });
      } catch {
        setDashboard({ kind: "error" });
      }
      try {
        await refreshTaskHistory();
      } catch {
        // 历史是辅助视图，读取失败不能隐藏已经成功读取的仪表盘。
      }
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
        <div className="topbar-actions">
          <button type="button" onClick={() => setSettingsOpen(true)}>设置</button>
          <div className="local-only">本机专用 · 日频</div>
        </div>
      </header>

      {dashboard.kind === "ready" && dashboard.data.failure && (
        <section className="failure-banner" role="alert" aria-labelledby="failure-title">
          <div>
            <p className="section-kicker">最近一次任务</p>
            <h2 id="failure-title">今日更新失败</h2>
            <p>
              继续展示 {dashboard.data.actual_data_date ?? "最近一次"} 的完整成功结果，
              本次失败未覆盖已发布快照。
            </p>
          </div>
          <dl>
            <div><dt>失败阶段</dt><dd data-testid="failure-stage">{dashboard.data.failure.stage_label}</dd></div>
            <div><dt>失败时间</dt><dd>{new Date(dashboard.data.failure.failed_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}</dd></div>
            <div><dt>错误摘要</dt><dd>{dashboard.data.failure.error_summary}</dd></div>
          </dl>
          <button type="button" disabled={isRunning} onClick={() => void runDailyTask(true)}>
            {isRunning ? "正在重新运行…" : "重新运行今日任务"}
          </button>
        </section>
      )}

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
              disabled={isRunning}
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
                onClick={() => void runDailyTask(false)}
              >
                {isRunning ? "正在运行…" : "运行目标日期任务"}
              </button>
            </div>
          </div>
        )}
      </section>

      <TechnicalLeadershipPanel
        targetDate={targetDate}
        onSelectSecurity={selectInstrument}
      />

      <RealMarketDashboard targetDate={targetDate} onSelectSecurity={selectInstrument} />

      <KlineExplorer
        targetDate={targetDate}
        selection={selectedInstrument}
        onSelectionChange={selectInstrument}
      />

      <section className="task-history" aria-labelledby="task-history-title">
        <div className="snapshot-heading">
          <div>
            <p className="section-kicker">运行记录</p>
            <h2 id="task-history-title">任务历史</h2>
          </div>
          <span>最近 {taskHistory.length} 条</span>
        </div>
        {taskHistory.length === 0 ? (
          <p className="snapshot-message">尚无任务记录。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead><tr><th>目标日期</th><th>触发</th><th>开始</th><th>结束</th><th>阶段</th><th>状态 / 摘要</th></tr></thead>
              <tbody>
                {taskHistory.map((item) => (
                  <tr key={item.id}>
                    <td>{item.target_date}</td>
                    <td>{({ manual: "手动", retry: "重试", backfill: "补算", scheduled: "定时" } as Record<string, string>)[item.trigger_method] ?? item.trigger_method}</td>
                    <td>{new Date(item.started_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}</td>
                    <td>{item.finished_at ? new Date(item.finished_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "—"}</td>
                    <td>{item.stage_label}</td>
                    <td className={item.status === "failed" ? "history-failed" : "history-succeeded"}>{item.status === "failed" ? `失败 · ${item.error_summary ?? "查看本机日志"}` : item.status === "succeeded" ? "成功" : "运行中"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {dashboard.kind === "ready" && dashboard.data.snapshot && (
        <StrategyPerformance data={dashboard.data.snapshot.strategy_performance} />
      )}

      {dashboard.kind === "ready" && dashboard.data.snapshot && (
        <PortfolioReview data={dashboard.data.snapshot.portfolio_review} onSelectSecurity={selectInstrument} />
      )}

      <ReviewNotes
        reviewDate={
          dashboard.kind === "ready" ? dashboard.data.actual_data_date : null
        }
      />

      <SettingsPanel
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onBackfillCompleted={() => {
          void fetchDashboard(targetDate)
            .then((data) => setDashboard({ kind: "ready", data }))
            .catch(() => undefined);
          void refreshTaskHistory().catch(() => undefined);
        }}
      />

    </main>
  );
}
