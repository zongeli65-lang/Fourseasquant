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

export function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [state, setState] = useState<SettingsState>({ kind: "loading" });

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

  return (
    <div className="settings-layer" role="presentation">
      <button className="settings-backdrop" type="button" aria-label="收起面板背景" onClick={onClose} />
      <aside className="settings-panel" aria-labelledby="settings-title">
        <header>
          <div>
            <p className="section-kicker">本机配置</p>
            <h2 id="settings-title">运行设置</h2>
          </div>
          <button type="button" aria-label="关闭设置" onClick={onClose}>×</button>
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
                onChange={(event) => updateData({ auto_update_time: event.target.value })}
              />
            </label>
            <label>
              <span>默认基准</span>
              <select value={data.benchmark} onChange={(event) => updateData({ benchmark: event.target.value })}>
                {data.benchmark_options.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>
            <label>
              <span>数据源适配器</span>
              <select value={data.data_adapter} onChange={(event) => updateData({ data_adapter: event.target.value })}>
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
              <button type="button" disabled={state.kind === "saving"} onClick={() => void save()}>
                保存设置
              </button>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}
