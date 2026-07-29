import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type RuntimeStatus = {
  enabled: boolean;
  state: "paused" | "running" | "pausing" | "error";
  paused_at: string | null;
  resumed_at: string | null;
  catchup_from: string | null;
  worker_heartbeat_at: string | null;
  worker_online: boolean;
  last_poll_at: string | null;
  last_model_run_at: string | null;
  last_cleanup_at: string | null;
  error_summary: string | null;
  queued_count: number;
  running_count: number;
  discovered_count: number;
  triaged_discovery_count: number;
  untriaged_count: number;
  research_material_count: number;
  event_cluster_count: number;
  investigated_company_count: number;
  freshness_cutoff: string;
  deep_hunt_count: number;
  completed_selection_count: number;
  invalid_event_count: number;
  failed_hunt_count: number;
  published_count: number;
  next_scheduled_scan_at: string | null;
  updated_at: string;
};

type HuntRecord = {
  request_id: string;
  trigger_method: string;
  trigger_type: string;
  trigger_content: string;
  source_url: string | null;
  as_of_time: string;
  priority: number;
  status: string;
  requested_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_summary: string | null;
};

type SourceRecord = {
  source_id: string;
  source_version: number;
  source_name: string;
  base_url: string;
  domain: string;
  source_tier: number;
  source_type: string;
  categories: string[];
  access_class: string;
  lifecycle_state: "active" | "observing" | "disabled";
  poll_interval_minutes: number;
  allow_browser: boolean;
  config_version: string;
  effective_at: string;
};

type SelectionCandidate = {
  rank: number;
  code: string;
  name: string;
  chain_node: string;
  benefit_type: "direct" | "indirect" | "conditional";
  profit_transmission_path: string[];
  business_materiality: {
    revenue_share: number | null;
    gross_profit_share: number | null;
    official_qualitative_basis: string | null;
  };
  realization: {
    status: string;
    expected_start: string | null;
    basis: string;
  };
  uncertainties: string[];
};

type SelectionRecord = {
  selection_id: string;
  selection_version: number;
  event_id: string;
  as_of_time: string;
  completed_at: string;
  status: "selected" | "empty" | "evidence_insufficient";
  event: {
    title: string;
    affected_product_or_service: string;
    gap_node: string;
    trigger_types: string[];
  };
  candidates: SelectionCandidate[];
  summary: string;
  unresolved_items: string[];
};

type PageState =
  | { kind: "loading" }
  | {
      kind: "ready" | "updating";
      status: RuntimeStatus;
      hunts: HuntRecord[];
      sources: SourceRecord[];
      selections: SelectionRecord[];
    }
  | { kind: "error"; message: string };

const triggerLabels: Record<string, string> = {
  keyword: "关键词或产业",
  url: "公开网页网址",
  message: "消息正文",
};

const methodLabels: Record<string, string> = {
  manual: "手动",
  resume_catchup: "恢复补搜",
  automatic: "自动发现",
  scheduled_scan: "定时巡检",
  new_evidence: "新证据",
  historical_replay: "历史回放",
};

const statusLabels: Record<string, string> = {
  queued: "等待分析",
  paused: "已暂停",
  running: "正在分析",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const sourceTypeLabels: Record<string, string> = {
  exchange_disclosure: "交易所披露",
  government_statistics: "政府统计",
  government: "政府信息",
  news_discovery: "新闻发现",
  financial_media: "财经媒体",
  social_discovery: "社交平台财经线索",
};

function formatBeijingDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}

function RollingNumber({ value }: { value: number }) {
  const [display, setDisplay] = useState(value);
  const current = useRef(value);

  useEffect(() => {
    const startValue = current.current;
    const difference = value - startValue;
    if (difference === 0) return;
    const startedAt = performance.now();
    let frame = 0;
    const animate = (timestamp: number) => {
      const progress = Math.min(1, (timestamp - startedAt) / 900);
      const eased = 1 - (1 - progress) ** 3;
      const nextValue = Math.round(startValue + difference * eased);
      current.current = nextValue;
      setDisplay(nextValue);
      if (progress < 1) frame = requestAnimationFrame(animate);
    };
    frame = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frame);
  }, [value]);

  return <>{display}</>;
}

