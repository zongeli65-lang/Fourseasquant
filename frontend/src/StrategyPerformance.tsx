import { useMemo, useState } from "react";
import { movementTone, signedPercentage } from "./marketFormatting";

type PerformancePoint = {
  date: string;
  strategy_nav: number;
  benchmark_nav: number;
  excess_nav: number;
  drawdown_pct: number;
};

type PerformanceStatistics = {
  cumulative_return_pct: number;
  annualized_return_pct: number;
  max_drawdown_pct: number;
  current_drawdown_pct: number;
  sharpe_ratio: number;
  win_rate_pct: number;
};

export type StrategyPerformanceData = {
  is_demo: boolean;
  benchmark_label: string;
  inception_date: string;
  daily_summary: {
    strategy_return_pct: number;
    benchmark_return_pct: number;
    excess_return_pct: number;
  };
  statistics: PerformanceStatistics;
  range_options: Array<{
    key: string;
    label: string;
    trading_day_count: number | null;
  }>;
  range_statistics: Record<string, PerformanceStatistics>;
  points: PerformancePoint[];
};

function chartPoints(
  values: number[],
  width: number,
  height: number,
  minimum = Math.min(...values),
  maximum = Math.max(...values),
): string {
  const range = maximum - minimum || 1;
  return values
    .map((value, index) => {
      const x = values.length === 1 ? 0 : (index / (values.length - 1)) * width;
      const y = height - ((value - minimum) / range) * height;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

function ReturnCard({
  label,
  value,
  testId,
}: {
  label: string;
  value: number;
  testId: string;
}) {
  return (
    <article className="strategy-return-card">
      <span>{label}</span>
      <strong className={`metric-value--${movementTone(value)}`} data-testid={testId}>
        {signedPercentage(value)}
      </strong>
    </article>
  );
}

export function StrategyPerformance({ data }: { data: StrategyPerformanceData }) {
  const defaultRange = data.range_options[0] ?? {
    key: "all",
    label: "成立以来",
    trading_day_count: null,
  };
  const [range, setRange] = useState(defaultRange.key);
  const selectedRange =
    data.range_options.find((item) => item.key === range) ?? defaultRange;
  const selectedStatistics =
    data.range_statistics[selectedRange.key] ?? data.statistics;
  const points = useMemo(
    () =>
      selectedRange.trading_day_count
        ? data.points.slice(-selectedRange.trading_day_count)
        : data.points,
    [data.points, selectedRange.trading_day_count],
  );
  const strategyValues = points.map((point) => point.strategy_nav);
  const benchmarkValues = points.map((point) => point.benchmark_nav);
  const excessValues = points.map((point) => point.excess_nav);
  const navValues = [...strategyValues, ...benchmarkValues, ...excessValues];
  const navMinimum = Math.min(...navValues);
  const navMaximum = Math.max(...navValues);
  const strategyLine = chartPoints(strategyValues, 1000, 180, navMinimum, navMaximum);
  const benchmarkLine = chartPoints(benchmarkValues, 1000, 180, navMinimum, navMaximum);
  const excessLine = chartPoints(excessValues, 1000, 180, navMinimum, navMaximum);
  const drawdownLine = chartPoints(points.map((point) => point.drawdown_pct), 1000, 120);

  const statistics = [
    ["累计收益", signedPercentage(selectedStatistics.cumulative_return_pct)],
    ["年化收益", signedPercentage(selectedStatistics.annualized_return_pct)],
    ["最大回撤", signedPercentage(selectedStatistics.max_drawdown_pct)],
    ["当前回撤", signedPercentage(selectedStatistics.current_drawdown_pct)],
    ["夏普比率", selectedStatistics.sharpe_ratio.toFixed(2)],
    ["胜率", `${selectedStatistics.win_rate_pct.toFixed(2)}%`],
  ];

  return (
    <section className="strategy-performance" aria-labelledby="strategy-performance-title">
      <header className="strategy-performance__header">
        <div>
          <p className="section-kicker">策略</p>
          <h2 id="strategy-performance-title">策略业绩复盘</h2>
        </div>
        <div className="strategy-header-meta">
          <span className="demo-badge">演示策略数据</span>
          <span>基准 · {data.benchmark_label}</span>
          <span>成立 · {data.inception_date}</span>
        </div>
      </header>

      <div className="strategy-return-grid">
        <ReturnCard
          label="当日策略收益"
          value={data.daily_summary.strategy_return_pct}
          testId="strategy-daily-return"
        />
        <ReturnCard
          label={`当日${data.benchmark_label}收益`}
          value={data.daily_summary.benchmark_return_pct}
          testId="benchmark-daily-return"
        />
        <ReturnCard
          label="当日超额收益"
          value={data.daily_summary.excess_return_pct}
          testId="excess-daily-return"
        />
      </div>

      <div className="strategy-range-row">
        <div className="segmented-control" aria-label="策略时间范围">
          {data.range_options.map((item) => (
            <button
              type="button"
              key={item.key}
              aria-pressed={range === item.key}
              onClick={() => setRange(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <span data-testid="selected-range">{selectedRange.label}</span>
      </div>

      <article className="chart-card" data-testid="nav-chart">
        <div className="chart-heading">
          <h3>净值与超额走势</h3>
          <div className="chart-legend">
            <span className="legend-strategy">策略净值</span>
            <span className="legend-benchmark">基准净值</span>
            <span className="legend-excess">超额净值</span>
          </div>
        </div>
        <svg viewBox="0 0 1000 180" preserveAspectRatio="none" aria-label="策略、基准和超额净值曲线">
          <polyline className="line-strategy" points={strategyLine} />
          <polyline className="line-benchmark" points={benchmarkLine} />
          <polyline className="line-excess" points={excessLine} />
        </svg>
      </article>

      <div className="strategy-bottom-grid">
        <article className="chart-card" data-testid="drawdown-chart">
          <div className="chart-heading">
            <h3>策略回撤</h3>
            <span>{signedPercentage(selectedStatistics.current_drawdown_pct)}</span>
          </div>
          <svg viewBox="0 0 1000 120" preserveAspectRatio="none" aria-label="策略回撤曲线">
            <polyline className="line-drawdown" points={drawdownLine} />
          </svg>
        </article>
        <article className="strategy-statistics" data-testid="strategy-statistics">
          <h3>核心统计</h3>
          <dl>
            {statistics.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </article>
      </div>
    </section>
  );
}
