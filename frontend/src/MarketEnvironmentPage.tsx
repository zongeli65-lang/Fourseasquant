import { useEffect, useMemo, useState } from "react";

type TrendState = "rising" | "falling" | "sideways";
type Direction = "bullish" | "bearish" | "neutral";

type IndexEvidence = {
  code: string;
  name: string;
  close: number;
  change_pct: number;
  fast_direction: Direction;
  fast_bull_votes: number;
  fast_bear_votes: number;
  return_3d_standardized: number | null;
  slope_5d_standardized: number | null;
  breakout_10d: boolean;
  breakdown_10d: boolean;
  slow_bull_votes: number;
  slow_bear_votes: number;
  return_10d_standardized: number | null;
  slope_10d_standardized: number | null;
  swing_structure: Direction;
  breakout_held_2d: boolean;
  breakdown_held_2d: boolean;
};

type Warning = {
  warning_id: string;
  warning_type: string;
  direction: "risk" | "support";
  index_name: string;
  trigger_date: string;
  description: string;
  invalidation_level: number;
};

type MarketEnvironmentSnapshot = {
  actual_data_date: string;
  rules_version: string;
  trend_id: string;
  trend_state: TrendState;
  trend_changed: boolean;
  trend_start_date: string;
  validation_state: "pending" | "validated" | "not_required";
  validation_deadline: string | null;
  fast_bull_streak: number;
  fast_bear_streak: number;
  sideways_streak: number;
  extreme_decline: boolean;
  indices: IndexEvidence[];
  breadth: {
    state: "strong" | "neutral" | "weak";
    eligible_count: number;
    advancers: number;
    decliners: number;
    unchanged: number;
    advancer_ratio: number;
    advancer_ratio_5d_average: number;
    positive_breadth_days_5d: number;
    new_high_20d: number;
    new_low_20d: number;
    high_low_ratio: number;
    strong_votes: number;
    weak_votes: number;
  };
  capacity: {
    state: "abundant" | "normal" | "insufficient";
    turnover_cny: number;
    turnover_20d_median_cny: number;
    ratio_to_20d_median: number;
  };
  cost_pressure: {
    lookback_days: number;
    upper_trapped_pressure_pct: number;
    lower_profit_pressure_pct: number;
    display_only: boolean;
    explanation: string;
  };
  warnings: Warning[];
  reasons: string[];
  data_sources: string[];
};

type HistoryResponse = {
  requested_end_date: string;
  rules_version: string;
  items: MarketEnvironmentSnapshot[];
};

type LoadState =
  | { kind: "loading" }
  | {
      kind: "ready";
      snapshot: MarketEnvironmentSnapshot;
      history: MarketEnvironmentSnapshot[];
    }
  | { kind: "empty"; message: string }
  | { kind: "error" };

type TrendRun = {
  state: TrendState;
  startDate: string;
  endDate: string;
  tradingDays: number;
};

type MonthGroup = {
  key: string;
  label: string;
  tradingDays: number;
};

const trendLabels: Record<TrendState, string> = {
  rising: "上升",
  falling: "下降",
  sideways: "震荡",
};

const directionLabels: Record<Direction, string> = {
  bullish: "快速看涨",
  bearish: "快速看跌",
  neutral: "快速中性",
};

const breadthLabels = {
  strong: "广度强",
  neutral: "广度中性",
  weak: "广度弱",
} as const;

const capacityLabels = {
  abundant: "容量充沛",
  normal: "容量正常",
  insufficient: "容量不足",
} as const;

function percentage(value: number): string {
  return `${value.toFixed(2)}%`;
}

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function money(value: number): string {
  return `${(value / 1_0000_0000).toFixed(0)}亿元`;
}

function validationCopy(snapshot: MarketEnvironmentSnapshot): string {
  if (snapshot.validation_state === "validated") return "慢速结构已验证";
  if (snapshot.validation_state === "pending") {
    return `等待慢速验证${snapshot.validation_deadline ? ` · 截止 ${snapshot.validation_deadline}` : ""}`;
  }
  return "当前状态无需慢速验证";
}

