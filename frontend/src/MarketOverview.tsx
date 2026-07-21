import { movementTone, signedPercentage } from "./marketFormatting";

export type MarketOverviewData = {
  indices: Array<{ name: string; change_pct: number }>;
  breadth: {
    advancers: number;
    decliners: number;
    unchanged: number;
    advancer_ratio: number;
    decliner_ratio: number;
    unchanged_ratio: number;
  };
  limit_activity: {
    limit_up: number;
    limit_down: number;
    max_limit_up_streak: number;
  };
  turnover: {
    amount_cny: number;
    change_vs_20d_pct: number;
  };
  high_low: {
    new_high_20d: number;
    new_low_20d: number;
  };
  eligible_security_count: number;
};

type BreadthItemProps = {
  label: string;
  count: number;
  ratio: number;
  tone: "positive" | "negative" | "neutral";
  testId: string;
};

function BreadthItem({ label, count, ratio, tone, testId }: BreadthItemProps) {
  return (
    <div className="breadth-item">
      <span>{label}</span>
      <strong className={`metric-value metric-value--${tone}`} data-testid={testId}>
        {count} 只 · {ratio.toFixed(2)}%
      </strong>
    </div>
  );
}

export function MarketOverview({ data }: { data: MarketOverviewData }) {
  return (
    <section className="market-overview" aria-labelledby="market-overview-title">
      <header className="market-overview__header">
        <div>
          <p className="section-kicker">市场</p>
          <h2 id="market-overview-title">沪深主板市场概览</h2>
        </div>
        <div className="universe-count">
          <span>有效样本</span>
          <strong data-testid="eligible-security-count">
            {data.eligible_security_count} 只
          </strong>
        </div>
      </header>

      <div className="index-grid" aria-label="主要指数涨跌幅">
        {data.indices.map((index) => (
          <article className="index-card" key={index.name} data-testid={`index-${index.name}`}>
            <span>{index.name}</span>
            <strong className={`metric-value metric-value--${movementTone(index.change_pct)}`}>
              {signedPercentage(index.change_pct)}
            </strong>
          </article>
        ))}
      </div>

      <div className="market-metric-grid">
        <article className="market-metric-card market-metric-card--wide">
          <div className="metric-heading">
            <span>市场广度</span>
            <small>数量与占比</small>
          </div>
          <div className="breadth-grid">
            <BreadthItem
              label="上涨"
              count={data.breadth.advancers}
              ratio={data.breadth.advancer_ratio}
              tone="positive"
              testId="breadth-advancers"
            />
            <BreadthItem
              label="下跌"
              count={data.breadth.decliners}
              ratio={data.breadth.decliner_ratio}
              tone="negative"
              testId="breadth-decliners"
            />
            <BreadthItem
              label="平盘"
              count={data.breadth.unchanged}
              ratio={data.breadth.unchanged_ratio}
              tone="neutral"
              testId="breadth-unchanged"
            />
          </div>
        </article>

        <article className="market-metric-card">
          <div className="metric-heading">
            <span>涨跌停与连板</span>
            <small>当日</small>
          </div>
          <dl className="compact-metrics">
            <div>
              <dt>涨停</dt>
              <dd className="metric-value metric-value--positive" data-testid="limit-up-count">
                {data.limit_activity.limit_up} 只
              </dd>
            </div>
            <div>
              <dt>跌停</dt>
              <dd className="metric-value metric-value--negative" data-testid="limit-down-count">
                {data.limit_activity.limit_down} 只
              </dd>
            </div>
            <div>
              <dt>最高连板</dt>
              <dd className="metric-value" data-testid="limit-up-streak">
                {data.limit_activity.max_limit_up_streak} 板
              </dd>
            </div>
          </dl>
        </article>

        <article className="market-metric-card" data-testid="market-turnover">
          <div className="metric-heading">
            <span>成交活跃度</span>
            <small>沪深主板</small>
          </div>
          <strong className="turnover-value">
            {(data.turnover.amount_cny / 100_000_000).toFixed(2)} 亿元
          </strong>
          <p className={`turnover-change metric-value--${movementTone(data.turnover.change_vs_20d_pct)}`}>
            较近 20 日 {signedPercentage(data.turnover.change_vs_20d_pct)}
          </p>
        </article>

        <article className="market-metric-card">
          <div className="metric-heading">
            <span>近 20 日新高新低</span>
            <small>股票数量</small>
          </div>
          <dl className="compact-metrics compact-metrics--two">
            <div>
              <dt>新高</dt>
              <dd className="metric-value metric-value--positive" data-testid="new-high-20d">
                {data.high_low.new_high_20d} 只
              </dd>
            </div>
            <div>
              <dt>新低</dt>
              <dd className="metric-value metric-value--negative" data-testid="new-low-20d">
                {data.high_low.new_low_20d} 只
              </dd>
            </div>
          </dl>
        </article>
      </div>

      <p className="universe-note">
        统计范围仅含沪深主板；排除 ST（特别处理）、停牌、退市整理期及上市未满 20 个交易日的新股。
      </p>
    </section>
  );
}
