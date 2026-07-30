import { FormEvent, useCallback, useEffect, useState } from "react";

type RuntimeStage =
  | "account_uninitialized"
  | "not_prepared"
  | "blocked_inputs"
  | "investigations_ready"
  | "finalized"
  | "failed";

type RuntimeStatus = {
  requested_date: string;
  actual_date: string | null;
  strategy_version: string;
  stage: RuntimeStage;
  message: string;
  account: {
    initial_capital: number;
    initialized_at: string;
  } | null;
  candidate_count: number;
  fundamental_target_count: number;
  opinion_target_codes: string[];
  opinion_jobs: {
    pending: number;
    running: number;
    succeeded: number;
    failed: number;
    blocked: number;
  };
  prepared_at: string | null;
  auto_finalize_at: string | null;
  finalized_at: string | null;
  order_count: number;
  holding_count: number;
  available_cash: number | null;
  net_asset_value: number | null;
  portfolio_publication_complete: boolean;
  latest_attempt: {
    action: "prepare" | "finalize";
    status: "succeeded" | "blocked" | "failed";
    attempted_at: string;
    error_summary: string | null;
  } | null;
};

type EntryDecision = {
  code: string;
  name: string;
  route: "industry_chain" | "technical_mainline" | "pure_technical";
  preliminary_rank: number;
  base_grade: "B" | "C";
  grade: "S" | "A" | "B" | "C" | null;
  net_reward_risk_ratio: number | null;
  position_fraction: number;
  open_allowed: boolean;
  block_reasons: string[];
  fundamental_priority: "preferred" | "neutral" | "deprioritized";
};

type BuyOrder = {
  code: string;
  name: string;
  execution_price: number;
  shares: number;
  total_cash: number;
  grade: "S" | "A" | "B" | "C";
  stop_price: number;
  pressure_target: number | null;
};

type SellOrder = {
  code: string;
  name: string;
  execution_session: "open" | "close";
  execution_price: number;
  shares: number;
  net_cash: number;
  reason: string;
};

type StrategyDayResponse = {
  snapshot: {
    actual_date: string;
    strategy_version: string;
    market_state: "rising" | "sideways" | "falling";
    entry_planning: {
      maximum_positions: number;
      full_position_slot: number;
      candidates: EntryDecision[];
      orders: BuyOrder[];
      remaining_cash: number;
    };
    position_management: {
      orders: SellOrder[];
    };
  };
  published_at: string;
};

type HoldingPosition = {
  code: string;
  name: string;
  shares: number;
  cost_price: number;
  stop_price: number;
  pressure_target: number | null;
  highest_close_since_entry: number;
  final_profit_line: number | null;
  pending_exit_reason: string | null;
};

type PortfolioResponse = {
  snapshot: {
    actual_date: string;
    strategy_version: string;
    initial_capital: number;
    available_cash: number;
    net_asset_value: number;
    positions: HoldingPosition[];
    marks: {
      code: string;
      price: number;
      mark_date: string;
      source: "daily_close" | "entry_execution";
    }[];
  };
  published_at: string;
};

type PerformancePoint = {
  actual_date: string;
  net_asset_value: number;
  normalized_nav: number;
  daily_profit_loss: number;
  daily_return_pct: number;
  cumulative_profit_loss: number;
  cumulative_return_pct: number;
  drawdown_pct: number;
};

type PerformanceResponse = {
  requested_as_of_date: string;
  actual_date: string;
  strategy_version: string;
  sample_count: number;
  initial_capital: number;
  available_cash: number;
  holding_market_value: number;
  net_asset_value: number;
  daily_profit_loss: number;
  daily_return_pct: number;
  cumulative_profit_loss: number;
  cumulative_return_pct: number;
  statistics: {
    annualized_return_pct: number | null;
    annualized_return_unavailable_reason: string | null;
    max_drawdown_pct: number | null;
    current_drawdown_pct: number | null;
    sharpe_ratio: number | null;
    sharpe_ratio_unavailable_reason: string | null;
    win_rate_pct: number | null;
    win_rate_unavailable_reason: string | null;
  };
  points: PerformancePoint[];
};