function oneYearEarlier(isoDate: string): string {
  const year = Number(isoDate.slice(0, 4));
  const month = Number(isoDate.slice(5, 7));
  const day = Number(isoDate.slice(8, 10));
  const lastDay = new Date(Date.UTC(year - 1, month, 0)).getUTCDate();
  return [
    String(year - 1).padStart(4, "0"),
    String(month).padStart(2, "0"),
    String(Math.min(day, lastDay)).padStart(2, "0"),
  ].join("-");
}

function buildTrendRuns(items: MarketEnvironmentSnapshot[]): TrendRun[] {
  const runs: TrendRun[] = [];
  for (const item of items) {
    const current = runs.at(-1);
    if (current?.state === item.trend_state) {
      current.endDate = item.actual_data_date;
      current.tradingDays += 1;
      continue;
    }
    runs.push({
      state: item.trend_state,
      startDate: item.actual_data_date,
      endDate: item.actual_data_date,
      tradingDays: 1,
    });
  }
  return runs;
}

function buildMonthGroups(items: MarketEnvironmentSnapshot[]): MonthGroup[] {
  const groups: MonthGroup[] = [];
  for (const item of items) {
    const key = item.actual_data_date.slice(0, 7);
    const current = groups.at(-1);
    if (current?.key === key) {
      current.tradingDays += 1;
      continue;
    }
    const [year, month] = key.split("-");
    groups.push({
      key,
      label: month === "01" ? `${year}年1月` : `${Number(month)}月`,
      tradingDays: 1,
    });
  }
  return groups;
}

