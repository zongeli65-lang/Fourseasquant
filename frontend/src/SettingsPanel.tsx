import { useEffect, useState } from "react";

type SettingsData = {
  auto_update_time: string;
  benchmark: string;
  data_adapter: string;
  new_stock_exclusion_days: number;
  benchmark_options: string[];
  data_adapter_options: string[];
};

type SettingsState =
  | { kind: "loading" }
  | { kind: "ready" | "saving" | "saved"; data: SettingsData }
  | { kind: "error"; data?: SettingsData };

const adapterLabels: Record<string, string> = {
  simulation: "确定性模拟",
  simulation_conservative: "保守模拟",
};

type BackfillPreview = {
  start_date: string;
  end_date: string;
  trading_day_count: number;
  trading_days: string[];
};

type BackfillDayResult = {
  id: number;
  target_date: string;
  status: "succeeded" | "failed";
  stage_label: string;
  error_summary: string | null;
};

type BackfillResult = {
  total: number;
  succeeded: number;
  failed: number;
  results: BackfillDayResult[];
};

function beijingDate(offsetDays = 0): string {
  const now = new Date();
  now.setDate(now.getDate() + offsetDays);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function SettingsPanel({
  open,
  onClose,
  onBackfillCompleted,
}: {
  open: boolean;
  onClose: () => void;
  onBackfillCompleted: () => void;
}) {
  const [state, setState] = useState<SettingsState>({ kind: "loading" });
  const [backfillStart, setBackfillStart] = useState(() => beijingDate(-7));
  const [backfillEnd, setBackfillEnd] = useState(() => beijingDate());
  const [backfillPreview, setBackfillPreview] = useState<BackfillPreview | null>(null);
  const [backfillResult, setBackfillResult] = useState<BackfillResult | null>(null);
  const [backfillStatus, setBackfillStatus] = useState<"idle" | "loading" | "running" | "error">("idle");

  useEffect(() => {
    if (!open) {
      return;
    }
    const controller = new AbortController();
    setState({ kind: "loading" });
    void fetch("/api/settings", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`设置接口返回 ${response.status}`);
        }
        return response.json() as Promise<SettingsData>;
      })
      .then((data) => setState({ kind: "ready", data }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setState({ kind: "error" });
      });
    return () => controller.abort();
  }, [open]);

  if (!open) {
    return null;
  }

  const data = "data" in state ? state.data : undefined;
  function updateData(update: Partial<SettingsData>) {
    if (data) {
      setState({ kind: "ready", data: { ...data, ...update } });
    }
  }

  async function save() {
    if (!data) {
      return;
    }
    setState({ kind: "saving", data });
    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          auto_update_time: data.auto_update_time,
          benchmark: data.benchmark,
          data_adapter: data.data_adapter,
          new_stock_exclusion_days: data.new_stock_exclusion_days,
        }),
      });
      if (!response.ok) {
        throw new Error(`设置保存返回 ${response.status}`);
      }
      setState({ kind: "saved", data: (await response.json()) as SettingsData });
    } catch {
      setState({ kind: "error", data });
    }
  }

  function updateBackfillRange(kind: "start" | "end", value: string) {
    if (kind === "start") {
      setBackfillStart(value);
    } else {
      setBackfillEnd(value);
    }
    setBackfillPreview(null);
    setBackfillResult(null);
    setBackfillStatus("idle");
  }

  async function previewBackfill() {
    const requestedStart = backfillStart;
    const requestedEnd = backfillEnd;
    setBackfillStatus("loading");
    setBackfillResult(null);
    try {
      const response = await fetch(
        `/api/backfill/preview?start_date=${encodeURIComponent(requestedStart)}&end_date=${encodeURIComponent(requestedEnd)}`,
      );
      if (!response.ok) {
        throw new Error(`补算预览返回 ${response.status}`);
      }
      const preview = (await response.json()) as BackfillPreview;
      if (preview.start_date !== requestedStart || preview.end_date !== requestedEnd) {
        throw new Error("补算预览范围不一致");
      }
      setBackfillPreview(preview);
      setBackfillStatus("idle");
    } catch {
      setBackfillPreview(null);
      setBackfillStatus("error");
    }
  }

  async function runBackfill() {
    if (!backfillPreview) {
      return;
    }
    setBackfillStatus("running");
    try {
      const response = await fetch("/api/backfill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          start_date: backfillPreview.start_date,
          end_date: backfillPreview.end_date,
        }),
      });
      if (!response.ok) {
        throw new Error(`补算任务返回 ${response.status}`);
      }
      setBackfillResult((await response.json()) as BackfillResult);
      setBackfillStatus("idle");
      onBackfillCompleted();
    } catch {
      setBackfillStatus("error");
    }
  }

  return (
    <div className="settings-layer" role="presentation">
      <button className="settings-backdrop" type="button" aria-label="收起面板背景" disabled={backfillStatus === "running"} onClick={onClose} />
      <aside className="settings-panel" aria-labelledby="settings-title">
        <header>
          <div>
            <p className="section-kicker">本机配置</p>
            <h2 id="settings-title">运行设置</h2>
          </div>
          <button type="button" aria-label="关闭设置" disabled={backfillStatus === "running"} onClick={onClose}>×</button>
        </header>
        {state.kind === "loading" && <p className="settings-message">正在读取设置…</p>}
        {state.kind === "error" && !data && <p className="settings-message">读取设置失败。</p>}
        {data && (
          <div className="settings-form">
            <label>
              <span>自动更新时间</span>
              <input
                type="time"
                value={data.auto_update_time}
                disabled={backfillStatus === "running"}
                onChange={(event) => updateData({ auto_update_time: event.target.value })}
              />
            </label>
            <label>
              <span>默认基准</span>
              <select disabled={backfillStatus === "running"} value={data.benchmark} onChange={(event) => updateData({ benchmark: event.target.value })}>
                {data.benchmark_options.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>
            <label>
              <span>数据源适配器</span>
              <select disabled={backfillStatus === "running"} value={data.data_adapter} onChange={(event) => updateData({ data_adapter: event.target.value })}>
                {data.data_adapter_options.map((option) => (
                  <option key={option} value={option}>{adapterLabels[option] ?? option}</option>
                ))}
              </select>
            </label>
            <label>
              <span>新股排除交易日</span>
              <input
                type="number"
                min="0"
                max="365"
                value={data.new_stock_exclusion_days}
                disabled={backfillStatus === "running"}
                onChange={(event) => updateData({ new_stock_exclusion_days: Number(event.target.value) })}
              />
            </label>
            <p className="settings-security-note">
              仅显示非敏感配置。访问令牌保留在本机安全环境中，不通过网页读取或保存。
            </p>
            <div className="settings-actions">
              <span data-testid="settings-save-status">
                {state.kind === "saving" ? "保存中" : state.kind === "saved" ? "保存成功" : state.kind === "error" ? "保存失败" : "设置已载入"}
              </span>
              <button type="button" disabled={state.kind === "saving" || backfillStatus === "running"} onClick={() => void save()}>
                保存设置
              </button>
            </div>

            <section className="backfill-settings" aria-labelledby="backfill-title">
              <div>
                <p className="section-kicker">历史任务</p>
                <h3 id="backfill-title">日期范围补算</h3>
              </div>
              <div className="backfill-range">
                <label>
                  <span>补算开始日期</span>
                  <input type="date" value={backfillStart} disabled={backfillStatus === "loading" || backfillStatus === "running"} onChange={(event) => updateBackfillRange("start", event.target.value)} />
                </label>
                <label>
                  <span>补算结束日期</span>
                  <input type="date" value={backfillEnd} disabled={backfillStatus === "loading" || backfillStatus === "running"} onChange={(event) => updateBackfillRange("end", event.target.value)} />
                </label>
              </div>
              <div className="backfill-summary" data-testid="backfill-preview-count">
                {backfillStatus === "loading" && "正在计算交易日…"}
                {backfillStatus === "running" && backfillPreview && `正在逐日补算 ${backfillPreview.trading_day_count} 个交易日…`}
                {backfillStatus === "error" && "补算请求失败，请检查日期范围。"}
                {backfillPreview && backfillStatus !== "loading" && `预计处理 ${backfillPreview.trading_day_count} 个交易日`}
                {!backfillPreview && backfillStatus === "idle" && "请先预览实际交易日数量"}
              </div>
              {backfillResult && (
                <>
                  <p className="backfill-result" data-testid="backfill-result">
                    补算完成：成功 {backfillResult.succeeded} 日，失败 {backfillResult.failed} 日。
                  </p>
                  <div className="backfill-day-results" data-testid="backfill-day-results">
                    {backfillResult.results.map((item) => (
                      <div key={item.id}>
                        <span>{item.target_date}</span>
                        <strong className={item.status === "failed" ? "history-failed" : "history-succeeded"}>
                          {item.status === "failed" ? `失败 · ${item.stage_label}` : "成功"}
                        </strong>
                      </div>
                    ))}
                  </div>
                </>
              )}
              <div className="backfill-buttons">
                <button type="button" disabled={backfillStatus === "loading" || backfillStatus === "running"} onClick={() => void previewBackfill()}>预览交易日</button>
                <button type="button" disabled={!backfillPreview || backfillStatus === "running"} onClick={() => void runBackfill()}>{backfillStatus === "running" ? "正在补算…" : "确认补算"}</button>
              </div>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}