function LiveProcessingClock({
  active,
  anchor,
}: {
  active: boolean;
  anchor: string | null;
}) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [active]);

  if (!active) return <>等待下一轮自动巡检</>;
  const anchorTime = anchor ? new Date(anchor).getTime() : now;
  const elapsed = Math.max(0, Math.floor((now - anchorTime) / 1_000));
  return (
    <>
      <i className="industry-chain-live-dot" aria-hidden="true" />
      自动处理中 · 本批 {elapsed} 秒
    </>
  );
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(payload?.detail ?? `接口返回 ${response.status}`);
  }
  return (await response.json()) as T;
}

export function IndustryChainLeadersPage() {
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [triggerType, setTriggerType] = useState<"keyword" | "url" | "message">(
    "keyword",
  );
  const [content, setContent] = useState("");
  const [submitState, setSubmitState] = useState<
    "idle" | "submitting" | "succeeded" | "error"
  >("idle");
  const [submitMessage, setSubmitMessage] = useState("");
  const [deletingSelection, setDeletingSelection] = useState<string | null>(
    null,
  );
  const [selectionMessage, setSelectionMessage] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [status, hunts, sources, selections] = await Promise.all([
        fetchJson<RuntimeStatus>("/api/industry-chain/status"),
        fetchJson<HuntRecord[]>("/api/industry-chain/hunts?limit=20"),
        fetchJson<SourceRecord[]>("/api/industry-chain/sources"),
        fetchJson<SelectionRecord[]>("/api/industry-chain/selections?limit=10"),
      ]);
      setState({ kind: "ready", status, hunts, sources, selections });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "产业链接口读取失败",
      });
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const data = state.kind === "ready" || state.kind === "updating" ? state : null;
  const sourceSummary = useMemo(() => {
    if (!data) return { active: 0, observing: 0 };
    return {
      active: data.sources.filter((source) => source.lifecycle_state === "active").length,
      observing: data.sources.filter(
        (source) => source.lifecycle_state === "observing",
      ).length,
    };
  }, [data]);

  async function toggle(enabled: boolean) {
    if (!data) return;
    setState({ ...data, kind: "updating" });
    try {
      await fetchJson<RuntimeStatus>("/api/industry-chain/control", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      await refresh();
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "开关更新失败",
      });
    }
  }

  async function submitHunt() {
    if (!data || !content.trim()) return;
    setSubmitState("submitting");
    setSubmitMessage("");
    try {
      await fetchJson<HuntRecord>("/api/industry-chain/hunts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger_type: triggerType, content }),
      });
      setContent("");
      setSubmitState("succeeded");
      setSubmitMessage("狩猎请求已进入队列。");
      await refresh();
    } catch (error) {
      setSubmitState("error");
      setSubmitMessage(
        error instanceof Error ? error.message : "手动狩猎提交失败",
      );
    }
  }

  async function deleteSelection(selection: SelectionRecord) {
    const confirmed = window.confirm(
      `确定从候选池删除“${selection.event.title}”吗？\n\n删除后前端不再展示，底层审计记录仍保留，避免误删正式证据。`,
    );
    if (!confirmed) return;
    const key = `${selection.selection_id}:${selection.selection_version}`;
    setDeletingSelection(key);
    setSelectionMessage("");
    try {
      await fetchJson(
        `/api/industry-chain/selections/${encodeURIComponent(
          selection.selection_id,
        )}/${selection.selection_version}`,
        { method: "DELETE" },
      );
      setSelectionMessage("已从候选池删除该分析。");
      await refresh();
    } catch (error) {
      setSelectionMessage(
        error instanceof Error ? error.message : "候选分析删除失败",
      );
    } finally {
      setDeletingSelection(null);
    }
  }

  if (state.kind === "loading") {
    return <section className="industry-chain-message">正在读取产业链狩猎状态…</section>;
  }
  if (state.kind === "error") {
    return (
      <section className="industry-chain-message industry-chain-message--error">
        <strong>产业链模块暂时不可用</strong>
        <span>{state.message}</span>
        <button type="button" onClick={() => void refresh()}>
          重新读取
        </button>
      </section>
    );
  }

  const { status, hunts, sources, selections } = state;
  const isUpdating = state.kind === "updating";
  const serviceLabel = status.state === "pausing"
    ? "正在完成当前任务"
    : !status.enabled
      ? "已暂停"
    : status.worker_online
      ? "正在监控"
      : "已开启 · 执行器未在线";
  const progress =
    status.discovered_count === 0
      ? 0
      : (status.triaged_discovery_count / status.discovered_count) * 100;
  const automaticallyProcessing =
    status.enabled && status.worker_online && status.untriaged_count > 0;

  return (
    <div className="industry-chain-page">
      <section className="industry-chain-control">
        <div>
          <p className="section-kicker">本机专用控制</p>
          <h2>产业链供需狩猎</h2>
          <p>
            开启后后台会自行持续采集、初筛、深挖和发布，不需要保持网页打开。关闭后完成手上这一批再停止；临时运行记录按留存规则清理，正式证据继续保留。
          </p>
        </div>
        <div className="industry-chain-switch-group">
          <span className={`industry-chain-service industry-chain-service--${status.enabled ? "on" : "off"}`}>
            {serviceLabel}
          </span>
          <button
            className={`industry-chain-switch ${status.enabled ? "industry-chain-switch--on" : ""}`}
            type="button"
            role="switch"
            aria-checked={status.enabled}
            disabled={isUpdating || status.state === "pausing"}
            onClick={() => void toggle(!status.enabled)}
          >
            <span />
            {status.state === "pausing"
              ? "正在收尾"
              : status.enabled
                ? "关闭自动狩猎"
                : "开启自动狩猎"}
          </button>
        </div>
      </section>

      <section className="industry-chain-metrics" aria-label="产业链运行摘要">
        <article>
          <span>后台执行器</span>
          <strong>{status.worker_online ? "在线" : "尚未在线"}</strong>
          <small>最近心跳 {formatBeijingDateTime(status.worker_heartbeat_at)}</small>
        </article>
        <article>
          <span>72小时精选新闻</span>
          <strong>
            <RollingNumber value={status.triaged_discovery_count} /> /{" "}
            <RollingNumber value={status.discovered_count} />
          </strong>
          <div
            className="industry-chain-progress"
            role="progressbar"
            aria-label="新闻初筛进度"
            aria-valuemin={0}
            aria-valuemax={status.discovered_count}
            aria-valuenow={status.triaged_discovery_count}
          >
            <i style={{ width: `${progress}%` }} />
          </div>
          <small>
            待初筛 <RollingNumber value={status.untriaged_count} /> ·{" "}
            <LiveProcessingClock
              active={automaticallyProcessing}
              anchor={status.worker_heartbeat_at}
            />
            {" "}· 归并为{" "}
            <RollingNumber value={status.event_cluster_count} /> 个供需事件
            {" "}· 深挖补充材料 {status.research_material_count} 条
          </small>
        </article>
        <article>
          <span>持续监控来源</span>
          <strong>
            <RollingNumber value={sourceSummary.active} />
          </strong>
          <small>未启用观察源 {sourceSummary.observing}</small>
        </article>
        <article>
          <span>调查公司 → 正式候选</span>
          <strong>
            <RollingNumber value={status.investigated_company_count} /> /{" "}
            <RollingNumber value={status.published_count} />
          </strong>
          <small>
            已调查公司 / 有效候选 · 已完成{" "}
            <RollingNumber value={status.completed_selection_count} /> 次正式判定
            {" "}· 排除无效事件{" "}
            <RollingNumber value={status.invalid_event_count} /> 次
            {" "}· 调查失败{" "}
            <RollingNumber value={status.failed_hunt_count} /> 次
            {" "}· 最近模型{" "}
            {formatBeijingDateTime(status.last_model_run_at)} · 最近清理{" "}
            {formatBeijingDateTime(status.last_cleanup_at)}
          </small>
        </article>
      </section>

      {!status.worker_online && (
        <section className="industry-chain-readiness" role="status">
          <div>
            <span>执行边界</span>
            <strong>本地模型已配置，但后台执行器当前不在线</strong>
          </div>
          <p>
            开关和手动请求仍可保存；后台执行器恢复后才会处理队列。关闭开关时不会联网，也不会调用本地模型。
          </p>
        </section>
      )}

      <section className="industry-chain-manual">
        <div className="industry-chain-section-heading">
          <div>
            <p className="section-kicker">用户主动触发</p>
            <h2>立即狩猎</h2>
          </div>
          <span>仍须通过供需、主营和股票范围校验</span>
        </div>
        <div className="industry-chain-manual-grid">
          <label>
            <span>输入类型</span>
            <select
              value={triggerType}
              disabled={!status.enabled || submitState === "submitting"}
              onChange={(event) =>
                setTriggerType(event.target.value as "keyword" | "url" | "message")
              }
            >
              {Object.entries(triggerLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="industry-chain-manual-content">
            <span>{triggerLabels[triggerType]}</span>
            <textarea
              rows={4}
              value={content}
              maxLength={10_000}
              disabled={!status.enabled || submitState === "submitting"}
              placeholder={
                triggerType === "url"
                  ? "https://公开网页地址"
                  : triggerType === "message"
                    ? "粘贴需要核验的消息正文"
                    : "例如：铜精矿供应中断"
              }
              onChange={(event) => {
                setContent(event.target.value);
                setSubmitState("idle");
                setSubmitMessage("");
              }}
            />
          </label>
          <div className="industry-chain-manual-action">
            <span className={submitState === "error" ? "industry-chain-error-copy" : ""}>
              {submitMessage ||
                (!status.enabled
                  ? "请先开启自动狩猎。"
                  : "优先闭环高价值事件；证据缺口会继续定向补搜。")}
            </span>
            <button
              type="button"
              disabled={
                !status.enabled ||
                !content.trim() ||
                submitState === "submitting"
              }
              onClick={() => void submitHunt()}
            >
              {submitState === "submitting" ? "正在提交…" : "立即狩猎"}
            </button>
          </div>
        </div>
      </section>

      <section className="industry-chain-results">
        <div className="industry-chain-section-heading">
          <div>
            <p className="section-kicker">真实发布结果</p>
            <h2>产业链龙头候选</h2>
          </div>
          <span>只展示证据链完整的正式候选</span>
        </div>
        {selectionMessage && (
          <p className="industry-chain-selection-message">{selectionMessage}</p>
        )}
        {selections.length === 0 ? (
          <div className="industry-chain-empty">
            <span>
              {status.untriaged_count > 0
                ? `${status.triaged_discovery_count}/${status.discovered_count}`
                : "0"}
            </span>
            <div>
              <strong>
                {status.untriaged_count > 0
                  ? "本轮新闻仍在初筛"
                  : "本轮尚无证据链完整的候选"}
              </strong>
              <p>
                {status.untriaged_count > 0
                  ? `还有 ${status.untriaged_count} 条待处理；模糊研报、资本市场叙事和无公司兑现证据的材料不会强行生成股票。`
                  : `已完成 ${status.completed_selection_count} 次正式判定；其中只有证据链闭合的结果才计入有效候选，另有 ${status.invalid_event_count} 次事件被排除、${status.failed_hunt_count} 次调查失败。`}
              </p>
            </div>
          </div>
        ) : (
          <div className="industry-chain-selection-list">
            {selections.map((selection) => (
              <article
                className="industry-chain-selection"
                key={`${selection.selection_id}:${selection.selection_version}`}
              >
                <header>
                  <div>
                    <span
                      className={`industry-chain-selection-status industry-chain-selection-status--${selection.status}`}
                    >
                      {selection.status === "selected"
                        ? `已选 ${selection.candidates.length} 只`
                        : selection.status === "empty"
                          ? "空选"
                          : "证据不足"}
                    </span>
                    <div className="industry-chain-selection-actions">
                      <small>{formatBeijingDateTime(selection.completed_at)}</small>
                      <button
                        type="button"
                        disabled={
                          deletingSelection ===
                          `${selection.selection_id}:${selection.selection_version}`
                        }
                        aria-label={`删除分析：${selection.event.title}`}
                        onClick={() => void deleteSelection(selection)}
                      >
                        {deletingSelection ===
                        `${selection.selection_id}:${selection.selection_version}`
                          ? "删除中…"
                          : "删除"}
                      </button>
                    </div>
                  </div>
                  <h3>{selection.event.title}</h3>
                  <p>
                    {selection.event.affected_product_or_service} ·{" "}
                    {selection.event.gap_node}
                  </p>
                </header>
                <p className="industry-chain-selection-summary">
                  {selection.summary}
                </p>
                {selection.candidates.length > 0 && (
                  <div className="industry-chain-candidate-grid">
                    {selection.candidates.map((candidate) => (
                      <section key={`${selection.selection_id}:${candidate.code}`}>
                        <div>
                          <span>第 {candidate.rank} 名</span>
                          <small>{candidate.code}</small>
                        </div>
                        <strong>{candidate.name}</strong>
                        <p>{candidate.chain_node}</p>
                        <ol>
                          {candidate.profit_transmission_path.map((step) => (
                            <li key={step}>{step}</li>
                          ))}
                        </ol>
                        <small>
                          主营毛利润占比{" "}
                          {candidate.business_materiality.gross_profit_share === null
                            ? "未披露"
                            : `${(
                                candidate.business_materiality.gross_profit_share *
                                100
                              ).toFixed(1)}%`}
                        </small>
                        <small>{candidate.realization.basis}</small>
                      </section>
                    ))}
                  </div>
                )}
                {selection.unresolved_items.length > 0 && (
                  <details>
                    <summary>
                      待核查事项 {selection.unresolved_items.length} 项
                    </summary>
                    <ul>
                      {selection.unresolved_items.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="industry-chain-queue">
        <div className="industry-chain-section-heading">
          <div>
            <p className="section-kicker">追加式记录</p>
            <h2>狩猎请求</h2>
          </div>
          <span>最近 {hunts.length} 条</span>
        </div>
        {hunts.length === 0 ? (
          <p className="industry-chain-table-empty">尚无自动或手动狩猎请求。</p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>触发</th>
                  <th>内容</th>
                  <th>优先级</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {hunts.map((hunt) => (
                  <tr key={hunt.request_id}>
                    <td>{formatBeijingDateTime(hunt.requested_at)}</td>
                    <td>{methodLabels[hunt.trigger_method] ?? hunt.trigger_method}</td>
                    <td className="industry-chain-request-content">
                      {hunt.trigger_type === "catchup_window"
                        ? "恢复后补搜，最多 72 小时"
                        : hunt.trigger_content}
                    </td>
                    <td>{hunt.priority}</td>
                    <td>
                      <span className={`industry-chain-queue-status industry-chain-queue-status--${hunt.status}`}>
                        {statusLabels[hunt.status] ?? hunt.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="industry-chain-sources">
        <div className="industry-chain-section-heading">
          <div>
            <p className="section-kicker">版本化来源注册表</p>
            <h2>持续联网来源</h2>
          </div>
          <span>{sources[0]?.config_version ?? "尚无配置"}</span>
        </div>
        <div className="industry-chain-source-grid">
          {sources.map((source) => (
            <article key={`${source.source_id}:${source.source_version}`}>
              <div>
                <span className={`industry-chain-source-state industry-chain-source-state--${source.lifecycle_state}`}>
                  {source.lifecycle_state === "active"
                    ? "持续监控"
                    : source.lifecycle_state === "observing"
                      ? "观察"
                      : "停用"}
                </span>
                <small>等级 {source.source_tier}</small>
              </div>
              <strong>{source.source_name}</strong>
              <p>{sourceTypeLabels[source.source_type] ?? source.source_type}</p>
              <small>
                {source.source_tier <= 3
                  ? "可作为候选证据链的一部分"
                  : "仅用于发现线索，不能单独证明主营受益"}
              </small>
              <a href={source.base_url} target="_blank" rel="noreferrer">
                {source.domain}
              </a>
              <small>检查间隔 {source.poll_interval_minutes} 分钟</small>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