export function MarketEnvironmentPage({ targetDate }: { targetDate: string }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [refreshing, setRefreshing] = useState(false);

  async function load(signal?: AbortSignal) {
    setState({ kind: "loading" });
    try {
      const [snapshotResponse, historyResponse] = await Promise.all([
        fetch(`/api/market-environment?target_date=${encodeURIComponent(targetDate)}`, {
          signal,
        }),
        fetch(
          `/api/market-environment/history?end_date=${encodeURIComponent(targetDate)}&limit=320`,
          { signal },
        ),
      ]);
      if (snapshotResponse.status === 404) {
        const payload = (await snapshotResponse.json()) as { detail?: string };
        setState({ kind: "empty", message: payload.detail ?? "尚无市场环境快照" });
        return;
      }
      if (!snapshotResponse.ok || !historyResponse.ok) {
        throw new Error("市场环境接口读取失败");
      }
      const snapshot = (await snapshotResponse.json()) as MarketEnvironmentSnapshot;
      const history = (await historyResponse.json()) as HistoryResponse;
      setState({ kind: "ready", snapshot, history: history.items });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setState({ kind: "error" });
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [targetDate]);

  async function refresh() {
    setRefreshing(true);
    try {
      const response = await fetch("/api/market-environment/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_date: targetDate }),
      });
      if (!response.ok) throw new Error("刷新失败");
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  const recentChanges = useMemo(
    () =>
      state.kind === "ready"
        ? state.history.filter((item) => item.trend_changed).slice(-8).reverse()
        : [],
    [state],
  );
  const yearHistory = useMemo(() => {
    if (state.kind !== "ready") return [];
    const startDate = oneYearEarlier(targetDate);
    return state.history.filter((item) => item.actual_data_date >= startDate);
  }, [state, targetDate]);
  const yearCounts = useMemo(
    () =>
      yearHistory.reduce<Record<TrendState, number>>(
        (counts, item) => {
          counts[item.trend_state] += 1;
          return counts;
        },
        { rising: 0, falling: 0, sideways: 0 },
      ),
    [yearHistory],
  );
  const yearRuns = useMemo(() => buildTrendRuns(yearHistory), [yearHistory]);
  const monthGroups = useMemo(() => buildMonthGroups(yearHistory), [yearHistory]);

  if (state.kind === "loading") {
    return <section className="market-environment-empty">正在读取机械市场环境…</section>;
  }
  if (state.kind === "error") {
    return (
      <section className="market-environment-empty market-environment-empty--error">
        市场环境读取失败，请确认本地后端状态。
      </section>
    );
  }
  if (state.kind === "empty") {
    return (
      <section className="market-environment-empty">
        <div>
          <p className="section-kicker">独立机械模块</p>
          <h2>尚无市场环境快照</h2>
          <p>{state.message}</p>
        </div>
        <button type="button" disabled={refreshing} onClick={() => void refresh()}>
          {refreshing ? "正在生成…" : "生成目标日期快照"}
        </button>
      </section>
    );
  }

  const { snapshot } = state;
  return (
    <div className="market-environment-page">
      <section className={`market-environment-hero market-environment-hero--${snapshot.trend_state}`}>
        <div>
          <p className="section-kicker">机械判断 · 与个股技术分独立</p>
          <h2>{trendLabels[snapshot.trend_state]}环境</h2>
          <p>
            实际数据日 {snapshot.actual_data_date} · 本轮始于 {snapshot.trend_start_date}
          </p>
          <span className="market-environment-validation">{validationCopy(snapshot)}</span>
        </div>
        <div className="market-environment-hero__state" aria-label="当前市场环境">
          <span>{trendLabels[snapshot.trend_state]}</span>
          <small>{snapshot.rules_version}</small>
        </div>
        <button type="button" disabled={refreshing} onClick={() => void refresh()}>
          {refreshing ? "正在重算…" : "重新生成"}
        </button>
      </section>

      <section className="market-environment-index-grid" aria-label="指数快速与慢速证据">
        {snapshot.indices.map((index) => (
          <article key={index.code} className={`market-index-card market-index-card--${index.fast_direction}`}>
            <header>
              <div>
                <span>{index.code}</span>
                <h3>{index.name}</h3>
              </div>
              <strong>{index.close.toFixed(2)}</strong>
              <small>{signed(index.change_pct)}</small>
            </header>
            <div className="market-index-card__direction">
              <b>{directionLabels[index.fast_direction]}</b>
              <span>
                看涨 {index.fast_bull_votes}/3 · 看跌 {index.fast_bear_votes}/3
              </span>
            </div>
            <dl>
              <div>
                <dt>3日标准化收益</dt>
                <dd>{index.return_3d_standardized?.toFixed(2) ?? "—"}</dd>
              </div>
              <div>
                <dt>5日标准化斜率</dt>
                <dd>{index.slope_5d_standardized?.toFixed(2) ?? "—"}</dd>
              </div>
              <div>
                <dt>10日突破 / 跌破</dt>
                <dd>{index.breakout_10d ? "突破" : index.breakdown_10d ? "跌破" : "未触发"}</dd>
              </div>
              <div>
                <dt>慢速结构</dt>
                <dd>
                  涨 {index.slow_bull_votes}/3 · 跌 {index.slow_bear_votes}/3
                </dd>
              </div>
            </dl>
          </article>
        ))}
      </section>

      <section className="market-environment-factors">
        <article>
          <p className="section-kicker">市场广度</p>
          <h3>{breadthLabels[snapshot.breadth.state]}</h3>
          <strong>{percentage(snapshot.breadth.advancer_ratio)}</strong>
          <span>当日上涨家数占比</span>
          <dl>
            <div><dt>上涨 / 下跌</dt><dd>{snapshot.breadth.advancers} / {snapshot.breadth.decliners}</dd></div>
            <div><dt>5日平均</dt><dd>{percentage(snapshot.breadth.advancer_ratio_5d_average)}</dd></div>
            <div><dt>20日新高 / 新低</dt><dd>{snapshot.breadth.new_high_20d} / {snapshot.breadth.new_low_20d}</dd></div>
          </dl>
        </article>
        <article>
          <p className="section-kicker">成交容量</p>
          <h3>{capacityLabels[snapshot.capacity.state]}</h3>
          <strong>{money(snapshot.capacity.turnover_cny)}</strong>
          <span>主板成交额</span>
          <dl>
            <div><dt>20日中位数</dt><dd>{money(snapshot.capacity.turnover_20d_median_cny)}</dd></div>
            <div><dt>容量比例</dt><dd>{percentage(snapshot.capacity.ratio_to_20d_median * 100)}</dd></div>
          </dl>
        </article>
        <article>
          <p className="section-kicker">成交成本压力代理</p>
          <h3>仅展示，不改判</h3>
          <strong>{percentage(snapshot.cost_pressure.upper_trapped_pressure_pct)}</strong>
          <span>上方成交压力代理</span>
          <dl>
            <div><dt>下方获利压力代理</dt><dd>{percentage(snapshot.cost_pressure.lower_profit_pressure_pct)}</dd></div>
            <div><dt>观察窗口</dt><dd>{snapshot.cost_pressure.lookback_days}个交易日</dd></div>
          </dl>
        </article>
      </section>

      <section className="market-environment-detail-grid">
        <article className="market-environment-reasons">
          <header>
            <p className="section-kicker">状态依据</p>
            <h3>为什么是{trendLabels[snapshot.trend_state]}</h3>
          </header>
          <ul>
            {snapshot.reasons.map((reason) => <li key={reason}>{reason}</li>)}
          </ul>
          <small>
            快速看涨连续 {snapshot.fast_bull_streak} 日 · 快速看跌连续{" "}
            {snapshot.fast_bear_streak} 日 · 中性连续 {snapshot.sideways_streak} 日
          </small>
        </article>
        <article className="market-environment-warnings">
          <header>
            <p className="section-kicker">独立技术提示</p>
            <h3>指数形态与背离</h3>
          </header>
          {snapshot.warnings.length ? (
            <ul>
              {snapshot.warnings.map((warning) => (
                <li key={warning.warning_id} data-tone={warning.direction}>
                  <strong>{warning.index_name} · {warning.warning_type}</strong>
                  <span>{warning.description}</span>
                  <small>触发 {warning.trigger_date} · 否定位置 {warning.invalidation_level.toFixed(2)}</small>
                </li>
              ))}
            </ul>
          ) : (
            <p>当前没有仍然有效的指数形态或RSI背离提示。</p>
          )}
        </article>
      </section>

      <section className="market-environment-year">
        <header>
          <div>
            <p className="section-kicker">最近一年 · 每个交易日</p>
            <h3>市场环境历史标注</h3>
            <small>
              {yearHistory.at(0)?.actual_data_date ?? "—"} 至{" "}
              {yearHistory.at(-1)?.actual_data_date ?? "—"} · 共 {yearHistory.length} 个交易日
            </small>
          </div>
          <div className="market-environment-year__legend" aria-label="市场环境颜色说明">
            <span data-state="rising">上涨</span>
            <span data-state="falling">下跌</span>
            <span data-state="sideways">震荡</span>
          </div>
        </header>

        <div className="market-environment-year__summary">
          {(["rising", "falling", "sideways"] as TrendState[]).map((trendState) => (
            <article key={trendState} data-state={trendState}>
              <span>{trendLabels[trendState]}</span>
              <strong>{yearCounts[trendState]}日</strong>
              <small>
                {yearHistory.length
                  ? percentage((yearCounts[trendState] / yearHistory.length) * 100)
                  : "—"}
              </small>
            </article>
          ))}
        </div>

        <div
          className="market-environment-year__track"
          role="img"
          aria-label={`最近一年${yearHistory.length}个交易日的上涨、下跌和震荡状态`}
        >
          {yearHistory.map((item) => (
            <span
              key={item.actual_data_date}
              data-state={item.trend_state}
              title={`${item.actual_data_date} · ${trendLabels[item.trend_state]}`}
            />
          ))}
        </div>
        <div className="market-environment-year__months" aria-hidden="true">
          {monthGroups.map((month) => (
            <span key={month.key} style={{ flexGrow: month.tradingDays }}>
              {month.label}
            </span>
          ))}
        </div>

        <div className="market-environment-year__periods">
          <h4>连续状态区间</h4>
          <div>
            {[...yearRuns].reverse().map((run) => (
              <article
                key={`${run.startDate}-${run.endDate}-${run.state}`}
                data-state={run.state}
              >
                <strong>{trendLabels[run.state]}</strong>
                <span>
                  {run.startDate === run.endDate
                    ? run.startDate
                    : `${run.startDate} — ${run.endDate}`}
                </span>
                <small>{run.tradingDays}个交易日</small>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="market-environment-history">
        <header>
          <div>
            <p className="section-kicker">不可回写历史</p>
            <h3>最近状态切换</h3>
          </div>
          <small>目标日期之后的数据不会修改旧快照</small>
        </header>
        <div>
          {recentChanges.map((item) => (
            <article key={`${item.actual_data_date}-${item.trend_id}`} data-state={item.trend_state}>
              <time>{item.actual_data_date}</time>
              <strong>{trendLabels[item.trend_state]}</strong>
              <span>{item.reasons[0]}</span>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
