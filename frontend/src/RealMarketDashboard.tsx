import { useEffect, useMemo, useState } from "react";
import { movementTone, signedPercentage } from "./marketFormatting";
import type { InstrumentSelection } from "./KlineExplorer";

type SecurityMarketView = {
  code: string;
  name: string;
  close: number;
  change_pct: number;
  turnover_cny: number;
};

type TrendPoint = {
  date: string;
  benchmark_close: number;
  turnover_cny: number;
  advancer_ratio: number;
};

type RealMarketData = {
  source: string;
  requested_date: string;
  actual_data_date: string;
  coverage_start: string;
  coverage_end: string;
  benchmark: {
    name: string;
    close: number;
    change_pct: number;
  };
  eligible_security_count: number;
  turnover_cny: number;
  turnover_change_vs_20d_pct: number;
  breadth: {
    advancers: number;
    decliners: number;
    unchanged: number;
    advancer_ratio: number;
  };
  activity: {
    limit_up: number;
    limit_down: number;
    consecutive_limit_up: number;
    new_high_20d: number;
    new_low_20d: number;
  };
  distribution: Array<{ label: string; count: number; tone: string }>;
  gainers: SecurityMarketView[];
  losers: SecurityMarketView[];
  heatmap: SecurityMarketView[];
  trend: TrendPoint[];
};

type OverviewCategory =
  | "eligible"
  | "advancers"
  | "decliners"
  | "unchanged"
  | "turnover"
  | "limit_up"
  | "limit_down"
  | "consecutive_limit_up"
  | "new_high_20d"
  | "new_low_20d";

type OverviewSecurity = SecurityMarketView & {
  status: string[];
  sector_labels: string[];
  concept_labels: string[];
};

type OverviewPage = {
  requested_date: string;
  actual_data_date: string;
  category: OverviewCategory;
  category_label: string;
  total: number;
  page: number;
  page_size: number;
  items: OverviewSecurity[];
};

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; data: RealMarketData }
  | { kind: "empty" }
  | { kind: "error" };

