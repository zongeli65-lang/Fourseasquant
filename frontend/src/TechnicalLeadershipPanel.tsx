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
};

type LoadState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; status: ScoreStatus; scores: TechnicalScore[] };

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
}: {
  targetDate: string;
  onSelectSecurity: (selection: InstrumentSelection) => void;
}) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
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
          setState({ kind: "ready", status, scores: [] });
          return;
        }
        const params = new URLSearchParams({
          target_date: targetDate,
          limit: "20",
        });
        const scoreResponse = await fetch(`/api/technical-scores/top?${params}`, {
          signal: controller.signal,
        });
        if (!scoreResponse.ok) {
          throw new Error(`技术评分排行接口返回 ${scoreResponse.status}`);
        }
        setState({
          kind: "ready",
          status,
          scores: (await scoreResponse.json()) as TechnicalScore[],
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState({ kind: "error" });
      }
    }
    void load();
    return () => controller.abort();
  }, [targetDate]);

  if (state.kind === "loading") {
    return <section className="technical-panel technical-panel--message">正在读取个股技术评分…</section>;
  }
  if (state.kind === "error") {
    return <section className="technical-panel technical-panel--message technical-panel--error">技术评分读取失败，请检查本机服务。</section>;
  }
  const publication = state.status.publication;
  if (publication === null) {
    return (
      <section className="technical-panel" data-testid="technical-score-panel">
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
    <section className="technical-panel" data-testid="technical-score-panel">
      <header className="technical-panel__header">
        <div>
          <p className="section-kicker">技术龙头 · 独立量价体系</p>
          <h2>全市场个股技术评分</h2>
          <p>
            正式覆盖 {publication.official_start} 至 {publication.official_end}
            {" · "}算法 {publication.version}
          </p>
        </div>
        <span className="technical-state-badge">完整批次</span>
      </header>

      <div className="technical-summary-grid">
        <article><span>评分股票</span><strong>{publication.symbol_count.toLocaleString("zh-CN")}</strong><small>主板 · 创业板 · 科创板</small></article>
        <article><span>永久结果</span><strong>{publication.score_count.toLocaleString("zh-CN")}</strong><small>股票 × 交易日</small></article>
        <article><span>结构权重</span><strong>{state.status.parameters.structure_weight}</strong><small>极值结构主导</small></article>
        <article><span>正式门槛</span><strong>{state.status.parameters.minimum_leader_score}</strong><small>结构完整仍须达标</small></article>
      </div>

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
              <th>股票</th><th>市场</th><th>总分</th><th>极值结构</th><th>进行中突破</th>
              <th>相对强度</th><th>成交确认</th><th>状态证据</th>
            </tr>
          </thead>
          <tbody>
            {state.scores.map((score) => (
              <tr key={score.code}>
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
              </tr>
            ))}
            {state.scores.length === 0 && (
              <tr><td colSpan={8} className="technical-empty">目标日期前尚无已发布技术评分。</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="technical-footnote">排名使用未四舍五入分数；参数只能从后端按版本修改。点击股票可查看日 K 线、成交量和 RSI（相对强弱指标）。</p>
    </section>
  );
}
