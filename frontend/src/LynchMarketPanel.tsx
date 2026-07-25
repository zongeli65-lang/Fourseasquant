import { FormEvent, useEffect, useState } from "react";

type AnnualEps = { year: number; value: number };

type LynchItem = {
  target_date: string;
  code: string;
  name: string;
  close: number;
  financial_as_of: string | null;
  annual_adjusted_eps: AnnualEps[];
  ttm_adjusted_eps: number | null;
  three_year_cagr: number | null;
  dividend_yield: number | null;
  adjusted_pe: number | null;
  lynch_ratio: number | null;
  absolute_grade:
    | "exceptional"
    | "excellent"
    | "reasonable"
    | "weak"
    | "earnings_contraction"
    | "insufficient_data";
  warnings: string[];
  calculable: boolean;
  unavailable_reason: string | null;
  audit_status: string;
  risk_reasons: string[];
  ranking_eligible: boolean;
  ranking_exclusion_reason: string | null;
  market_percentile: number | null;
  percentile_universe_size: number;
};

type LynchOverview = {
  requested_date: string;
  actual_data_date: string;
  financial_base_date: string;
  financial_base_age_days: number;
  financial_base_status: "fresh" | "warning" | "expired";
  published_at: string;
  rules_version: string;
  total_count: number;
  calculable_count: number;
  ranking_eligible_count: number;
  unavailable_count: number;
  audit_pending_count: number;
  items: LynchItem[];
};

type PanelState =
  | { kind: "loading" }
  | { kind: "ready"; data: LynchOverview }
  | { kind: "empty" }
  | { kind: "error" };

const gradeLabels: Record<LynchItem["absolute_grade"], string> = {
  exceptional: "卓越",
  excellent: "优秀",
  reasonable: "合理",
  weak: "偏弱",
  earnings_contraction: "盈利收缩",
  insufficient_data: "数据不足",
};