function linePoints(values: number[], width = 1000, height = 190): string {
  if (values.length === 0) return "";
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = maximum - minimum || 1;
  return values
    .map((value, index) => {
      const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
      const y = height - ((value - minimum) / range) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function shortDate(value: string): string {
  return value.slice(5).replace("-", "/");
}

function hundredMillion(value: number): string {
  if (value < 100_000_000) {
    return `${Math.round(value / 10_000).toLocaleString("zh-CN")} 万`;
  }
  return `${(value / 100_000_000).toFixed(1)} 亿`;
}

function MetricCard({
  category,
  label,
  value,
  detail,
  tone = "neutral",
  active,
  onSelect,
}: {
  category: OverviewCategory;
  label: string;
  value: string;
  detail: string;
  tone?: "positive" | "negative" | "neutral";
  active: boolean;
  onSelect: (category: OverviewCategory) => void;
}) {
  return (
    <button
      type="button"
      className={`real-overview-card real-overview-card--${tone}${active ? " real-overview-card--active" : ""}`}
      aria-pressed={active}
      onClick={() => onSelect(category)}
      data-testid={`overview-card-${category}`}
    >
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </button>
  );
}

function OverviewDetails({
  targetDate,
  category,
  onClose,
  onSelectSecurity,
}: {
  targetDate: string;
  category: OverviewCategory;
  onClose: () => void;
  onSelectSecurity: (selection: InstrumentSelection) => void;
}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [query, setQuery] = useState("");
  const [data, setData] = useState<OverviewPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setPage(1);
    setQuery("");
  }, [category, targetDate]);

  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      async function load() {
        setLoading(true);
        setFailed(false);
        const params = new URLSearchParams({
          target_date: targetDate,
          category,
          page: String(page),
          page_size: String(pageSize),
          query,
        });
        try {
          const response = await fetch(`/api/market-data/overview-securities?${params}`, {
            signal: controller.signal,
          });
          if (!response.ok) throw new Error(`市场明细接口返回 ${response.status}`);
          setData((await response.json()) as OverviewPage);
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
          setFailed(true);
        } finally {
          if (!controller.signal.aborted) setLoading(false);
        }
      }
      void load();
    }, 180);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [category, page, pageSize, query, targetDate]);

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));

  return (
    <article className="overview-details" data-testid="overview-details">
      <header>
        <div>
          <p className="section-kicker">指标明细</p>
          <h3>{data?.category_label ?? "正在读取…"}</h3>
          <span>{data ? `共 ${data.total.toLocaleString("zh-CN")} 只 · 数据日 ${data.actual_data_date}` : "读取真实股票列表"}</span>
        </div>
        <div className="overview-detail-actions">
          <label>
            <span>搜索股票</span>
            <input
              type="search"
              value={query}
              placeholder="代码或名称"
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(1);
              }}
            />
          </label>
          <label>
            <span>每页</span>
            <select
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
            >
              <option value={50}>50 只</option>
              <option value={100}>100 只</option>
            </select>
          </label>
          <button type="button" onClick={onClose}>收起明细</button>
        </div>
      </header>
      {failed ? (
        <p className="overview-detail-message overview-detail-message--error">股票明细读取失败，请检查本机服务。</p>
      ) : loading && data === null ? (
        <p className="overview-detail-message">正在读取真实行情明细…</p>
      ) : (
        <div className={`overview-table-wrap${loading ? " overview-table-wrap--loading" : ""}`}>
          <table>
            <thead>
              <tr><th>代码</th><th>名称</th><th>收盘价</th><th>涨跌幅</th><th>成交额</th><th>状态</th><th>板块 / 概念</th></tr>
            </thead>
            <tbody>
              {data?.items.map((stock) => (
                <tr key={stock.code}>
                  <td><button type="button" className="overview-stock-button" onClick={() => onSelectSecurity({ instrumentType: "stock", code: stock.code, name: stock.name })}>{stock.code}</button></td>
                  <td>{stock.name}</td>
                  <td>{stock.close.toFixed(2)}</td>
                  <td className={`metric-value--${movementTone(stock.change_pct)}`}>{signedPercentage(stock.change_pct)}</td>
                  <td>{hundredMillion(stock.turnover_cny)}</td>
                  <td><div className="overview-status-list">{stock.status.map((status) => <span key={status}>{status}</span>)}</div></td>
                  <td className="overview-strategy-pending">
                    {stock.sector_labels.length || stock.concept_labels.length
                      ? [...stock.sector_labels, ...stock.concept_labels].join(" · ")
                      : "待策略定义"}
                  </td>
                </tr>
              ))}
              {data?.items.length === 0 && <tr><td colSpan={7} className="overview-empty-row">没有匹配的股票。</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <footer>
        <span>第 {Math.min(page, totalPages)} / {totalPages} 页</span>
        <div>
          <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页</button>
          <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage((value) => value + 1)}>下一页</button>
        </div>
      </footer>
    </article>
  );
}

function heatColor(change: number): string {
  const intensity = Math.min(Math.abs(change) / 10, 1);
  if (change > 0) return `rgba(224, 67, 78, ${0.2 + intensity * 0.68})`;
  if (change < 0) return `rgba(33, 171, 119, ${0.2 + intensity * 0.68})`;
  return "rgba(92, 108, 132, 0.42)";
}

function Ranking({
  title,
  rows,
  onSelect,
}: {
  title: string;
  rows: SecurityMarketView[];
  onSelect: (selection: InstrumentSelection) => void;
}) {
  return (
    <article className="real-ranking-card">
      <h3>{title}</h3>
      <ol>
        {rows.map((row) => (
          <li key={row.code}>
            <button type="button" className="real-stock-link" onClick={() => onSelect({ instrumentType: "stock", code: row.code, name: row.name })}>
            <span className="real-ranking-name">
              <strong>{row.name}</strong>
              <small>{row.code}</small>
            </span>
            <span>{row.close.toFixed(2)}</span>
            <strong className={`metric-value--${movementTone(row.change_pct)}`}>
              {signedPercentage(row.change_pct)}
            </strong>
            </button>
          </li>
        ))}
      </ol>
    </article>
  );
}