type LoadState =
  | { kind: "loading" }
  | {
      kind: "ready";
      status: RuntimeStatus;
      day: StrategyDayResponse | null;
      portfolio: PortfolioResponse | null;
      performance: PerformanceResponse | null;
    }
  | { kind: "error"; message: string };

const stageLabels: Record<RuntimeStage, string> = {
  account_uninitialized: "账户未初始化",
  not_prepared: "尚未准备",
  blocked_inputs: "输入未完整",
  investigations_ready: "调查目标已发布",
  finalized: "策略日已完成",
  failed: "运行失败",
};

const marketLabels = {
  rising: "上升",
  sideways: "震荡",
  falling: "下降",
} as const;

const routeLabels: Record<EntryDecision["route"], string> = {
  industry_chain: "产业链",
  technical_mainline: "技术主线",
  pure_technical: "纯技术",
};

const fundamentalLabels: Record<EntryDecision["fundamental_priority"], string> = {
  preferred: "优先",
  neutral: "普通",
  deprioritized: "降后",
};

const reasonLabels: Record<string, string> = {
  stop_close_break: "收盘跌破止损",
  pressure_target_touched: "触及压力目标",
  strong_bearish: "强看空形态",
  standard_rsi_top_divergence: "标准 RSI 顶背离",
  weak_bearish_rsi_composite: "弱看空与 RSI 复合证据",
  final_profit_protection: "最终利润保护",
};

function money(value: number | null): string {
  if (value === null) return "—";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 2,
  }).format(value);
}

function percentage(value: number): string {
  return `${(value * 100).toFixed(0)}%`;
}

function performancePercentage(value: number | null): string {
  if (value === null) return "—";
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${value.toFixed(2)}%`;
}

function plainPercentage(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(2)}%`;
}

function decimal(value: number | null): string {
  return value === null ? "—" : value.toFixed(2);
}