function decimal(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

function percentage(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

export function LynchMarketPanel({ targetDate }: { targetDate: string }) {
  const [state, setState] = useState<PanelState>({ kind: "loading" });
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<
    "market_percentile" | "lynch_ratio" | "code"
  >("lynch_ratio");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [revision, setRevision] = useState(0);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setState({ kind: "loading" });
      const params = new URLSearchParams({
        target_date: targetDate,
        search,
        sort_by: sortBy,
        sort_order: sortOrder,
        limit: "100",
      });
      try {
        const response = await fetch(
          `/api/fundamentals/lynch/overview?${params}`,
          { signal: controller.signal },
        );
        if (response.status === 404) {
          setState({ kind: "empty" });
          return;
        }
        if (!response.ok) throw new Error(String(response.status));
        setState({
          kind: "ready",
          data: (await response.json()) as LynchOverview,
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({ kind: "error" });
      }
    }
    void load();
    return () => controller.abort();
  }, [revision, search, sortBy, sortOrder, targetDate]);

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setSearch(searchDraft.trim());
  }

  async function retry() {
    setRetrying(true);
    try {
      const response = await fetch("/api/fundamentals/lynch/retry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_date: targetDate }),
      });
      if (!response.ok) throw new Error(String(response.status));
      setRevision((value) => value + 1);
    } finally {
      setRetrying(false);
    }
  }

  return (
    <section className="lynch-market" aria-labelledby="lynch-market-title">
      <header className="lynch-market__header">
        <div>
          <p className="section-kicker">全市场横截面 · 独立数据集</p>
          <h2 id="lynch-market-title">三年林奇比</h2>
          <p>
            沪深主板、创业板与科创板；剔除 ST、退市、新股与无有效收盘价股票。
            绝对等级和市场百分位独立展示，不形成基本面总判断。
          </p>
        </div>
        {state.kind === "ready" && (
          <dl className="lynch-market__stats">
            <div><dt>全量股票</dt><dd>{state.data.total_count}</dd></div>
            <div><dt>可计算</dt><dd>{state.data.calculable_count}</dd></div>
            <div><dt>可排名</dt><dd>{state.data.ranking_eligible_count}</dd></div>
            <div><dt>数据不足</dt><dd>{state.data.unavailable_count}</dd></div>
          </dl>
        )}
      </header>

      {state.kind === "ready" && (
        <>
          <div
            className={`lynch-market__publication lynch-market__publication--${state.data.financial_base_status}`}
          >
            <span>行情日期 {state.data.actual_data_date}</span>
            <span>
              财务基座 {state.data.financial_base_date} ·
              {state.data.financial_base_age_days} 天
            </span>
            <span>规则 {state.data.rules_version}</span>
            {state.data.audit_pending_count > 0 && (
              <strong>
                {state.data.audit_pending_count} 只审计意见证据待确认，不进入正式百分位
              </strong>
            )}
          </div>
          <div className="lynch-market__controls">
            <form onSubmit={submitSearch}>
              <input
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                placeholder="搜索代码或名称"
                aria-label="搜索全市场林奇比"
              />
              <button type="submit">搜索</button>
            </form>
            <select
              value={sortBy}
              onChange={(event) =>
                setSortBy(
                  event.target.value as
                    | "market_percentile"
                    | "lynch_ratio"
                    | "code",
                )
              }
              aria-label="林奇比排序字段"
            >
              <option value="lynch_ratio">林奇比</option>
              <option value="market_percentile">市场百分位</option>
              <option value="code">股票代码</option>
            </select>
            <button
              type="button"
              onClick={() =>
                setSortOrder((value) => (value === "desc" ? "asc" : "desc"))
              }
            >
              {sortOrder === "desc" ? "降序" : "升序"}
            </button>
          </div>
          <div className="lynch-market__table">
            <table>
              <thead>
                <tr>
                  <th>股票</th>
                  <th>收盘价</th>
                  <th>三年扣非增长</th>
                  <th>扣非市盈率</th>
                  <th>股息率</th>
                  <th>林奇比</th>
                  <th>绝对等级</th>
                  <th>市场百分位</th>
                  <th>证据状态</th>
                </tr>
              </thead>
              <tbody>
                {state.data.items.map((item) => (
                  <tr key={item.code}>
                    <td>
                      <a
                        href={`/quotes?target_date=${targetDate}&instrument=stock:${item.code}&instrument_name=${encodeURIComponent(item.name)}`}
                      >
                        <strong>{item.name}</strong>
                        <span>{item.code}</span>
                      </a>
                    </td>
                    <td>{decimal(item.close)}</td>
                    <td>{percentage(item.three_year_cagr)}</td>
                    <td>{decimal(item.adjusted_pe)}</td>
                    <td>{percentage(item.dividend_yield)}</td>
                    <td>{decimal(item.lynch_ratio)}</td>
                    <td>
                      <span className={`lynch-grade lynch-grade--${item.absolute_grade}`}>
                        {gradeLabels[item.absolute_grade]}
                      </span>
                    </td>
                    <td>
                      {item.market_percentile === null
                        ? "—"
                        : `${item.market_percentile.toFixed(1)}%`}
                    </td>
                    <td>
                      {item.ranking_eligible
                        ? "基础财务闸门通过"
                        : item.audit_status === "unknown"
                          ? "审计证据待确认"
                          : item.unavailable_reason
                            ? "财务数据不足"
                            : "风险闸门暂停"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="lynch-market__note">
            页面最多展示当前排序前 100 只；数据库永久保留每日全量结果。
            “通过”只表示基础财务机械闸门通过，不表示安全、不会暴雷或投资建议。
          </p>
        </>
      )}
      {state.kind === "loading" && (
        <p className="lynch-market__state">正在读取全市场林奇结果…</p>
      )}
      {state.kind === "empty" && (
        <div className="lynch-market__state">
          <p>尚未完成首次全量初始化。</p>
          <button type="button" disabled={retrying} onClick={() => void retry()}>
            {retrying ? "正在运行全量任务…" : "重新运行今日任务"}
          </button>
        </div>
      )}
      {state.kind === "error" && (
        <div className="lynch-market__state">
          <p>全市场林奇数据读取失败，继续保留最近一次完整结果。</p>
          <button type="button" disabled={retrying} onClick={() => void retry()}>
            {retrying ? "正在重新运行…" : "重新运行今日任务"}
          </button>
        </div>
      )}
    </section>
  );
}
