import { useEffect, useMemo, useState } from "react";
import {
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";

import { BrandMark } from "./BrandMark";
import { type StrategyPerformanceData } from "./StrategyPerformance";
import { ReviewNotes } from "./ReviewNotes";
import { type PortfolioReviewData } from "./PortfolioReview";
import { SettingsPanel } from "./SettingsPanel";
import { RealMarketDashboard } from "./RealMarketDashboard";
import { MarketEnvironmentPage } from "./MarketEnvironmentPage";
import { KlineExplorer, type InstrumentSelection } from "./KlineExplorer";
import { TechnicalLeadershipPanel } from "./TechnicalLeadershipPanel";
import { FundamentalsPage } from "./FundamentalsPage";
import { IndustryChainLeadersPage } from "./IndustryChainLeadersPage";
import { PublicOpinionPage } from "./PublicOpinionPage";
import { CoreStrategyPage } from "./CoreStrategyPage";

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

type PageDefinition = {
  path: string;
  label: string;
  shortLabel: string;
  title: string;
  description: string;
};

const pages: PageDefinition[] = [
  {
    path: "/overview",
    label: "总览",
    shortLabel: "01",
    title: "收盘后的全局视图",
    description: "把市场、策略、技术评分与任务状态放在同一条时间线上。",
  },
  {
    path: "/market",
    label: "市场",
    shortLabel: "02",
    title: "市场概览",
    description: "指数、广度、排行榜与成交额热力图。",
  },
  {
    path: "/market-environment",
    label: "市场环境",
    shortLabel: "03",
    title: "机械市场环境",
    description: "上证、深证、市场广度与成交容量的独立趋势判断。",
  },
  {
    path: "/quotes",
    label: "行情",
    shortLabel: "04",
    title: "行情浏览",
    description: "查看规则范围内股票与指数的日 K、成交量和 RSI。",
  },
  {
    path: "/technical-scores",
    label: "技术评分",
    shortLabel: "05",
    title: "全市场技术评分",
    description: "独立查看全市场量价评分、结构证据与历史数据状态。",
  },
  {
    path: "/strategy",
    label: "策略",
    shortLabel: "06",
    title: "核心交易策略",
    description: "查看调查准备、机会判断、模拟订单与完整组合状态。",
  },
  {
    path: "/fundamentals",
    label: "基本面",
    shortLabel: "07",
    title: "个人基本面分析",
    description: "全量基本面股票库、板块筛选与个股独立证据。",
  },
  {
    path: "/industry-chain-leaders",
    label: "产业链龙头",
    shortLabel: "08",
    title: "产业链供需龙头",
    description: "由本地智能体追查供需、产业链传导和实质主营受益。",
  },
  {
    path: "/public-opinion",
    label: "舆论监测",
    shortLabel: "09",
    title: "公开讨论舆论监测",
    description: "分平台查看讨论数量、方向、时效和采集完整性。",
  },
  {
    path: "/tasks",
    label: "任务",
    shortLabel: "10",
    title: "任务中心",
    description: "运行目标日期任务、查看失败状态与历史记录。",
  },
];

const pageByPath = new Map(pages.map((page) => [page.path, page]));

const taskStatusLabels: Record<TaskStatus, string> = {
  not_run: "尚未运行",
  running: "正在运行",
  succeeded: "运行成功",
  failed: "运行失败",
};

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

function initialTargetDate(): string {
  return new URLSearchParams(window.location.search).get("target_date") ?? beijingDate();
}

function initialInstrument(): InstrumentSelection {
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
}

function formatBeijingDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
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
  const location = useLocation();
  const navigate = useNavigate();
  const [connection, setConnection] = useState<ConnectionState>({ kind: "loading" });
  const [targetDate, setTargetDate] = useState(initialTargetDate);
  const [dashboard, setDashboard] = useState<DashboardState>({ kind: "loading" });
  const [isRunning, setIsRunning] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [taskHistory, setTaskHistory] = useState<TaskHistoryItem[]>([]);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.localStorage.getItem("fourseasquant-sidebar") === "collapsed",
  );
  const [selectedInstrument, setSelectedInstrument] =
    useState<InstrumentSelection>(initialInstrument);

  const currentPage = pageByPath.get(location.pathname) ?? pages[0]!;
  const status = connectionCopy(connection);
  const dashboardData = dashboard.kind === "ready" ? dashboard.data : null;
  const snapshot = dashboardData?.snapshot ?? null;

  const linkSearch = useMemo(() => {
    const params = new URLSearchParams(location.search);
    params.set("target_date", targetDate);
    return `?${params.toString()}`;
  }, [location.search, targetDate]);

  function updateTargetDate(value: string) {
    setTargetDate(value);
    const params = new URLSearchParams(location.search);
    params.set("target_date", value);
    navigate(
      { pathname: location.pathname, search: `?${params.toString()}` },
      { replace: true },
    );
  }

  function selectInstrument(selection: InstrumentSelection) {
    setSelectedInstrument(selection);
    const params = new URLSearchParams(location.search);
    params.set("target_date", targetDate);
    params.set("instrument", `${selection.instrumentType}:${selection.code}`);
    params.set("instrument_name", selection.name);
    navigate({ pathname: "/quotes", search: `?${params.toString()}` });
  }

  function toggleSidebar() {
    setSidebarCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem(
        "fourseasquant-sidebar",
        next ? "collapsed" : "expanded",
      );
      return next;
    });
  }

  useEffect(() => {
    if (location.pathname === "/") return;
    const params = new URLSearchParams(location.search);
    if (params.get("target_date") === targetDate) return;
    params.set("target_date", targetDate);
    navigate(
      { pathname: location.pathname, search: `?${params.toString()}` },
      { replace: true },
    );
  }, [location.pathname, location.search, navigate, targetDate]);

  useEffect(() => {
    setSelectedInstrument(initialInstrument());
  }, [location.search]);

  useEffect(() => {
    const controller = new AbortController();
    async function loadHealth() {
      try {
        const response = await fetch("/api/health", { signal: controller.signal });
        if (!response.ok) throw new Error(`健康检查返回 ${response.status}`);
        const health = (await response.json()) as HealthStatus;
        setConnection(
          health.status === "ok" && health.database === "ready"
            ? { kind: "online", health }
            : { kind: "degraded", health },
        );
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
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
        setDashboard({
          kind: "ready",
          data: await fetchDashboard(targetDate, controller.signal),
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
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
      const response = await fetch(
        retry ? "/api/tasks/daily/retry" : "/api/tasks/daily",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target_date: targetDate }),
        },
      );
      if (!response.ok) throw new Error(`每日任务接口返回 ${response.status}`);
    } catch {
      // 后端保留失败状态，下面重新读取最近一次完整成功结果。
    } finally {
      try {
        setDashboard({
          kind: "ready",
          data: await fetchDashboard(targetDate),
        });
      } catch {
        setDashboard({ kind: "error" });
      }
      await refreshTaskHistory().catch(() => undefined);
      setIsRunning(false);
    }
  }

  const failureBanner =
    dashboardData?.failure ? (
      <section className="failure-banner" role="alert" aria-labelledby="failure-title">
        <div>
          <p className="section-kicker">发布保护已生效</p>
          <h2 id="failure-title">今日更新失败</h2>
          <p>
            继续展示 {dashboardData.actual_data_date ?? "最近一次"} 的完整成功结果，
            本次失败未覆盖已发布快照。
          </p>
        </div>
        <dl>
          <div>
            <dt>失败阶段</dt>
            <dd data-testid="failure-stage">{dashboardData.failure.stage_label}</dd>
          </div>
          <div>
            <dt>失败时间</dt>
            <dd>{formatBeijingDateTime(dashboardData.failure.failed_at)}</dd>
          </div>
          <div>
            <dt>错误摘要</dt>
            <dd>{dashboardData.failure.error_summary}</dd>
          </div>
        </dl>
        <button
          type="button"
          disabled={isRunning}
          onClick={() => void runDailyTask(true)}
        >
          {isRunning ? "正在重新运行…" : "重新运行今日任务"}
        </button>
      </section>
    ) : null;

  return (
    <div className={`application-frame${sidebarCollapsed ? " application-frame--collapsed" : ""}`}>
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <aside className="app-sidebar">
        <div className="brand-lockup">
          <BrandMark compact={sidebarCollapsed} />
          <div className="brand-copy">
            <h1>Fourseasquant</h1>
            <span>A 股量化工作台</span>
          </div>
        </div>

        <nav className="primary-nav" aria-label="主要导航">
          {pages.map((page) => (
            <NavLink
              key={page.path}
              to={`${page.path}${linkSearch}`}
              aria-label={page.label}
              className={({ isActive }) =>
                `primary-nav__link${isActive ? " primary-nav__link--active" : ""}`
              }
            >
              <span className="primary-nav__glyph">{page.shortLabel}</span>
              <span className="primary-nav__label">{page.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-status">
            <span className={`status-dot status-dot--${status.tone}`} />
            <div>
              <strong>{status.label}</strong>
              <small>仅在本机运行</small>
            </div>
          </div>
          <button
            className="sidebar-collapse"
            type="button"
            aria-label={sidebarCollapsed ? "展开侧栏" : "折叠侧栏"}
            onClick={toggleSidebar}
          >
            <span aria-hidden="true">{sidebarCollapsed ? "›" : "‹"}</span>
            <span className="primary-nav__label">折叠侧栏</span>
          </button>
        </div>
      </aside>

      <main className="app-main" id="main-content">
        <header className="page-topbar">
          <div>
            <p className="page-context">
              {currentPage.shortLabel} — Fourseasquant / {currentPage.label}
            </p>
            <h2>{currentPage.title}</h2>
            <p>{currentPage.description}</p>
          </div>
          <div className="page-actions">
            <label className="date-control">
              <span>目标日期</span>
              <input
                type="date"
                value={targetDate}
                disabled={isRunning}
                onChange={(event) => updateTargetDate(event.target.value)}
              />
            </label>
            <button
              className="settings-trigger"
              type="button"
              onClick={() => setSettingsOpen(true)}
            >
              设置
            </button>
          </div>
        </header>

        {failureBanner}

        <div className="page-content">
          <Routes>
            <Route path="/" element={<Navigate to={`/overview${linkSearch}`} replace />} />
            <Route
              path="/overview"
              element={
                <>
                  <section className="overview-hero">
                    <div>
                      <p className="section-kicker">收盘后 · 日频</p>
                      <h2>
                        {dashboardData?.actual_data_date
                          ? `${dashboardData.actual_data_date} 数据视图`
                          : "等待第一份完整结果"}
                      </h2>
                      <p>
                        {snapshot?.label ??
                          "运行目标日期任务后，这里会连接市场、策略与技术证据。"}
                      </p>
                    </div>
                    <span className="overview-hero__index" aria-hidden="true">
                      01
                    </span>
                    <div className="overview-hero__signal" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                      <span />
                    </div>
                  </section>

                  <section className="overview-summary-grid" aria-label="系统与发布摘要">
                    <article>
                      <span>本机服务</span>
                      <strong>{status.label}</strong>
                      <small>{status.detail}</small>
                    </article>
                    <article>
                      <span>实际数据日期</span>
                      <strong data-testid="actual-data-date">
                        {dashboardData?.actual_data_date ?? "尚无已发布快照"}
                      </strong>
                      <small>目标日期 {targetDate}</small>
                    </article>
                    <article>
                      <span>每日任务</span>
                      <strong data-testid="task-status">
                        {isRunning
                          ? taskStatusLabels.running
                          : dashboardData
                            ? taskStatusLabels[dashboardData.task_status]
                            : "读取中"}
                      </strong>
                      <small>{formatBeijingDateTime(dashboardData?.last_updated_at ?? null)}</small>
                    </article>
                    <article>
                      <span>核心策略</span>
                      <strong>独立策略页</strong>
                      <small>调查、机会与模拟组合均以核心策略发布为准</small>
                    </article>
                  </section>

                  <TechnicalLeadershipPanel
                    compact
                    targetDate={targetDate}
                    onSelectSecurity={selectInstrument}
                  />

                  <ReviewNotes reviewDate={dashboardData?.actual_data_date ?? null} />
                </>
              }
            />
            <Route
              path="/market"
              element={
                <RealMarketDashboard
                  targetDate={targetDate}
                  onSelectSecurity={selectInstrument}
                />
              }
            />
            <Route
              path="/market-environment"
              element={<MarketEnvironmentPage targetDate={targetDate} />}
            />
            <Route
              path="/quotes"
              element={
                <KlineExplorer
                  targetDate={targetDate}
                  selection={selectedInstrument}
                  onSelectionChange={selectInstrument}
                />
              }
            />
            <Route
              path="/technical-scores"
              element={
                <TechnicalLeadershipPanel
                  targetDate={targetDate}
                  onSelectSecurity={selectInstrument}
                />
              }
            />
            <Route
              path="/strategy"
              element={<CoreStrategyPage targetDate={targetDate} />}
            />
            <Route
              path="/fundamentals"
              element={<FundamentalsPage targetDate={targetDate} />}
            />
            <Route
              path="/industry-chain-leaders"
              element={<IndustryChainLeadersPage />}
            />
            <Route
              path="/public-opinion"
              element={<PublicOpinionPage targetDate={targetDate} />}
            />
            <Route
              path="/tasks"
              element={
                <>
                  <section className="status-panel" aria-labelledby="application-status">
                    <div>
                      <p className="section-kicker">本机运行环境</p>
                      <h2 id="application-status">应用状态</h2>
                      <p>{status.detail}</p>
                    </div>
                    <span className={`status-pill status-pill--${status.tone}`}>
                      {status.label}
                    </span>
                  </section>

                  <section className="snapshot-panel" aria-labelledby="daily-snapshot-status">
                    <div className="snapshot-heading">
                      <div>
                        <p className="section-kicker">每日快照</p>
                        <h2 id="daily-snapshot-status">目标日期与发布状态</h2>
                      </div>
                      <span className="task-target-date">{targetDate}</span>
                    </div>

                    {dashboard.kind === "loading" && (
                      <p className="snapshot-message">正在读取目标日期状态…</p>
                    )}
                    {dashboard.kind === "error" && (
                      <p className="snapshot-message snapshot-message--error">
                        无法读取或运行每日任务，请确认本地服务状态。
                      </p>
                    )}
                    {dashboardData && (
                      <div className="snapshot-content">
                        <dl className="snapshot-facts">
                          <div>
                            <dt>任务状态</dt>
                            <dd data-testid="task-status">
                              {isRunning
                                ? taskStatusLabels.running
                                : taskStatusLabels[dashboardData.task_status]}
                            </dd>
                          </div>
                          <div>
                            <dt>目标日期</dt>
                            <dd>{dashboardData.target_date}</dd>
                          </div>
                          <div>
                            <dt>实际数据日期</dt>
                            <dd data-testid="actual-data-date">
                              {dashboardData.actual_data_date ?? "尚无已发布快照"}
                            </dd>
                          </div>
                          <div>
                            <dt>最近更新时间</dt>
                            <dd>{formatBeijingDateTime(dashboardData.last_updated_at)}</dd>
                          </div>
                        </dl>
                        <div className="snapshot-action">
                          <p>{snapshot?.label ?? "尚无已发布快照"}</p>
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
                          <thead>
                            <tr>
                              <th>目标日期</th>
                              <th>触发</th>
                              <th>开始</th>
                              <th>结束</th>
                              <th>阶段</th>
                              <th>状态 / 摘要</th>
                            </tr>
                          </thead>
                          <tbody>
                            {taskHistory.map((item) => (
                              <tr key={item.id}>
                                <td>{item.target_date}</td>
                                <td>
                                  {(
                                    {
                                      manual: "手动",
                                      retry: "重试",
                                      backfill: "补算",
                                      scheduled: "定时",
                                      startup_catchup: "启动补跑",
                                    } as Record<string, string>
                                  )[item.trigger_method] ?? item.trigger_method}
                                </td>
                                <td>{formatBeijingDateTime(item.started_at)}</td>
                                <td>{formatBeijingDateTime(item.finished_at)}</td>
                                <td>{item.stage_label}</td>
                                <td
                                  className={
                                    item.status === "failed"
                                      ? "history-failed"
                                      : "history-succeeded"
                                  }
                                >
                                  {item.status === "failed"
                                    ? `失败 · ${item.error_summary ?? "查看本机日志"}`
                                    : item.status === "succeeded"
                                      ? "成功"
                                      : "运行中"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </section>
                </>
              }
            />
            <Route path="*" element={<Navigate to={`/overview${linkSearch}`} replace />} />
          </Routes>
        </div>

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
    </div>
  );
}
