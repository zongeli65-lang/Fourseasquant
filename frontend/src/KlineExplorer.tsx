import { useEffect, useMemo, useRef, useState } from "react";
import { signedPercentage } from "./marketFormatting";

export type InstrumentSelection = {
  instrumentType: "stock" | "index";
  code: string;
  name: string;
};

type Candle = {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover_cny: number;
  change_pct: number;
  rsi14: number | null;
};

type TradeMarker = {
  date: string;
  side: "buy" | "sell";
  price: number;
  amount_cny: number;
};

type CandleSeries = {
  source: "akshare";
  instrument_type: "stock" | "index";
  code: string;
  name: string;
  adjustment: "raw" | "qfq";
  requested_date: string;
  actual_data_date: string;
  coverage_start: string;
  coverage_end: string;
  candles: Candle[];
  trades: TradeMarker[];
};

type SearchResult = {
  code: string;
  name: string;
  exchange: "上海" | "深圳";
};

type RangeKey = "1m" | "3m" | "6m" | "1y" | "all";

const INDEXES: InstrumentSelection[] = [
  { instrumentType: "index", code: "sh000001", name: "上证指数" },
  { instrumentType: "index", code: "sz399001", name: "深证成指" },
  { instrumentType: "index", code: "sh000300", name: "沪深 300" },
  { instrumentType: "index", code: "sz399006", name: "创业板指" },
];

const RANGE_COUNTS: Record<RangeKey, number> = {
  "1m": 22,
  "3m": 66,
  "6m": 132,
  "1y": 260,
  all: Number.POSITIVE_INFINITY,
};

const RANGE_LABELS: Record<RangeKey, string> = {
  "1m": "近 1 月",
  "3m": "近 3 月",
  "6m": "近 6 月",
  "1y": "近 1 年",
  all: "全部",
};

const RANGE_BUTTON_LABELS: Record<RangeKey, string> = {
  "1m": "1 个月",
  "3m": "3 个月",
  "6m": "6 个月",
  "1y": "1 年",
  all: "全部 K 线",
};

const WIDTH = 1200;
const PLOT_LEFT = 58;
const PLOT_RIGHT = 1128;
const PLOT_WIDTH = PLOT_RIGHT - PLOT_LEFT;
const PRICE_TOP = 28;
const PRICE_HEIGHT = 330;
const VOLUME_TOP = 390;
const VOLUME_HEIGHT = 92;
const RSI_TOP = 522;
const RSI_HEIGHT = 112;

function compactVolume(value: number): string {
  if (value >= 100_000_000) return `${(value / 100_000_000).toFixed(2)} 亿`;
  if (value >= 10_000) return `${(value / 10_000).toFixed(1)} 万`;
  return value.toLocaleString("zh-CN");
}

