import { useEffect, useMemo, useState } from "react";
import { movementTone, signedPercentage } from "./marketFormatting";

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
  distribution: Array<{ label: string; count: number; tone: string }>;
  gainers: SecurityMarketView[];
  losers: SecurityMarketView[];
  heatmap: SecurityMarketView[];
  trend: TrendPoint[];
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
  return `${(value / 100_000_000).toFixed(1)} 亿`;
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
}: {
  title: string;
  rows: SecurityMarketView[];
}) {
  return (
    <article className="real-ranking-card">
      <h3>{title}</h3>
      <ol>
        {rows.map((row) => (
          <li key={row.code}>
            <span className="real-ranking-name">
              <strong>{row.name}</strong>
              <small>{row.code}</small>
            </span>
            <span>{row.close.toFixed(2)}</span>
            <strong className={`metric-value--${movementTone(row.change_pct)}`}>
              {signedPercentage(row.change_pct)}
            </strong>
          </li>
        ))}
      </ol>
    </article>
  );
}

export function RealMarketDashboard({ targetDate }: { targetDate: string }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

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

  return (
    <section className="real-market-dashboard" aria-labelledby="real-market-title" data-testid="real-market-dashboard">
      <header className="real-market-header">
        <div>
          <p className="section-kicker">AKShare · 真实行情</p>
          <h2 id="real-market-title">沪深主板市场状态</h2>
          <p>实际数据日 {data.actual_data_date} · 覆盖 {data.coverage_start} 至 {data.coverage_end}</p>
        </div>
        <span className="real-source-badge">真实数据 · 完整批次</span>
      </header>

      <div className="real-kpi-grid">
        <article>
          <span>{data.benchmark.name}</span>
          <strong>{data.benchmark.close.toFixed(3)}</strong>
          <small className={`metric-value--${movementTone(data.benchmark.change_pct)}`}>{signedPercentage(data.benchmark.change_pct)}</small>
        </article>
        <article>
          <span>上涨占比</span>
          <strong>{data.breadth.advancer_ratio.toFixed(2)}%</strong>
          <small>涨 {data.breadth.advancers} · 跌 {data.breadth.decliners} · 平 {data.breadth.unchanged}</small>
        </article>
        <article>
          <span>主板成交额</span>
          <strong>{hundredMillion(data.turnover_cny)}</strong>
          <small className={`metric-value--${movementTone(data.turnover_change_vs_20d_pct)}`}>较前 20 日 {signedPercentage(data.turnover_change_vs_20d_pct)}</small>
        </article>
        <article>
          <span>合格样本</span>
          <strong>{data.eligible_security_count.toLocaleString("zh-CN")}</strong>
          <small>沪深主板 · 已排除停牌与新股</small>
        </article>
      </div>

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
        <Ranking title="涨幅前十" rows={data.gainers} />
        <Ranking title="跌幅前十" rows={data.losers} />
      </div>

      <article className="real-heatmap-card">
        <div>
          <h3>成交活跃个股热力图</h3>
          <span>按成交额选取前 100 · 红涨绿跌</span>
        </div>
        <div className="real-heatmap" aria-label="成交额前一百个股涨跌热力图">
          {data.heatmap.map((stock) => (
            <div key={stock.code} style={{ backgroundColor: heatColor(stock.change_pct) }} title={`${stock.name} ${stock.code} ${signedPercentage(stock.change_pct)} · ${hundredMillion(stock.turnover_cny)}`}>
              <strong>{stock.name}</strong>
              <span>{signedPercentage(stock.change_pct)}</span>
            </div>
          ))}
        </div>
      </article>

      <p className="real-market-note">来源：AKShare 新浪个股日线与沪深 300 日线。板块和概念强弱需由核心策略定义，本模块不生成综合市场判断。</p>
    </section>
  );
}