export function RealMarketDashboard({
  targetDate,
  onSelectSecurity,
}: {
  targetDate: string;
  onSelectSecurity: (selection: InstrumentSelection) => void;
}) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedCategory, setSelectedCategory] = useState<OverviewCategory | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setState({ kind: "loading" });
      try {
        const response = await fetch(
          `/api/market-data/dashboard?target_date=${encodeURIComponent(targetDate)}`,
          { signal: controller.signal },
        );
        if (response.status === 404) {
          setState({ kind: "empty" });
          return;
        }
        if (!response.ok) throw new Error(`真实行情接口返回 ${response.status}`);
        setState({ kind: "ready", data: (await response.json()) as RealMarketData });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({ kind: "error" });
      }
    }
    void load();
    return () => controller.abort();
  }, [targetDate]);

  const data = state.kind === "ready" ? state.data : null;
  const benchmarkPoints = useMemo(
    () => linePoints(data?.trend.map((point) => point.benchmark_close) ?? []),
    [data],
  );
  const turnoverPoints = useMemo(
    () => linePoints(data?.trend.map((point) => point.turnover_cny) ?? []),
    [data],
  );

  if (state.kind === "loading") {
    return <section className="real-market-dashboard real-market-message">正在读取 AKShare 真实行情…</section>;
  }
  if (state.kind === "empty") {
    return <section className="real-market-dashboard real-market-message">目标日期前尚无 AKShare 真实历史数据。</section>;
  }
  if (state.kind === "error" || data === null) {
    return <section className="real-market-dashboard real-market-message real-market-message--error">真实行情可视化读取失败，请检查本机服务。</section>;
  }

  const distributionMaximum = Math.max(...data.distribution.map((item) => item.count), 1);
  const trendStart = data.trend[0];
  const trendEnd = data.trend[data.trend.length - 1];
  function selectCategory(category: OverviewCategory) {
    setSelectedCategory((current) => current === category ? null : category);
  }

  return (
    <section className="real-market-dashboard" aria-labelledby="real-market-title" data-testid="real-market-dashboard">
      <header className="real-market-header">
        <div>
          <p className="section-kicker">AKShare · 真实行情</p>
          <h2 id="real-market-title">沪深主板市场状态</h2>
          <p>实际数据日 {data.actual_data_date} · 覆盖 {data.coverage_start} 至 {data.coverage_end}</p>
        </div>
        <div className="real-header-summary">
          <div>
            <span>{data.benchmark.name}</span>
            <strong>{data.benchmark.close.toFixed(3)}</strong>
            <small className={`metric-value--${movementTone(data.benchmark.change_pct)}`}>{signedPercentage(data.benchmark.change_pct)}</small>
          </div>
          <span className="real-source-badge">真实数据 · 完整批次</span>
        </div>
      </header>

      <div className="real-overview-grid" aria-label="沪深主板真实市场概览">
        <MetricCard category="eligible" label="有效样本" value={`${data.eligible_security_count.toLocaleString("zh-CN")} 只`} detail="当前可用主板股票池" active={selectedCategory === "eligible"} onSelect={selectCategory} />
        <MetricCard category="advancers" label="上涨" value={`${data.breadth.advancers.toLocaleString("zh-CN")} 只`} detail={`占样本 ${data.breadth.advancer_ratio.toFixed(2)}%`} tone="positive" active={selectedCategory === "advancers"} onSelect={selectCategory} />
        <MetricCard category="decliners" label="下跌" value={`${data.breadth.decliners.toLocaleString("zh-CN")} 只`} detail="按当日收盘涨跌统计" tone="negative" active={selectedCategory === "decliners"} onSelect={selectCategory} />
        <MetricCard category="unchanged" label="平盘" value={`${data.breadth.unchanged.toLocaleString("zh-CN")} 只`} detail="收盘涨跌幅为零" active={selectedCategory === "unchanged"} onSelect={selectCategory} />
        <MetricCard category="turnover" label="主板成交额" value={hundredMillion(data.turnover_cny)} detail={`较前 20 日 ${signedPercentage(data.turnover_change_vs_20d_pct)}`} active={selectedCategory === "turnover"} onSelect={selectCategory} />
        <MetricCard category="limit_up" label="涨停" value={`${data.activity.limit_up.toLocaleString("zh-CN")} 只`} detail="普通主板 10% 价格限制" tone="positive" active={selectedCategory === "limit_up"} onSelect={selectCategory} />
        <MetricCard category="limit_down" label="跌停" value={`${data.activity.limit_down.toLocaleString("zh-CN")} 只`} detail="按实际跌停价判断" tone="negative" active={selectedCategory === "limit_down"} onSelect={selectCategory} />
        <MetricCard category="consecutive_limit_up" label="连板" value={`${data.activity.consecutive_limit_up.toLocaleString("zh-CN")} 只`} detail="连续至少两个交易日涨停" tone="positive" active={selectedCategory === "consecutive_limit_up"} onSelect={selectCategory} />
        <MetricCard category="new_high_20d" label="20 日新高" value={`${data.activity.new_high_20d.toLocaleString("zh-CN")} 只`} detail="收盘价达到 20 日最高" tone="positive" active={selectedCategory === "new_high_20d"} onSelect={selectCategory} />
        <MetricCard category="new_low_20d" label="20 日新低" value={`${data.activity.new_low_20d.toLocaleString("zh-CN")} 只`} detail="收盘价达到 20 日最低" tone="negative" active={selectedCategory === "new_low_20d"} onSelect={selectCategory} />
      </div>

      {selectedCategory && (
        <OverviewDetails
          targetDate={targetDate}
          category={selectedCategory}
          onClose={() => setSelectedCategory(null)}
          onSelectSecurity={onSelectSecurity}
        />
      )}

      <div className="real-trend-grid">
        <article className="real-chart-card">
          <div><h3>沪深 300 收盘走势</h3><span>{trendStart ? shortDate(trendStart.date) : "—"} — {trendEnd ? shortDate(trendEnd.date) : "—"}</span></div>
          <svg viewBox="0 0 1000 190" preserveAspectRatio="none" aria-label="沪深 300 过去一年收盘走势">
            <polyline className="real-line real-line--benchmark" points={benchmarkPoints} />
          </svg>
        </article>
        <article className="real-chart-card">
          <div><h3>沪深主板成交额走势</h3><span>单位 · 元</span></div>
          <svg viewBox="0 0 1000 190" preserveAspectRatio="none" aria-label="沪深主板过去一年成交额走势">
            <polyline className="real-line real-line--turnover" points={turnoverPoints} />
          </svg>
        </article>
      </div>

      <div className="real-analysis-grid">
        <article className="real-distribution-card">
          <h3>个股涨跌幅分布</h3>
          <div className="real-distribution-list">
            {data.distribution.map((item) => (
              <div key={item.label}>
                <span>{item.label}</span>
                <div><i className={`distribution-${item.tone}`} style={{ width: `${(item.count / distributionMaximum) * 100}%` }} /></div>
                <strong>{item.count}</strong>
              </div>
            ))}
          </div>
        </article>
        <Ranking title="涨幅前十" rows={data.gainers} onSelect={onSelectSecurity} />
        <Ranking title="跌幅前十" rows={data.losers} onSelect={onSelectSecurity} />
      </div>

      <article className="real-heatmap-card">
        <div>
          <h3>成交活跃个股热力图</h3>
          <span>按成交额选取前 100 · 红涨绿跌</span>
        </div>
        <div className="real-heatmap" aria-label="成交额前一百个股涨跌热力图">
          {data.heatmap.map((stock) => (
            <button type="button" key={stock.code} onClick={() => onSelectSecurity({ instrumentType: "stock", code: stock.code, name: stock.name })} style={{ backgroundColor: heatColor(stock.change_pct) }} title={`${stock.name} ${stock.code} ${signedPercentage(stock.change_pct)} · ${hundredMillion(stock.turnover_cny)}`}>
              <strong>{stock.name}</strong>
              <span>{signedPercentage(stock.change_pct)}</span>
            </button>
          ))}
        </div>
      </article>

      <p className="real-market-note">来源：AKShare 新浪个股日线与沪深 300 日线。历史统计采用当前可用股票池回看口径；新股按上市未满 60 个交易日排除。板块和概念强弱需由核心策略定义，本模块不生成综合市场判断。</p>
    </section>
  );
}
