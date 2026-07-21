import { useState } from "react";
import { movementTone, signedPercentage } from "./marketFormatting";

type SectorMove = {
  name: string;
  change_pct: number;
};

type SectorRanking = {
  leaders: SectorMove[];
  laggards: SectorMove[];
  heatmap_moves: SectorMove[];
};

export type SectorPerformanceData = {
  industries: SectorRanking;
  concepts: SectorRanking;
};

function heatColor(value: number): string {
  if (value === 0) {
    return "rgba(100, 117, 141, 0.28)";
  }
  const alpha = Math.min(0.58, 0.16 + Math.abs(value) * 0.075);
  return value > 0
    ? `rgba(221, 67, 82, ${alpha})`
    : `rgba(42, 169, 121, ${alpha})`;
}

function RankingList({
  title,
  moves,
  testId,
}: {
  title: string;
  moves: SectorMove[];
  testId: string;
}) {
  return (
    <section className="ranking-column">
      <h3>{title}</h3>
      <ol data-testid={testId}>
        {moves.map((move, index) => (
          <li key={move.name}>
            <span className="ranking-position">{String(index + 1).padStart(2, "0")}</span>
            <span className="ranking-name">{move.name}</span>
            <strong className={`market-${movementTone(move.change_pct)}`}>
              {signedPercentage(move.change_pct)}
            </strong>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function SectorPerformance({ data }: { data: SectorPerformanceData | null }) {
  const [category, setCategory] = useState<"industries" | "concepts">("industries");
  const [view, setView] = useState<"ranking" | "heatmap">("ranking");
  const ranking = data?.[category];
  const categoryLabel = category === "industries" ? "行业板块" : "概念板块";

  return (
    <section className="sector-performance" aria-labelledby="sector-performance-title">
      <header className="sector-performance__header">
        <div>
          <p className="section-kicker">板块</p>
          <h2 id="sector-performance-title">板块与概念强弱</h2>
        </div>
        <div className="sector-controls">
          <div className="segmented-control" aria-label="板块分类">
            <button
              type="button"
              aria-pressed={category === "industries"}
              onClick={() => setCategory("industries")}
            >
              行业板块
            </button>
            <button
              type="button"
              aria-pressed={category === "concepts"}
              onClick={() => setCategory("concepts")}
            >
              概念板块
            </button>
          </div>
          <div className="segmented-control" aria-label="板块视图">
            <button
              type="button"
              aria-pressed={view === "ranking"}
              onClick={() => setView("ranking")}
            >
              排行榜
            </button>
            <button
              type="button"
              aria-pressed={view === "heatmap"}
              onClick={() => setView("heatmap")}
            >
              热力图
            </button>
          </div>
        </div>
      </header>

      <p className="sector-category-label" data-testid="sector-category-label">
        {categoryLabel} · 演示数据
      </p>

      {!ranking && (
        <div className="sector-empty">尚无板块快照，请先运行目标日期任务。</div>
      )}
      {ranking && view === "ranking" && (
        <div className="ranking-grid">
          <RankingList title="涨幅前十" moves={ranking.leaders} testId="sector-leaders" />
          <RankingList title="跌幅前十" moves={ranking.laggards} testId="sector-laggards" />
        </div>
      )}
      {ranking && view === "heatmap" && (
        <div className="sector-heatmap" data-testid="sector-heatmap">
          {ranking.heatmap_moves.map((move) => (
            <div key={move.name} style={{ backgroundColor: heatColor(move.change_pct) }}>
              <span>{move.name}</span>
              <strong>{signedPercentage(move.change_pct)}</strong>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