function chartPoints(
  values: number[],
  width: number,
  height: number,
  includeZero = false,
): string {
  if (values.length === 0) return "";
  const domain = includeZero ? [...values, 0] : values;
  const minimum = Math.min(...domain);
  const maximum = Math.max(...domain);
  const range = maximum - minimum;
  return values
    .map((value, index) => {
      const x =
        values.length === 1
          ? width / 2
          : (index / (values.length - 1)) * width;
      const y =
        range === 0
          ? height / 2
          : height - ((value - minimum) / range) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function dateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `接口返回 ${response.status}`;
  } catch {
    return `接口返回 ${response.status}`;
  }
}

export function CoreStrategyPage({ targetDate }: { targetDate: string }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [initialCapital, setInitialCapital] = useState("");
  const [action, setAction] = useState<"initialize" | "reset" | null>(null);
  const [feedback, setFeedback] = useState("");

  const load = useCallback(async (
    signal?: AbortSignal,
    quiet = false,
  ) => {
    if (!quiet) setState({ kind: "loading" });
    try {
      const statusResponse = await fetch(
        `/api/core-strategy/run-status?target_date=${encodeURIComponent(targetDate)}`,
        { signal },
      );
      if (!statusResponse.ok) {
        throw new Error(await errorDetail(statusResponse));
      }
      const status = (await statusResponse.json()) as RuntimeStatus;
      let day: StrategyDayResponse | null = null;
      let portfolio: PortfolioResponse | null = null;
      let performance: PerformanceResponse | null = null;
      if (status.stage === "finalized" && status.actual_date) {
        const [dayResponse, portfolioResponse] = await Promise.all([
          fetch(
            `/api/core-strategy/days/${encodeURIComponent(status.actual_date)}`
            + `?strategy_version=${encodeURIComponent(status.strategy_version)}`,
            { signal },
          ),
          fetch(
            `/api/core-strategy/portfolio/${encodeURIComponent(status.actual_date)}`
            + `?strategy_version=${encodeURIComponent(status.strategy_version)}`,
            { signal },
          ),
        ]);
        if (!dayResponse.ok) {
          throw new Error(await errorDetail(dayResponse));
        }
        day = (await dayResponse.json()) as StrategyDayResponse;
        if (portfolioResponse.ok) {
          portfolio = (await portfolioResponse.json()) as PortfolioResponse;
        } else if (portfolioResponse.status !== 404) {
          throw new Error(await errorDetail(portfolioResponse));
        }
      }
      if (status.account !== null && portfolio === null) {
        const latestPortfolioResponse = await fetch(
          `/api/core-strategy/portfolio/latest`
          + `?as_of_date=${encodeURIComponent(targetDate)}`,
          { signal },
        );
        if (latestPortfolioResponse.ok) {
          portfolio =
            (await latestPortfolioResponse.json()) as PortfolioResponse;
          const latestDayResponse = await fetch(
            `/api/core-strategy/days/`
            + encodeURIComponent(portfolio.snapshot.actual_date)
            + `?strategy_version=`
            + encodeURIComponent(portfolio.snapshot.strategy_version),
            { signal },
          );
          if (latestDayResponse.ok) {
            const latestDay =
              (await latestDayResponse.json()) as StrategyDayResponse;
            if (
              latestDay.snapshot.actual_date
              === portfolio.snapshot.actual_date
            ) {
              day = latestDay;
            }
          } else if (latestDayResponse.status !== 404) {
            throw new Error(await errorDetail(latestDayResponse));
          }
        } else if (latestPortfolioResponse.status !== 404) {
          throw new Error(await errorDetail(latestPortfolioResponse));
        }
      }
      if (status.account !== null) {
        const performanceResponse = await fetch(
          `/api/core-strategy/performance`
          + `?as_of_date=${encodeURIComponent(targetDate)}`,
          { signal },
        );
        if (performanceResponse.ok) {
          performance =
            (await performanceResponse.json()) as PerformanceResponse;
        } else if (performanceResponse.status !== 404) {
          throw new Error(await errorDetail(performanceResponse));
        }
      }
      setState({ kind: "ready", status, day, portfolio, performance });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "核心策略接口读取失败",
      });
    }
  }, [targetDate]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const shouldPoll =
    state.kind === "ready"
    && state.status.account !== null
    && state.status.stage !== "finalized";

  useEffect(() => {
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      void load(undefined, true);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [load, shouldPoll]);

  async function initializeAccount(event: FormEvent) {
    event.preventDefault();
    const value = Number(initialCapital);
    if (!Number.isFinite(value) || value <= 0) {
      setFeedback("请输入大于零的初始资金。");
      return;
    }
    setAction("initialize");
    setFeedback("");
    try {
      const response = await fetch("/api/core-strategy/account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ initial_capital: value }),
      });
      if (!response.ok) throw new Error(await errorDetail(response));
      setFeedback("模拟账户初始化完成。");
      await load();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "账户初始化失败");
    } finally {
      setAction(null);
    }
  }

  async function resetAccount() {
    const confirmed = window.confirm(
      "这会清空全部模拟持仓、订单、策略日和初始资金。"
      + "历史市场及舆论证据不会删除。确定继续吗？",
    );
    if (!confirmed) return;
    setAction("reset");
    setFeedback("");
    try {
      const response = await fetch("/api/core-strategy/account", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "清空全部" }),
      });
      if (!response.ok) throw new Error(await errorDetail(response));
      setInitialCapital("");
      setFeedback("模拟账户已经清空，可以重新设置初始资金。");
      await load();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : "模拟账户清空失败");
    } finally {
      setAction(null);
    }
  }

  if (state.kind === "loading") {
    return (
      <section className="core-strategy-state" aria-live="polite">
        <p>正在读取核心策略状态…</p>
      </section>
    );
  }
  if (state.kind === "error") {
    return (
      <section className="core-strategy-state core-strategy-state--error">
        <div>
          <p className="section-kicker">只读结果未改变</p>
          <h2>核心策略页面读取失败</h2>
          <p>{state.message}</p>
        </div>
        <button type="button" onClick={() => void load()}>
          重新读取
        </button>
      </section>
    );
  }

  const { status, day, portfolio, performance } = state;
  const buyOrders = day?.snapshot.entry_planning.orders ?? [];
  const sellOrders = day?.snapshot.position_management.orders ?? [];
  const positions = portfolio?.snapshot.positions ?? [];
  const markByCode = new Map(
    (portfolio?.snapshot.marks ?? []).map((mark) => [mark.code, mark]),
  );
  const displayedDate =
    portfolio?.snapshot.actual_date
    ?? performance?.actual_date
    ?? day?.snapshot.actual_date
    ?? status.actual_date;
  const displayedStrategyVersion =
    day?.snapshot.strategy_version
    ?? performance?.strategy_version
    ?? status.strategy_version;
  const isShowingPriorResult =
    displayedDate !== null && displayedDate !== targetDate;
  const markedPositionValues = positions.map((position) => {
    const mark = markByCode.get(position.code);
    return mark ? position.shares * mark.price : null;
  });
  const portfolioHoldingMarketValue =
    markedPositionValues.every((value) => value !== null)
      ? markedPositionValues.reduce<number>(
          (total, value) => total + (value ?? 0),
          0,
        )
      : null;
  const baselineCapital =
    performance?.initial_capital
    ?? portfolio?.snapshot.initial_capital
    ?? status.account?.initial_capital
    ?? null;
  const displayedAvailableCash =
    performance?.available_cash
    ?? portfolio?.snapshot.available_cash
    ?? status.available_cash;
  const displayedHoldingMarketValue =
    performance?.holding_market_value
    ?? portfolioHoldingMarketValue;
  const displayedNetAssetValue =
    performance?.net_asset_value
    ?? portfolio?.snapshot.net_asset_value
    ?? status.net_asset_value;
  const opinionFinished =
    status.opinion_jobs.succeeded
    + status.opinion_jobs.failed
    + status.opinion_jobs.blocked;
  const opinionTotal =
    opinionFinished + status.opinion_jobs.pending + status.opinion_jobs.running;
  const performanceMetrics = performance
    ? [
        {
          label: "当日收益",
          value: performancePercentage(performance.daily_return_pct),
          detail: `当日盈亏 ${money(performance.daily_profit_loss)}`,
          raw: performance.daily_return_pct,
        },
        {
          label: "累计收益",
          value: performancePercentage(performance.cumulative_return_pct),
          detail: `累计盈亏 ${money(performance.cumulative_profit_loss)}`,
          raw: performance.cumulative_return_pct,
        },
        {
          label: "年化收益",
          value: performancePercentage(
            performance.statistics.annualized_return_pct,
          ),
          detail:
            performance.statistics.annualized_return_unavailable_reason
            ?? "按完整策略日序列计算",
          raw: performance.statistics.annualized_return_pct,
        },
        {
          label: "最大回撤",
          value: performancePercentage(
            performance.statistics.max_drawdown_pct,
          ),
          detail: "成立以来峰值至谷值",
          raw: performance.statistics.max_drawdown_pct,
        },
        {
          label: "当前回撤",
          value: performancePercentage(
            performance.statistics.current_drawdown_pct,
          ),
          detail: "当前净值相对历史峰值",
          raw: performance.statistics.current_drawdown_pct,
        },
        {
          label: "夏普比率",
          value: decimal(performance.statistics.sharpe_ratio),
          detail:
            performance.statistics.sharpe_ratio_unavailable_reason
            ?? "日收益风险调整后表现",
          raw: performance.statistics.sharpe_ratio,
        },
        {
          label: "胜率",
          value: plainPercentage(performance.statistics.win_rate_pct),
          detail:
            performance.statistics.win_rate_unavailable_reason
            ?? "正收益完整策略日占比",
          raw:
            performance.statistics.win_rate_pct === null
              ? null
              : 0,
        },
      ]
    : [];
  const normalizedNavLine = performance
    ? chartPoints(
        performance.points.map((point) => point.normalized_nav),
        1000,
        180,
      )
    : "";
  const drawdownLine = performance
    ? chartPoints(
        performance.points.map((point) => point.drawdown_pct),
        1000,
        120,
        true,
      )
    : "";

  return (
    <div className="core-strategy-page">
      <section className="core-strategy-hero">
        <div>
          <p className="section-kicker">日频收盘后 · 模拟执行</p>
          <h2>唯一核心交易策略</h2>
          <p>{status.message}</p>
        </div>
        <div className="core-strategy-hero__state">
          <span data-stage={status.stage}>{stageLabels[status.stage]}</span>
          <small>请求日期 {targetDate}</small>
          {isShowingPriorResult ? (
            <strong>当前展示最近完整结果 {displayedDate}</strong>
          ) : (
            <strong>
              {displayedDate
                ? `实际结果 ${displayedDate}`
                : "今日完整结果尚未发布"}
            </strong>
          )}
          <small>{displayedStrategyVersion}</small>
        </div>
      </section>

      <section className="core-strategy-summary" aria-label="核心策略摘要">
        <article>
          <span>基准本金</span>
          <strong>{money(baselineCapital)}</strong>
          <small>
            {status.account
              ? `仅作收益计算基准；初始化于 ${dateTime(status.account.initialized_at)}`
              : "等待用户设置"}
          </small>
        </article>
        <article>
          <span>可用现金</span>
          <strong>{money(displayedAvailableCash)}</strong>
          <small>{displayedDate ? `截至 ${displayedDate}` : "等待完整组合"}</small>
        </article>
        <article>
          <span>持仓市值</span>
          <strong>{money(displayedHoldingMarketValue)}</strong>
          <small>{positions.length} 只持仓按收盘价标记</small>
        </article>
        <article>
          <span>组合净资产</span>
          <strong>{money(displayedNetAssetValue)}</strong>
          <small>可用现金与持仓市值合计</small>
        </article>
        <article>
          <span>市场状态</span>
          <strong>
            {day ? marketLabels[day.snapshot.market_state] : "等待策略日"}
          </strong>
          <small>市场转换只改变新选股路径</small>
        </article>
        <article>
          <span>请求日初筛候选</span>
          <strong>{status.candidate_count}</strong>
          <small>基本面调查目标 {status.fundamental_target_count}</small>
        </article>
        <article>
          <span>请求日舆论调查</span>
          <strong>
            {opinionTotal > 0 ? `${opinionFinished} / ${opinionTotal}` : "0"}
          </strong>
          <small>仅策略前十目标进入新开仓调查</small>
        </article>
        <article>
          <span>完整结果</span>
          <strong>{displayedDate ?? "—"}</strong>
          <small>
            {portfolio
              ? "组合已完整发布并连续继承"
              : "没有完整组合快照"}
          </small>
        </article>
      </section>

      {performance && (
        <section
          className="core-strategy-performance"
          aria-labelledby="core-strategy-performance-title"
        >
          <header>
            <div>
              <p className="section-kicker">真实组合 · 连续记账</p>
              <h3 id="core-strategy-performance-title">策略收益与风险</h3>
              <p>
                每个完整策略日以上一日组合为起点连续计算，
                基准本金不会被误作当前现金。
              </p>
            </div>
            <div className="core-strategy-performance__meta">
              <strong>截至 {performance.actual_date}</strong>
              <span>{performance.sample_count} 个完整策略日</span>
            </div>
          </header>

          <div className="core-strategy-performance__metrics">
            {performanceMetrics.map((metric) => (
              <article
                key={metric.label}
                data-tone={
                  metric.raw === null
                    ? "unavailable"
                    : metric.raw > 0
                      ? "positive"
                      : metric.raw < 0
                        ? "negative"
                        : "neutral"
                }
              >
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
                <small>{metric.detail}</small>
              </article>
            ))}
          </div>

          <div className="core-strategy-performance__charts">
            <article
              className="core-strategy-performance__chart"
              data-testid="core-strategy-nav-chart"
            >
              <header>
                <h4>策略净值曲线</h4>
                <strong>
                  {performance.points.at(-1)?.normalized_nav.toFixed(4) ?? "—"}
                </strong>
              </header>
              <svg
                viewBox="0 0 1000 180"
                preserveAspectRatio="none"
                aria-label="核心策略净值曲线"
              >
                <polyline points={normalizedNavLine} />
                {performance.points.length === 1 && (
                  <circle cx="500" cy="90" r="6" />
                )}
              </svg>
              <footer>
                <span>{performance.points[0]?.actual_date ?? "—"}</span>
                <span>
                  {performance.points.at(-1)?.actual_date ?? "—"}
                </span>
              </footer>
            </article>
            <article
              className="core-strategy-performance__chart core-strategy-performance__chart--drawdown"
              data-testid="core-strategy-drawdown-chart"
            >
              <header>
                <h4>回撤曲线</h4>
                <strong>
                  {performancePercentage(
                    performance.statistics.current_drawdown_pct,
                  )}
                </strong>
              </header>
              <svg
                viewBox="0 0 1000 120"
                preserveAspectRatio="none"
                aria-label="核心策略回撤曲线"
              >
                <polyline points={drawdownLine} />
                {performance.points.length === 1 && (
                  <circle cx="500" cy="60" r="6" />
                )}
              </svg>
              <footer>
                <span>峰值为 0%</span>
                <span>越低表示回撤越深</span>
              </footer>
            </article>
          </div>
        </section>
      )}

      {feedback && (
        <p className="core-strategy-feedback" role="status" aria-live="polite">
          {feedback}
        </p>
      )}

      {status.stage === "account_uninitialized" && (
        <section className="core-strategy-action-panel">
          <div>
            <p className="section-kicker">首次使用</p>
            <h3>设置模拟账户初始资金</h3>
            <p>
              初始资金会固定最大持股数量。系统不会替你假定金额，
              初始化后也不会静默更改。
            </p>
          </div>
          <form onSubmit={initializeAccount}>
            <label>
              <span>初始资金（元）</span>
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={initialCapital}
                onChange={(event) => setInitialCapital(event.target.value)}
              />
            </label>
            <button type="submit" disabled={action !== null}>
              {action === "initialize" ? "正在初始化…" : "初始化模拟账户"}
            </button>
          </form>
        </section>
      )}

      {(status.stage === "not_prepared"
        || status.stage === "blocked_inputs"
        || status.stage === "failed") && (
        <section className="core-strategy-action-panel">
          <div>
            <p className="section-kicker">全自动运行</p>
            <h3>后台正在等待完整输入</h3>
            <p>
              仅在同日市场环境、技术评分、前复权 K 线及公司行为完整后，
              才会自动发布候选和调查目标。后台会持续重试，无需人工运行。
            </p>
            {status.latest_attempt?.error_summary && (
              <p className="core-strategy-action-panel__error">
                {status.latest_attempt.error_summary}
              </p>
            )}
          </div>
          <span className="core-strategy-automatic-label">无需人工操作</span>
        </section>
      )}

      {status.stage === "investigations_ready" && (
        <section className="core-strategy-investigations">
          <header>
            <div>
              <p className="section-kicker">第二阶段</p>
              <h3>调查目标已经锁定</h3>
              <p>
                基本面只影响同级排序；舆论不足不会伪造方向，也不会删除候选。
              </p>
              <p>
                调查达到终态后自动完成；最迟于{" "}
                {dateTime(status.auto_finalize_at)} 按当时完整证据自动管理仓位。
              </p>
            </div>
            <div className="core-strategy-investigations__actions">
              <a href={`/public-opinion?target_date=${encodeURIComponent(targetDate)}`}>
                查看舆论采集
              </a>
            </div>
          </header>
          <dl>
            <div>
              <dt>待执行</dt>
              <dd>{status.opinion_jobs.pending}</dd>
            </div>
            <div>
              <dt>执行中</dt>
              <dd>{status.opinion_jobs.running}</dd>
            </div>
            <div>
              <dt>已完成</dt>
              <dd>{status.opinion_jobs.succeeded}</dd>
            </div>
            <div>
              <dt>失败或阻塞</dt>
              <dd>
                {status.opinion_jobs.failed + status.opinion_jobs.blocked}
              </dd>
            </div>
          </dl>
          <div className="core-strategy-targets">
            <span>舆论目标</span>
            {status.opinion_target_codes.length > 0 ? (
              <ol>
                {status.opinion_target_codes.map((code) => (
                  <li key={code}>{code}</li>
                ))}
              </ol>
            ) : (
              <p>当日没有需要调查的新开仓目标。</p>
            )}
          </div>
        </section>
      )}

      {day && (
        <>
          <section className="core-strategy-publication">
            <div>
              <span>可用现金</span>
              <strong>{money(displayedAvailableCash)}</strong>
            </div>
            <div>
              <span>当前持仓</span>
              <strong>{positions.length}</strong>
            </div>
            <div>
              <span>当日订单</span>
              <strong>{buyOrders.length + sellOrders.length}</strong>
            </div>
            <div>
              <span>最大持股数</span>
              <strong>{day.snapshot.entry_planning.maximum_positions}</strong>
            </div>
            <small>
              结果日 {day.snapshot.actual_date}；发布于{" "}
              {dateTime(day.published_at)}；所有订单均为日线导入后的模拟成交。
            </small>
          </section>

          <section className="core-strategy-table-section">
            <header>
              <div>
                <p className="section-kicker">机会判断</p>
                <h3>当日候选与开仓许可</h3>
              </div>
              <span>{day.snapshot.entry_planning.candidates.length} 只</span>
            </header>
            <div className="core-strategy-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>排名</th>
                    <th>股票</th>
                    <th>路径</th>
                    <th>等级</th>
                    <th>净盈亏比</th>
                    <th>目标仓位</th>
                    <th>基本面</th>
                    <th>结果</th>
                  </tr>
                </thead>
                <tbody>
                  {day.snapshot.entry_planning.candidates.map((candidate) => (
                    <tr key={candidate.code}>
                      <td>{candidate.preliminary_rank}</td>
                      <td>
                        <strong>{candidate.name}</strong>
                        <small>{candidate.code}</small>
                      </td>
                      <td>{routeLabels[candidate.route]}</td>
                      <td>{candidate.grade ?? "取消"}</td>
                      <td>
                        {candidate.net_reward_risk_ratio?.toFixed(2) ?? "—"}
                      </td>
                      <td>{percentage(candidate.position_fraction)}</td>
                      <td>{fundamentalLabels[candidate.fundamental_priority]}</td>
                      <td>
                        {candidate.open_allowed
                          ? "允许开仓"
                          : candidate.block_reasons.join("；") || "不允许开仓"}
                      </td>
                    </tr>
                  ))}
                  {day.snapshot.entry_planning.candidates.length === 0 && (
                    <tr>
                      <td colSpan={8}>当日没有进入最终判断的候选。</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="core-strategy-table-section">
            <header>
              <div>
                <p className="section-kicker">模拟成交</p>
                <h3>当日订单</h3>
              </div>
              <span>{buyOrders.length + sellOrders.length} 笔</span>
            </header>
            <div className="core-strategy-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>方向</th>
                    <th>股票</th>
                    <th>成交时点</th>
                    <th>成交价</th>
                    <th>股数</th>
                    <th>现金影响</th>
                    <th>依据</th>
                  </tr>
                </thead>
                <tbody>
                  {sellOrders.map((order) => (
                    <tr key={`sell-${order.code}`}>
                      <td>卖出</td>
                      <td>
                        <strong>{order.name}</strong>
                        <small>{order.code}</small>
                      </td>
                      <td>{order.execution_session === "open" ? "开盘" : "收盘"}</td>
                      <td>{order.execution_price.toFixed(2)}</td>
                      <td>{order.shares}</td>
                      <td>{money(order.net_cash)}</td>
                      <td>{reasonLabels[order.reason] ?? order.reason}</td>
                    </tr>
                  ))}
                  {buyOrders.map((order) => (
                    <tr key={`buy-${order.code}`}>
                      <td>买入</td>
                      <td>
                        <strong>{order.name}</strong>
                        <small>{order.code}</small>
                      </td>
                      <td>收盘</td>
                      <td>{order.execution_price.toFixed(2)}</td>
                      <td>{order.shares}</td>
                      <td>−{money(order.total_cash)}</td>
                      <td>{order.grade} 级分歧买入</td>
                    </tr>
                  ))}
                  {buyOrders.length + sellOrders.length === 0 && (
                    <tr>
                      <td colSpan={7}>当日没有模拟成交，空仓也是有效结果。</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="core-strategy-table-section">
            <header>
              <div>
                <p className="section-kicker">完整组合</p>
                <h3>收盘后持仓</h3>
              </div>
              <span>
                {portfolio ? `${positions.length} 只` : "沿用上一版完整组合"}
              </span>
            </header>
            <div className="core-strategy-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>股数</th>
                    <th>成本价</th>
                    <th>收盘标记</th>
                    <th>止损线</th>
                    <th>压力目标</th>
                    <th>利润保护</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((position) => {
                    const mark = markByCode.get(position.code);
                    return (
                      <tr key={position.code}>
                        <td>
                          <strong>{position.name}</strong>
                          <small>{position.code}</small>
                        </td>
                        <td>{position.shares}</td>
                        <td>{position.cost_price.toFixed(2)}</td>
                        <td>{mark?.price.toFixed(2) ?? "—"}</td>
                        <td>{position.stop_price.toFixed(2)}</td>
                        <td>{position.pressure_target?.toFixed(2) ?? "新高"}</td>
                        <td>{position.final_profit_line?.toFixed(2) ?? "未激活"}</td>
                        <td>
                          {position.pending_exit_reason
                            ? "待卖"
                            : "持有"}
                        </td>
                      </tr>
                    );
                  })}
                  {positions.length === 0 && (
                    <tr>
                      <td colSpan={8}>
                        {portfolio
                          ? "当前组合为空仓。"
                          : "持仓行情不完整，本日未覆盖上一版组合。"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {status.account && (
        <section className="core-strategy-reset-panel">
          <div>
            <p className="section-kicker">账户生命周期</p>
            <h3>清空全部并重新初始化</h3>
            <p>
              该操作会取消当前模拟账户、持仓和全部核心策略结果，
              之后系统停止自动运行，直到你重新输入初始资金。
            </p>
          </div>
          <button
            type="button"
            disabled={action !== null}
            onClick={() => void resetAccount()}
          >
            {action === "reset" ? "正在清空…" : "清空全部"}
          </button>
        </section>
      )}
    </div>
  );
}
