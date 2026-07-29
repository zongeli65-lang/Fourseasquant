import { useMemo, useState } from "react";
import { movementTone, signedPercentage } from "./marketFormatting";
import type { InstrumentSelection } from "./KlineExplorer";

type Holding = {
  security: SecurityRef;
  weight_pct: number;
  holding_return_pct: number;
  industry: string;
};

type Trade = {
  date: string;
  security: SecurityRef;
  side: "buy" | "sell";
  quantity: number;
  weight_change_pct: number;
  execution_price: number;
};

type Contribution = {
  security: SecurityRef;
  contribution_pct: number;
};

type SecurityRef = {
  code: string;
  name: string;
};

export type PortfolioReviewData = {
  date: string;
  is_demo: boolean;
  daily_strategy_return_pct: number;
  holdings: Holding[];
  trades: Trade[];
  contributions: Contribution[];
};

type PortfolioView = "holdings" | "trades" | "contributions";

function SecurityCell({
  security,
  onSelect,
}: {
  security: SecurityRef;
  onSelect: (selection: InstrumentSelection) => void;
}) {
  return <button type="button" className="portfolio-security-link" onClick={() => onSelect({ instrumentType: "stock", code: security.code, name: security.name })}><strong>{security.name}</strong><span>{security.code}</span></button>;
}

export function PortfolioReview({
  data,
  onSelectSecurity,
}: {
  data: PortfolioReviewData;
  onSelectSecurity: (selection: InstrumentSelection) => void;
}) {
  const [view, setView] = useState<PortfolioView>("holdings");
  const [holdingSort, setHoldingSort] = useState<"weight_pct" | "holding_return_pct">(
    "weight_pct",
  );
  const [contributionAscending, setContributionAscending] = useState(false);
  const holdings = useMemo(
    () =>
      [...data.holdings].sort(
        (left, right) => right[holdingSort] - left[holdingSort],
      ),
    [data.holdings, holdingSort],
  );
  const contributions = useMemo(
    () =>
      [...data.contributions].sort((left, right) =>
        contributionAscending
          ? left.contribution_pct - right.contribution_pct
          : right.contribution_pct - left.contribution_pct,
      ),
    [contributionAscending, data.contributions],
  );

  return (
    <section className="portfolio-review" aria-labelledby="portfolio-review-title">
      <header className="portfolio-review__header">
        <div>
          <p className="section-kicker">组合</p>
          <h2 id="portfolio-review-title">持仓与交易复盘</h2>
        </div>
        <div className="portfolio-meta">
          <span className="demo-badge">演示组合数据</span>
          <span>{data.date}</span>
          <span>{data.holdings.length} 只持仓</span>
        </div>
      </header>

      <div className="portfolio-toolbar">
        <div className="segmented-control" aria-label="组合复盘视图">
          <button type="button" aria-pressed={view === "holdings"} onClick={() => setView("holdings")}>
            当前持仓
          </button>
          <button type="button" aria-pressed={view === "trades"} onClick={() => setView("trades")}>
            当日交易
          </button>
          <button
            type="button"
            aria-pressed={view === "contributions"}
            onClick={() => setView("contributions")}
          >
            收益贡献
          </button>
        </div>
        <span>
          当日策略收益 · {signedPercentage(data.daily_strategy_return_pct)}
        </span>
      </div>

      {view === "holdings" && (
        <table className="data-table" data-testid="holdings-table">
          <thead>
            <tr>
              <th>股票</th>
              <th>行业</th>
              <th>
                <button type="button" onClick={() => setHoldingSort("weight_pct")}>
                  按权重排序
                </button>
              </th>
              <th>
                <button type="button" onClick={() => setHoldingSort("holding_return_pct")}>
                  按持有收益排序
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((holding) => (
              <tr key={holding.security.code}>
                <td><SecurityCell security={holding.security} onSelect={onSelectSecurity} /></td>
                <td>{holding.industry}</td>
                <td>{holding.weight_pct.toFixed(2)}%</td>
                <td className={`metric-value--${movementTone(holding.holding_return_pct)}`}>
                  {signedPercentage(holding.holding_return_pct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {view === "trades" && (
        <table className="data-table" data-testid="trades-table">
          <thead><tr><th>股票</th><th>方向</th><th>数量</th><th>权重变化</th><th>成交价格</th></tr></thead>
          <tbody>
            {data.trades.map((trade, index) => (
              <tr key={`${trade.date}-${trade.security.code}-${trade.side}-${index}`}>
                <td><SecurityCell security={trade.security} onSelect={onSelectSecurity} /></td>
                <td className={trade.side === "buy" ? "metric-value--positive" : "metric-value--negative"}>
                  {trade.side === "buy" ? "买入" : "卖出"}
                </td>
                <td>{trade.quantity.toLocaleString("zh-CN")} 股</td>
                <td>{signedPercentage(trade.weight_change_pct)}</td>
                <td>¥{trade.execution_price.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {view === "contributions" && (
        <table className="data-table" data-testid="contributions-table">
          <thead>
            <tr>
              <th>股票</th>
              <th>
                <button
                  type="button"
                  onClick={() => setContributionAscending((current) => !current)}
                >
                  按贡献排序
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {contributions.map((item) => (
              <tr key={item.security.code}>
                <td><SecurityCell security={item.security} onSelect={onSelectSecurity} /></td>
                <td className={`metric-value--${movementTone(item.contribution_pct)}`}>
                  {signedPercentage(item.contribution_pct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
