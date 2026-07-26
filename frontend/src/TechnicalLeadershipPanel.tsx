import { useEffect, useState } from "react";
import type { InstrumentSelection } from "./KlineExplorer";

type Publication = {
  version: string;
  official_start: string;
  official_end: string;
  qfq_source: string;
  symbol_count: number;
  score_count: number;
  published_at: string;
};

type ScoreStatus = {
  status: "not_initialized" | "ready";
  publication: Publication | null;
  parameters: {
    structure_weight: number;
    breakout_weight: number;
    relative_strength_weight: number;
    turnover_weight: number;
    minimum_leader_score: number;
    ema_span: number;
    atr_period: number;
  };
};

type TechnicalScore = {
  actual_data_date: string;
  code: string;
  name: string;
  board: "main" | "chinext" | "star";
  derivative_state: "positive" | "zero" | "negative";
  structure_state: "forming" | "candidate" | "strong" | "broken";
  structure_valid: boolean;
  active_breakout: boolean;
  structure_score: number;
  breakout_score: number;
  relative_strength_score: number;
  turnover_score: number;
  total_score: number;
  maxima: Array<{ date: string; value: number }>;
  minima: Array<{ date: string; value: number }>;
  rank?: number | null;
  is_current?: boolean;
};

type ScorePage = {
  requested_date: string;
  actual_data_date: string | null;
  total: number;
  universe_count: number;
  current_score_count: number;
  stale_score_count: number;
  page: number;
  page_size: number;
  items: TechnicalScore[];
};

type BoardFilter = "" | TechnicalScore["board"];
type ScoreSort =
  | "total_score"
  | "structure_score"
  | "breakout_score"
  | "relative_strength_score"
  | "turnover_score"
  | "code"
  | "name";

type LoadState =
  | { kind: "loading" }
  | { kind: "error" }
  | {
      kind: "ready";
      status: ScoreStatus;
      scores: TechnicalScore[];
      pageData: ScorePage | null;
    };

const BOARD_LABELS: Record<TechnicalScore["board"], string> = {
  main: "沪深主板",
  chinext: "创业板",
  star: "科创板",
};

const STRUCTURE_LABELS: Record<TechnicalScore["structure_state"], string> = {
  forming: "结构形成中",
  candidate: "两组结构成立",
  strong: "三组强结构",
  broken: "结构已破坏",
};

const DERIVATIVE_LABELS: Record<TechnicalScore["derivative_state"], string> = {
  positive: "导数为正",
  zero: "导数零区间",
  negative: "导数为负",
};