function compactMoney(value: number): string {
  if (value >= 100_000_000) return `¥${(value / 100_000_000).toFixed(2)} 亿`;
  if (value >= 10_000) return `¥${(value / 10_000).toFixed(1)} 万`;
  return `¥${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}

function yScale(value: number, minimum: number, maximum: number, top: number, height: number): number {
  const range = maximum - minimum || 1;
  return top + ((maximum - value) / range) * height;
}

function dateLabel(value: string): string {
  return value.slice(5).replace("-", "/");
}

export function KlineExplorer({
  targetDate,
  selection,
  onSelectionChange,
}: {
  targetDate: string;
  selection: InstrumentSelection;
  onSelectionChange: (selection: InstrumentSelection) => void;
}) {
  const [adjustment, setAdjustment] = useState<"raw" | "qfq">("qfq");
  const [series, setSeries] = useState<CandleSeries | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "empty" | "error">("loading");
  const [range, setRange] = useState<RangeKey>("1y");
  const [visibleCount, setVisibleCount] = useState(260);
  const [panOffset, setPanOffset] = useState(0);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const drag = useRef<{ x: number; offset: number } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function loadCandles() {
      setLoadState("loading");
      const params = new URLSearchParams({
        instrument_type: selection.instrumentType,
        code: selection.code,
        target_date: targetDate,
        adjustment,
      });
      try {
        const response = await fetch(`/api/market-data/candles?${params}`, {
          signal: controller.signal,
        });
        if (response.status === 404) {
          setSeries(null);
          setLoadState("empty");
          return;
        }
        if (!response.ok) throw new Error(`K 线接口返回 ${response.status}`);
        const result = (await response.json()) as CandleSeries;
        setSeries(result);
        setLoadState("ready");
        setPanOffset(0);
        setHoverIndex(null);
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setSeries(null);
        setLoadState("error");
      }
    }
    void loadCandles();
    return () => controller.abort();
  }, [adjustment, selection.code, selection.instrumentType, targetDate]);

  useEffect(() => {
    if (search.trim().length === 0) {
      setSearchResults([]);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams({
        query: search.trim(),
        target_date: targetDate,
        limit: "20",
      });
      void fetch(`/api/market-data/securities?${params}`, { signal: controller.signal })
        .then((response) => response.ok ? response.json() as Promise<SearchResult[]> : [])
        .then((results) => setSearchResults(results))
        .catch((error: unknown) => {
          if (!(error instanceof DOMException && error.name === "AbortError")) {
            setSearchResults([]);
          }
        });
    }, 180);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [search, targetDate]);

  const candles = series?.candles ?? [];
  const effectiveVisibleCount = Math.min(Math.max(12, visibleCount), candles.length || 12);
  const maximumOffset = Math.max(0, candles.length - effectiveVisibleCount);
  const safeOffset = Math.min(panOffset, maximumOffset);
  const end = Math.max(0, candles.length - safeOffset);
  const start = Math.max(0, end - effectiveVisibleCount);
  const visible = useMemo(() => candles.slice(start, end), [candles, end, start]);

  const chart = useMemo(() => {
    if (visible.length === 0) return null;
    const low = Math.min(...visible.map((item) => item.low));
    const high = Math.max(...visible.map((item) => item.high));
    const padding = Math.max((high - low) * 0.08, high * 0.005);
    const minimum = low - padding;
    const maximum = high + padding;
    const volumeMaximum = Math.max(...visible.map((item) => item.volume), 1);
    const step = PLOT_WIDTH / visible.length;
    return { minimum, maximum, volumeMaximum, step };
  }, [visible]);

  const hovered = hoverIndex === null ? visible.at(-1) : visible[hoverIndex];

  function setRangeAndReset(next: RangeKey) {
    setRange(next);
    setVisibleCount(
      Number.isFinite(RANGE_COUNTS[next])
        ? RANGE_COUNTS[next]
        : Math.max(candles.length, 12),
    );
    setPanOffset(0);
  }

  function chooseStock(result: SearchResult) {
    onSelectionChange({
      instrumentType: "stock",
      code: result.code,
      name: result.name,
    });
    setSearch(`${result.name} ${result.code}`);
    setSearchOpen(false);
  }

  return (
    <section className="kline-explorer" aria-labelledby="kline-title" data-testid="kline-explorer">
      <header className="kline-header">
        <div>
          <p className="section-kicker">AKShare · 日 K 浏览</p>
          <h2 id="kline-title">{selection.name} <span>{selection.code}</span></h2>
          <p>
            截止 {series?.actual_data_date ?? targetDate} · 日 K / 日成交量 / RSI(14)
          </p>
        </div>
        <div className="kline-search-wrap">
          <label htmlFor="security-search">搜索规则内股票</label>
          <input
            id="security-search"
            value={search}
            placeholder="输入六位代码或中文名称"
            autoComplete="off"
            onFocus={() => setSearchOpen(true)}
            onChange={(event) => {
              setSearch(event.target.value);
              setSearchOpen(true);
            }}
          />
          {searchOpen && search.trim() && (
            <div className="kline-search-results">
              {searchResults.length === 0 ? (
                <p>没有符合当前规则的股票</p>
              ) : searchResults.map((result) => (
                <button type="button" key={result.code} onClick={() => chooseStock(result)}>
                  <strong>{result.name}</strong>
                  <span>{result.code} · {result.exchange}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </header>

      <div className="kline-toolbar">
        <div className="kline-index-tabs" aria-label="市场指数">
          {INDEXES.map((index) => (
            <button
              type="button"
              key={index.code}
              aria-pressed={selection.instrumentType === "index" && selection.code === index.code}
              onClick={() => onSelectionChange(index)}
            >
              {index.name}
            </button>
          ))}
        </div>
        <div className="kline-range-tabs" aria-label="K 线范围">
          {(Object.keys(RANGE_LABELS) as RangeKey[]).map((key) => (
            <button type="button" key={key} aria-label={`K 线范围：${RANGE_BUTTON_LABELS[key]}`} aria-pressed={range === key} onClick={() => setRangeAndReset(key)}>
              {RANGE_BUTTON_LABELS[key]}
            </button>
          ))}
        </div>
        {selection.instrumentType === "stock" && (
          <div className="kline-adjustment" aria-label="股票复权方式">
            <button type="button" aria-pressed={adjustment === "qfq"} onClick={() => setAdjustment("qfq")}>前复权</button>
            <button type="button" aria-pressed={adjustment === "raw"} onClick={() => setAdjustment("raw")}>不复权</button>
          </div>
        )}
        <button type="button" className="kline-latest" onClick={() => setPanOffset(0)}>回到最近交易日</button>
      </div>

      {loadState === "loading" && <p className="kline-message">正在读取真实 K 线…</p>}
      {loadState === "empty" && <p className="kline-message kline-message--error">当前没有完整 K 线数据集，请先运行真实数据补采。</p>}
      {loadState === "error" && <p className="kline-message kline-message--error">K 线读取失败，请检查本机服务。</p>}

      {loadState === "ready" && series && chart && hovered && (
        <>
          <div className="kline-quote-strip" aria-live="polite">
            <strong>{hovered.date}</strong>
            <span>开 <b>{hovered.open.toFixed(2)}</b></span>
            <span>高 <b>{hovered.high.toFixed(2)}</b></span>
            <span>低 <b>{hovered.low.toFixed(2)}</b></span>
            <span>收 <b>{hovered.close.toFixed(2)}</b></span>
            <span className={hovered.change_pct >= 0 ? "metric-value--positive" : "metric-value--negative"}>
              {signedPercentage(hovered.change_pct)}
            </span>
            <span>量 <b>{compactVolume(hovered.volume)}</b></span>
            <span>RSI <b>{hovered.rsi14?.toFixed(2) ?? "数据不足"}</b></span>
          </div>
          <div className="kline-canvas-wrap">
            <svg
              className="kline-canvas"
              viewBox={`0 0 ${WIDTH} 670`}
              role="img"
              aria-label={`${selection.name} 日 K、成交量和 RSI 图`}
              onWheel={(event) => {
                event.preventDefault();
                const direction = event.deltaY > 0 ? 1.15 : 0.85;
                setVisibleCount((current) => clamp(Math.round(current * direction), 12, Math.max(candles.length, 12)));
                setRange("all");
              }}
              onPointerDown={(event) => {
                drag.current = { x: event.clientX, offset: safeOffset };
                event.currentTarget.setPointerCapture(event.pointerId);
              }}
              onPointerMove={(event) => {
                const bounds = event.currentTarget.getBoundingClientRect();
                const x = ((event.clientX - bounds.left) / bounds.width) * WIDTH;
                const nextHover = Math.floor((x - PLOT_LEFT) / chart.step);
                setHoverIndex(nextHover >= 0 && nextHover < visible.length ? nextHover : null);
                if (drag.current) {
                  const pixelPerCandle = (bounds.width * (PLOT_WIDTH / WIDTH)) / visible.length;
                  const delta = Math.round((event.clientX - drag.current.x) / Math.max(pixelPerCandle, 1));
                  setPanOffset(clamp(drag.current.offset - delta, 0, maximumOffset));
                }
              }}
              onPointerUp={(event) => {
                drag.current = null;
                event.currentTarget.releasePointerCapture(event.pointerId);
              }}
              onPointerLeave={() => {
                drag.current = null;
                setHoverIndex(null);
              }}
            >
              {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
                const y = PRICE_TOP + fraction * PRICE_HEIGHT;
                const price = chart.maximum - fraction * (chart.maximum - chart.minimum);
                return <g key={fraction}><line className="kline-grid" x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={y} y2={y} /><text className="kline-axis" x={PLOT_RIGHT + 8} y={y + 4}>{price.toFixed(2)}</text></g>;
              })}
              <line className="kline-divider" x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={375} y2={375} />
              <line className="kline-divider" x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={505} y2={505} />
              <text className="kline-panel-label" x={PLOT_LEFT} y={382}>成交量</text>
              <text className="kline-panel-label" x={PLOT_LEFT} y={514}>RSI(14)</text>

              {visible.map((candle, index) => {
                const x = PLOT_LEFT + chart.step * (index + 0.5);
                const rising = candle.close >= candle.open;
                const openY = yScale(candle.open, chart.minimum, chart.maximum, PRICE_TOP, PRICE_HEIGHT);
                const closeY = yScale(candle.close, chart.minimum, chart.maximum, PRICE_TOP, PRICE_HEIGHT);
                const highY = yScale(candle.high, chart.minimum, chart.maximum, PRICE_TOP, PRICE_HEIGHT);
                const lowY = yScale(candle.low, chart.minimum, chart.maximum, PRICE_TOP, PRICE_HEIGHT);
                const bodyWidth = Math.max(1.5, Math.min(chart.step * 0.64, 12));
                const bodyTop = Math.min(openY, closeY);
                const bodyHeight = Math.max(1.2, Math.abs(openY - closeY));
                const volumeHeight = (candle.volume / chart.volumeMaximum) * VOLUME_HEIGHT;
                return (
                  <g key={candle.date} className={rising ? "kline-rise" : "kline-fall"}>
                    <line x1={x} x2={x} y1={highY} y2={lowY} />
                    <rect x={x - bodyWidth / 2} y={bodyTop} width={bodyWidth} height={bodyHeight} />
                    <rect className="kline-volume" x={x - bodyWidth / 2} y={VOLUME_TOP + VOLUME_HEIGHT - volumeHeight} width={bodyWidth} height={volumeHeight} />
                  </g>
                );
              })}

              {[30, 50, 70].map((level) => {
                const y = RSI_TOP + ((100 - level) / 100) * RSI_HEIGHT;
                return <g key={level}><line className={`rsi-reference rsi-reference--${level}`} x1={PLOT_LEFT} x2={PLOT_RIGHT} y1={y} y2={y} /><text className="kline-axis" x={PLOT_RIGHT + 8} y={y + 4}>{level}</text></g>;
              })}
              <polyline
                className="rsi-line"
                points={visible.map((candle, index) => candle.rsi14 === null ? null : `${PLOT_LEFT + chart.step * (index + 0.5)},${RSI_TOP + ((100 - candle.rsi14) / 100) * RSI_HEIGHT}`).filter(Boolean).join(" ")}
              />

              {series.trades.flatMap((trade, tradeIndex) => {
                const index = visible.findIndex((candle) => candle.date === trade.date);
                if (index < 0) return [];
                const sameSideBefore = series.trades.slice(0, tradeIndex).filter((item) => item.date === trade.date && item.side === trade.side).length;
                const x = PLOT_LEFT + chart.step * (index + 0.5);
                const candle = visible[index];
                if (!candle) return [];
                const anchor = trade.side === "buy"
                  ? yScale(candle.low, chart.minimum, chart.maximum, PRICE_TOP, PRICE_HEIGHT) + 18 + sameSideBefore * 17
                  : yScale(candle.high, chart.minimum, chart.maximum, PRICE_TOP, PRICE_HEIGHT) - 10 - sameSideBefore * 17;
                return [
                  <g key={`${trade.date}-${trade.side}-${tradeIndex}`} className={`trade-marker trade-marker--${trade.side}`}>
                    <title>{`${trade.side === "buy" ? "买入" : "卖出"} · ${trade.price.toFixed(2)} · ${compactMoney(trade.amount_cny)}`}</title>
                    <circle cx={x} cy={anchor} r={8} />
                    <text x={x} y={anchor + 3.5}>{trade.side === "buy" ? "B" : "S"}</text>
                  </g>,
                ];
              })}

              {hoverIndex !== null && hoverIndex < visible.length && (
                <g className="kline-crosshair">
                  <line x1={PLOT_LEFT + chart.step * (hoverIndex + 0.5)} x2={PLOT_LEFT + chart.step * (hoverIndex + 0.5)} y1={PRICE_TOP} y2={RSI_TOP + RSI_HEIGHT} />
                </g>
              )}

              {visible.filter((_, index) => index % Math.max(1, Math.ceil(visible.length / 7)) === 0).map((candle) => {
                const index = visible.findIndex((item) => item.date === candle.date);
                return <text key={candle.date} className="kline-date-axis" x={PLOT_LEFT + chart.step * (index + 0.5)} y={658}>{dateLabel(candle.date)}</text>;
              })}
            </svg>
          </div>
          <p className="kline-footnote">
            来源：AKShare 真实日线。红涨绿跌；RSI 采用 Wilder（威尔德）14 日平滑算法，参考线为 30 / 50 / 70。滚轮缩放，按住拖动查看历史，休市日不留空白。
          </p>
        </>
      )}
    </section>
  );
}
