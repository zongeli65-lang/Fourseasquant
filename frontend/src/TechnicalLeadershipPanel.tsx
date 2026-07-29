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
    structure_break_pct: number;
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
  maxima: Array<{ date: string; value: number }>;
  minima: Array<{ date: string; value: number }>;
  evidence?: {
    structure_break_reason?: string | null;
    structure_break_reference?: number | null;
    structure_break_line?: number | null;
  };
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
  | "structure_score"
  | "breakout_score"
  | "relative_strength_score";

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

const RANKING_LABELS: Record<ScoreSort, string> = {
  structure_score: "极值结构",
  breakout_score: "突破",
  relative_strength_score: "相对强度",
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
  const [sortBy, setSortBy] = useState<ScoreSort>("structure_score");

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      async function load() {
        setState((current) =>
          current.kind === "ready" ? current : { kind: "loading" },
        );
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
          const params = new URLSearchParams({
            target_date: targetDate,
            page: compact ? "1" : String(page),
            page_size: compact ? "5" : "100",
            search: compact ? "" : search,
            sort_by: sortBy,
            sort_order: "desc",
            ...(!compact && board ? { board } : {}),
          });
          const scoreResponse = await fetch(
            `/api/technical-scores?${params}`,
            {
              signal: controller.signal,
            },
          );
          if (!scoreResponse.ok) {
            throw new Error(`技术评分接口返回 ${scoreResponse.status}`);
          }
          const result = (await scoreResponse.json()) as ScorePage;
          setState({
            kind: "ready",
            status,
            scores: result.items,
            pageData: result,
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
  }, [board, compact, page, search, sortBy, targetDate]);

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
          <h2>{compact ? "技术指标排行概览" : "全市场技术指标排行榜"}</h2>
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
            <article><span>目标日排行</span><strong>{(pageData?.current_score_count ?? 0).toLocaleString("zh-CN")}</strong><small>进入各指标独立排名</small></article>
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
            <div
              className="segmented-control"
              role="group"
              aria-label="选择技术指标排行榜"
            >
              {(Object.entries(RANKING_LABELS) as Array<[ScoreSort, string]>).map(
                ([metric, label]) => (
                  <button
                    key={metric}
                    type="button"
                    aria-pressed={sortBy === metric}
                    onClick={() => {
                      setSortBy(metric);
                      setPage(1);
                    }}
                  >
                    {label}榜
                  </button>
                ),
              )}
            </div>
            <span className="technical-result-count">
              匹配 {pageData?.total.toLocaleString("zh-CN") ?? "—"} 只
            </span>
          </div>
        </>
      )}

      <div className="technical-contract-note">
        <div>
          <strong>三项指标独立排行，不合成技术总分</strong>
          <span>成交确认并入突破榜与相对强度榜，不再单独成榜。当前查看：{RANKING_LABELS[sortBy]}榜。</span>
        </div>
        <span>3 日 EMA（指数移动平均线）· 前低 2% 破位失效</span>
      </div>

      <div className="technical-table-wrap">
        <table>
          <thead>
            <tr>
              <th>{RANKING_LABELS[sortBy]}名次</th>
              <th>股票</th><th>市场</th><th>{RANKING_LABELS[sortBy]}指标值</th><th>状态证据</th>
              {!compact && <th>数据状态</th>}
            </tr>
          </thead>
          <tbody>
            {(compact ? state.scores.slice(0, 5) : state.scores).map((score) => (
              <tr key={score.code}>
                <td><strong className="technical-rank">{score.rank ?? "—"}</strong></td>
                <td>
                  <button type="button" onClick={() => onSelectSecurity({ instrumentType: "stock", code: score.code, name: score.name })}>
                    <strong>{score.name}</strong><span>{score.code}</span>
                  </button>
                </td>
                <td>{BOARD_LABELS[score.board]}</td>
                <td><strong className="technical-total">{score[sortBy].toFixed(2)}</strong></td>
                <td>
                  <div className="technical-evidence">
                    <span className={score.structure_valid ? "technical-evidence--valid" : ""}>{STRUCTURE_LABELS[score.structure_state]}</span>
                    <span>{DERIVATIVE_LABELS[score.derivative_state]}</span>
                    {sortBy !== "structure_score" && (
                      <span>成交确认 {score.turnover_score.toFixed(2)}</span>
                    )}
                    {score.active_breakout && <span className="technical-evidence--breakout">创新高进行中</span>}
                    {score.evidence?.structure_break_reason === "ema_below_last_trough" && (
                      <span>
                        前低 {score.evidence.structure_break_reference?.toFixed(2)}
                        {" · "}2% 破坏线 {score.evidence.structure_break_line?.toFixed(2)}
                      </span>
                    )}
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
              <tr><td colSpan={compact ? 5 : 6} className="technical-empty">没有匹配的技术排行。</td></tr>
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
          ? `展示目标日期${RANKING_LABELS[sortBy]}榜前五名。点击股票直接进入行情浏览。`
          : `${RANKING_LABELS[sortBy]}榜按该项未四舍五入的指标值独立排名；缺行情股票仅展示最近结果且不进入目标日排名。点击股票可查看日 K 线、成交量和 RSI（相对强弱指标）。`}
      </p>
    </section>
  );
}