export function TechnicalLeadershipPanel({
  targetDate,
  onSelectSecurity,
  compact = false,
}: {
  targetDate: string;
  onSelectSecurity: (selection: InstrumentSelection) => void;
  compact?: boolean;
}) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [board, setBoard] = useState<BoardFilter>("");
  const [sortBy, setSortBy] = useState<ScoreSort>("total_score");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      async function load() {
        setState({ kind: "loading" });
        try {
          const statusResponse = await fetch("/api/technical-scores/status", {
            signal: controller.signal,
          });
          if (!statusResponse.ok) {
            throw new Error(`技术评分状态接口返回 ${statusResponse.status}`);
          }
          const status = (await statusResponse.json()) as ScoreStatus;
          if (status.status === "not_initialized") {
            setState({ kind: "ready", status, scores: [], pageData: null });
            return;
          }
          const params = compact
            ? new URLSearchParams({
                target_date: targetDate,
                limit: "20",
              })
            : new URLSearchParams({
                target_date: targetDate,
                page: String(page),
                page_size: "100",
                search,
                sort_by: sortBy,
                sort_order: sortOrder,
                ...(board ? { board } : {}),
              });
          const scoreResponse = await fetch(
            compact
              ? `/api/technical-scores/top?${params}`
              : `/api/technical-scores?${params}`,
            {
              signal: controller.signal,
            },
          );
          if (!scoreResponse.ok) {
            throw new Error(`技术评分接口返回 ${scoreResponse.status}`);
          }
          const result = compact
            ? ((await scoreResponse.json()) as TechnicalScore[])
            : ((await scoreResponse.json()) as ScorePage);
          setState({
            kind: "ready",
            status,
            scores: compact ? (result as TechnicalScore[]) : (result as ScorePage).items,
            pageData: compact ? null : (result as ScorePage),
          });
        } catch (error) {
          if (error instanceof DOMException && error.name === "AbortError") return;
          setState({ kind: "error" });
        }
      }
      void load();
    }, compact ? 0 : 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [board, compact, page, search, sortBy, sortOrder, targetDate]);

  useEffect(() => {
    setPage(1);
  }, [targetDate]);

  if (state.kind === "loading") {
    return <section className={`technical-panel technical-panel--message${compact ? " technical-panel--compact" : ""}`}>正在读取个股技术评分…</section>;
  }
  if (state.kind === "error") {
    return <section className={`technical-panel technical-panel--message technical-panel--error${compact ? " technical-panel--compact" : ""}`}>技术评分读取失败，请检查本机服务。</section>;
  }
  const publication = state.status.publication;
  const pageData = state.pageData;
  const totalPages = Math.max(
    1,
    Math.ceil((pageData?.total ?? 0) / (pageData?.page_size ?? 100)),
  );
  if (publication === null) {
    return (
      <section className={`technical-panel${compact ? " technical-panel--compact" : ""}`} data-testid="technical-score-panel">
        <header className="technical-panel__header">
          <div>
            <p className="section-kicker">技术龙头 · 第一阶段</p>
            <h2>个股技术评分尚未初始化</h2>
            <p>等待三市场一年正式行情与 60 个交易日预热数据形成完整批次。</p>
          </div>
          <span className="technical-state-badge technical-state-badge--pending">等待初始化</span>
        </header>
        <div className="technical-waiting">
          <strong>当前不会生成模拟板块龙头</strong>
          <p>个股技术评分完成后仍需基本面任务提供正式板块成员关系，才能执行板块技术龙头选举。</p>
        </div>
      </section>
    );
  }

  return (
    <section className={`technical-panel${compact ? " technical-panel--compact" : ""}`} data-testid="technical-score-panel">
      <header className="technical-panel__header">
        <div>
          <p className="section-kicker">技术龙头 · 独立量价体系</p>
          <h2>{compact ? "技术评分概览" : "全市场个股技术评分"}</h2>
          <p>
            正式覆盖 {publication.official_start} 至 {publication.official_end}
            {" · "}算法 {publication.version}
          </p>
        </div>
        <span className="technical-state-badge">完整批次</span>
      </header>

      {!compact && (
        <>
          <div className="technical-summary-grid">
            <article><span>评分股票</span><strong>{(pageData?.universe_count ?? publication.symbol_count).toLocaleString("zh-CN")}</strong><small>主板 · 创业板 · 科创板</small></article>
            <article><span>目标日评分</span><strong>{(pageData?.current_score_count ?? 0).toLocaleString("zh-CN")}</strong><small>进入目标日正式排名</small></article>
            <article><span>目标日无行情</span><strong>{(pageData?.stale_score_count ?? 0).toLocaleString("zh-CN")}</strong><small>保留最近评分，不混入排名</small></article>
            <article><span>永久结果</span><strong>{publication.score_count.toLocaleString("zh-CN")}</strong><small>股票 × 交易日</small></article>
          </div>
          <div className="technical-score-controls" aria-label="技术评分筛选">
            <label>
              <span>搜索技术评分</span>
              <input
                type="search"
                value={search}
                placeholder="输入六位代码或中文名称"
                onChange={(event) => {
                  setSearch(event.target.value);
                  setPage(1);
                }}
              />
            </label>
            <label>
              <span>市场</span>
              <select
                value={board}
                onChange={(event) => {
                  setBoard(event.target.value as BoardFilter);
                  setPage(1);
                }}
              >
                <option value="">全部市场</option>
                <option value="main">沪深主板</option>
                <option value="chinext">创业板</option>
                <option value="star">科创板</option>
              </select>
            </label>
            <label>
              <span>排序指标</span>
              <select
                value={sortBy}
                onChange={(event) => {
                  setSortBy(event.target.value as ScoreSort);
                  setPage(1);
                }}
              >
                <option value="total_score">技术总分</option>
                <option value="structure_score">极值结构</option>
                <option value="breakout_score">进行中突破</option>
                <option value="relative_strength_score">相对强度</option>
                <option value="turnover_score">成交确认</option>
                <option value="code">股票代码</option>
                <option value="name">股票名称</option>
              </select>
            </label>
            <button
              type="button"
              aria-label={sortOrder === "desc" ? "当前降序，切换为升序" : "当前升序，切换为降序"}
              onClick={() => {
                setSortOrder((value) => (value === "desc" ? "asc" : "desc"));
                setPage(1);
              }}
            >
              {sortOrder === "desc" ? "降序" : "升序"}
            </button>
            <span className="technical-result-count">
              匹配 {pageData?.total.toLocaleString("zh-CN") ?? "—"} 只
            </span>
          </div>
        </>
      )}

      <div className="technical-contract-note">
        <div>
          <strong>当前仅展示技术评分，不等同于正式板块龙头</strong>
          <span>等待基本面模块提供带版本和生效日期的板块成员关系。</span>
        </div>
        <span>3 日 EMA · 10 日自适应波幅 · 0—100 分</span>
      </div>

      <div className="technical-table-wrap">
        <table>
          <thead>
            <tr>
              {!compact && <th>排名</th>}
              <th>股票</th><th>市场</th><th>总分</th><th>极值结构</th><th>进行中突破</th>
              <th>相对强度</th><th>成交确认</th><th>状态证据</th>
              {!compact && <th>数据状态</th>}
            </tr>
          </thead>
          <tbody>
            {(compact ? state.scores.slice(0, 5) : state.scores).map((score) => (
              <tr key={score.code}>
                {!compact && <td><strong className="technical-rank">{score.rank ?? "—"}</strong></td>}
                <td>
                  <button type="button" onClick={() => onSelectSecurity({ instrumentType: "stock", code: score.code, name: score.name })}>
                    <strong>{score.name}</strong><span>{score.code}</span>
                  </button>
                </td>
                <td>{BOARD_LABELS[score.board]}</td>
                <td><strong className="technical-total">{score.total_score.toFixed(2)}</strong></td>
                <td>{score.structure_score.toFixed(2)} / 55</td>
                <td>{score.breakout_score.toFixed(2)} / 20</td>
                <td>{score.relative_strength_score.toFixed(2)} / 15</td>
                <td>{score.turnover_score.toFixed(2)} / 10</td>
                <td>
                  <div className="technical-evidence">
                    <span className={score.structure_valid ? "technical-evidence--valid" : ""}>{STRUCTURE_LABELS[score.structure_state]}</span>
                    <span>{DERIVATIVE_LABELS[score.derivative_state]}</span>
                    {score.active_breakout && <span className="technical-evidence--breakout">创新高进行中</span>}
                  </div>
                </td>
                {!compact && (
                  <td>
                    {score.is_current ? (
                      <span className="technical-data-state technical-data-state--current">目标日评分</span>
                    ) : (
                      <span className="technical-data-state technical-data-state--stale">
                        目标日无行情 · 最近评分 {score.actual_data_date}
                      </span>
                    )}
                  </td>
                )}
              </tr>
            ))}
            {state.scores.length === 0 && (
              <tr><td colSpan={compact ? 8 : 10} className="technical-empty">没有匹配的技术评分。</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {!compact && (
        <footer className="technical-pagination">
          <span>
            第 {Math.min(page, totalPages)} / {totalPages} 页 · 每页 100 只
          </span>
          <div>
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              上一页
            </button>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage((value) => value + 1)}
            >
              下一页
            </button>
          </div>
        </footer>
      )}
      <p className="technical-footnote">
        {compact
          ? "展示目标日期前五名。点击股票直接进入行情浏览。"
          : "当日评分按未四舍五入分数排名；缺行情股票仅展示最近评分且不进入当日排名。点击股票可查看日 K 线、成交量和 RSI（相对强弱指标）。"}
      </p>
    </section>
  );
}
