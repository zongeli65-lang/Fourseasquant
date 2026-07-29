import {
  FormEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

type OverviewItem = {
  code: string;
  name: string;
  entry_reason:
    | "strategy_target"
    | "manual_investigation"
    | "explicit_watchlist"
    | "eastmoney_hot"
    | "tonghuashun_hot";
  monitoring_active: boolean;
  eastmoney: PlatformAggregate | null;
  sina: PlatformAggregate | null;
  tonghuashun: PlatformAggregate | null;
};

type OverviewResponse = { items: OverviewItem[] };

type PlatformAggregate = {
  actual_date: string;
  content_count: number;
  valid_count: number;
  favorable_count: number;
  unfavorable_count: number;
  disputed_count: number;
  unknown_count: number;
  classified_count: number;
  neutral_count: number;
  unrelated_count: number;
  low_confidence_count: number;
  sample_capped: boolean;
  sample_limit: number | null;
  rules_version: string;
  direction: "favorable" | "unfavorable" | "balanced" | null;
  direction_status: "collecting" | "insufficient_sample" | "published";
};

type CollectionJob = {
  id: number;
  platform: "eastmoney" | "sina" | "tonghuashun";
  code: string;
  start_date: string;
  end_date: string;
  trigger: "automatic" | "manual";
  classification_version: string;
  status: "pending" | "running" | "succeeded" | "failed" | "blocked";
  cursor: string | null;
  updated_at: string;
  error_summary: string | null;
  target_source: "strategy" | "manual" | "legacy_discovery";
  target_version: string;
  sample_capped: boolean;
  sample_limit: number | null;
};

type StrategyTargets = {
  actual_date: string;
  strategy_version: string;
  codes: string[];
  published_at: string;
};

type BatchStatus = {
  running: boolean;
  stop_requested: boolean;
  current_job_id: number | null;
  current_code: string | null;
  latest_end_date: string | null;
  started_at: string | null;
  updated_at: string | null;
  error_summary: string | null;
  slices_processed: number;
  succeeded: number;
  blocked: number;
  failed: number;
  remaining: number;
  stopped: boolean;
  classification_model: string;
  prompt_version: string;
  classifier_configured: boolean;
};

type ApiConfigurationStatus = {
  configured: boolean;
  source: "runtime" | "environment" | "keychain" | "unconfigured";
  transient: boolean;
  updated_at: string | null;
};

const initialApiConfiguration: ApiConfigurationStatus = {
  configured: false,
  source: "unconfigured",
  transient: true,
  updated_at: null,
};

const initialBatchStatus: BatchStatus = {
  running: false,
  stop_requested: false,
  current_job_id: null,
  current_code: null,
  latest_end_date: null,
  started_at: null,
  updated_at: null,
  error_summary: null,
  slices_processed: 0,
  succeeded: 0,
  blocked: 0,
  failed: 0,
  remaining: 0,
  stopped: false,
  classification_model: "deepseek-v4-pro",
  prompt_version: "public-opinion-deepseek-v1",
  classifier_configured: false,
};

const entryReasonLabels: Record<OverviewItem["entry_reason"], string> = {
  strategy_target: "策略指定",
  manual_investigation: "手动调查",
  explicit_watchlist: "重点监测",
  eastmoney_hot: "东方财富热榜",
  tonghuashun_hot: "同花顺热榜",
};

const platformLabels: Record<CollectionJob["platform"], string> = {
  eastmoney: "东方财富",
  sina: "新浪股吧",
  tonghuashun: "同花顺",
};

const jobStatusLabels: Record<CollectionJob["status"], string> = {
  pending: "待执行",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  blocked: "来源阻塞",
};

export function PublicOpinionPage({ targetDate }: { targetDate: string }) {
  const [items, setItems] = useState<OverviewItem[]>([]);
  const [jobs, setJobs] = useState<CollectionJob[]>([]);
  const [strategyTargets, setStrategyTargets] = useState<StrategyTargets | null>(null);
  const [message, setMessage] = useState("");
  const [manualCode, setManualCode] = useState("");
  const [manualStartDate, setManualStartDate] = useState(targetDate);
  const [manualEndDate, setManualEndDate] = useState(targetDate);
  const [runningJobId, setRunningJobId] = useState<number | null>(null);
  const [batchStatus, setBatchStatus] = useState<BatchStatus>(initialBatchStatus);
  const [apiConfiguration, setApiConfiguration] = useState<ApiConfigurationStatus>(
    initialApiConfiguration,
  );
  const [apiConfigurationSaving, setApiConfigurationSaving] = useState(false);
  const apiKeyInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const [
      overviewResponse,
      jobsResponse,
      batchResponse,
      targetsResponse,
      apiConfigurationResponse,
    ] = await Promise.all([
      fetch("/api/public-opinion/overview"),
      fetch("/api/public-opinion/jobs"),
      fetch("/api/public-opinion/jobs/batch-status"),
      fetch(
        `/api/public-opinion/strategy-targets/latest?actual_date=${encodeURIComponent(targetDate)}`,
      ),
      fetch("/api/public-opinion/api-configuration"),
    ]);
    if (
      !overviewResponse.ok
      || !jobsResponse.ok
      || !batchResponse.ok
      || !targetsResponse.ok
      || !apiConfigurationResponse.ok
    ) {
      throw new Error("舆论监测接口读取失败");
    }
    const overview = (await overviewResponse.json()) as OverviewResponse;
    setItems(overview.items);
    setJobs((await jobsResponse.json()) as CollectionJob[]);
    setBatchStatus((await batchResponse.json()) as BatchStatus);
    setStrategyTargets((await targetsResponse.json()) as StrategyTargets | null);
    setApiConfiguration(
      (await apiConfigurationResponse.json()) as ApiConfigurationStatus,
    );
  }, [targetDate]);

  useEffect(() => {
    void refresh().catch(() => setMessage("暂时无法读取舆论监测数据。"));
  }, [refresh]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refresh().catch(() => {
        setMessage("进度刷新暂时失败，采集任务仍在后端执行。");
      });
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function createManualInvestigation(event: FormEvent) {
    event.preventDefault();
    const response = await fetch("/api/public-opinion/manual-jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: manualCode,
        start_date: manualStartDate,
        end_date: manualEndDate,
      }),
    });
    if (!response.ok) {
      setMessage("手动调查任务创建失败，请检查股票代码和日期范围。");
      return;
    }
    const created = (await response.json()) as CollectionJob[];
    setMessage(`${manualCode} 已生成 ${created.length} 个定向调查任务。`);
    setManualCode("");
    await refresh();
  }

  async function scheduleToday() {
    const response = await fetch("/api/public-opinion/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actual_date: targetDate }),
    });
    if (!response.ok) {
      setMessage("今日采集任务生成失败。");
      return;
    }
    const created = (await response.json()) as CollectionJob[];
    setMessage(
      created.length > 0
        ? `已为 ${created.length} 只策略目标生成今日调查任务。`
        : strategyTargets === null
          ? "策略板块尚未发布今日舆论调查目标，没有调用任何采集接口。"
          : "今日策略目标任务已经生成，无需重复创建。",
    );
    await refresh();
  }

  async function runJob(job: CollectionJob) {
    setRunningJobId(job.id);
    setMessage(
      `正在低频采集 ${platformLabels[job.platform]} ${job.code}，请稍候。`,
    );
    try {
      const response = await fetch(`/api/public-opinion/jobs/${job.id}/run`, {
        method: "POST",
      });
      if (!response.ok) {
        setMessage("任务执行失败，请查看任务状态。");
        return;
      }
      const result = (await response.json()) as CollectionJob;
      setMessage(
        result.status === "succeeded"
          ? `${platformLabels[result.platform]} ${result.code} 已完成采集。`
          : result.error_summary ?? "来源暂时无法完成采集。",
      );
      await refresh();
    } finally {
      setRunningJobId(null);
    }
  }

  async function startBatch() {
    const response = await fetch("/api/public-opinion/jobs/run-batch", {
      method: "POST",
    });
    if (!response.ok) {
      setMessage("后端批次启动失败，请查看服务日志。");
      return;
    }
    const status = (await response.json()) as BatchStatus;
    setBatchStatus(status);
    setMessage(
      status.running
        ? "后端轮转已启动；现在刷新或关闭页面都不会中断采集。"
        : status.error_summary ?? "当前没有可执行的采集任务。",
    );
    await refresh();
  }

  async function stopBatch() {
    const response = await fetch("/api/public-opinion/jobs/stop-batch", {
      method: "POST",
    });
    if (!response.ok) {
      setMessage("停止请求发送失败。");
      return;
    }
    setBatchStatus((await response.json()) as BatchStatus);
    setMessage("已请求停止；后端将在当前请求分片结束后停下。");
  }

  async function saveApiConfiguration(event: FormEvent) {
    event.preventDefault();
    const apiKey = apiKeyInput.current?.value ?? "";
    if (apiKey.trim().length < 16) {
      setMessage("API 密钥长度不足，请检查后重新输入。");
      return;
    }
    setApiConfigurationSaving(true);
    try {
      const response = await fetch("/api/public-opinion/api-configuration", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey }),
      });
      if (!response.ok) {
        const error = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        setMessage(error?.detail ?? "API 密钥配置失败。");
        return;
      }
      const configured = (await response.json()) as ApiConfigurationStatus;
      setApiConfiguration(configured);
      setBatchStatus((status) => ({
        ...status,
        classifier_configured: configured.configured,
      }));
      if (apiKeyInput.current) apiKeyInput.current.value = "";
      setMessage("DeepSeek API 密钥已保存到 macOS 钥匙串，后台会自动采集。");
    } catch {
      setMessage("无法连接本机后端，API 密钥未保存。");
    } finally {
      setApiConfigurationSaving(false);
    }
  }

  async function clearApiConfiguration() {
    setApiConfigurationSaving(true);
    try {
      const response = await fetch("/api/public-opinion/api-configuration", {
        method: "DELETE",
      });
      if (!response.ok) {
        const error = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        setMessage(error?.detail ?? "API 密钥清除失败。");
        return;
      }
      const configured = (await response.json()) as ApiConfigurationStatus;
      setApiConfiguration(configured);
      setBatchStatus((status) => ({
        ...status,
        classifier_configured: configured.configured,
      }));
      setMessage(
        configured.configured
          ? "钥匙串密钥已清除，当前恢复使用终端环境变量配置。"
          : "DeepSeek API 密钥已从 macOS 钥匙串清除。",
      );
    } catch {
      setMessage("无法连接本机后端，钥匙串密钥没有被清除。");
    } finally {
      setApiConfigurationSaving(false);
    }
  }

  const apiConfigurationLabel = {
    runtime: "前端临时配置已生效",
    environment: "终端环境变量已生效",
    keychain: "macOS 钥匙串配置已生效",
    unconfigured: "尚未配置",
  }[apiConfiguration.source];

  function aggregateText(aggregate: PlatformAggregate | null): string {
    if (aggregate === null) return "尚未采集";
    const capPrefix = aggregate.sample_capped
      ? "已按采集上限截断 · "
      : "";
    if (aggregate.direction_status === "collecting") {
      return `${aggregate.actual_date} · ${capPrefix}已分类 ${aggregate.classified_count}/${aggregate.content_count} · 采集中`;
    }
    if (aggregate.direction_status === "insufficient_sample") {
      return `${aggregate.actual_date} · ${capPrefix}相关 ${aggregate.valid_count} 条，样本不足`;
    }
    const directionLabel = {
      favorable: "偏利好",
      unfavorable: "偏利空",
      balanced: "均衡",
    }[aggregate.direction ?? "balanced"];
    return `${aggregate.actual_date} · ${capPrefix}${directionLabel} · 相关 ${aggregate.valid_count}/${aggregate.content_count} 条`;
  }

  const currentVersionJobs = jobs.filter(
    (job) => (
      job.platform === "sina"
      && job.classification_version === batchStatus.prompt_version
      && (job.target_source === "strategy" || job.target_source === "manual")
    ),
  );
  const archivedJobCount = jobs.filter(
    (job) => (
      job.platform === "sina"
      && (
        job.classification_version !== batchStatus.prompt_version
        || job.target_source === "legacy_discovery"
      )
    ),
  ).length;
  const sinaAutomaticJobs = currentVersionJobs.filter(
    (job) => job.trigger === "automatic" && job.target_source === "strategy",
  );
  const latestEndDate = sinaAutomaticJobs.reduce(
    (latest, job) => job.end_date > latest ? job.end_date : latest,
    "",
  );
  const currentBatchJobs = sinaAutomaticJobs.filter(
    (job) => job.end_date === latestEndDate,
  );
  const pendingCount = currentBatchJobs.filter((job) => job.status === "pending").length;
  const runningCount = currentBatchJobs.filter((job) => job.status === "running").length;
  const succeededCount = currentBatchJobs.filter((job) => job.status === "succeeded").length;
  const interruptedCount = currentBatchJobs.filter(
    (job) => job.status === "failed" || job.status === "blocked",
  ).length;
  const runnableCount = currentBatchJobs.filter(
    (job) => (
      job.status === "pending"
      || job.status === "failed"
      || job.status === "running"
    ),
  ).length;
  const batchRunning = batchStatus.running;
  const partialCount = currentBatchJobs.filter(
    (job) => job.status === "pending" && job.cursor !== null,
  ).length;
  const finishedCount = succeededCount + interruptedCount;
  const coveredCount = currentBatchJobs.filter(
    (job) => job.status !== "pending" || job.cursor !== null,
  ).length;
  const collectedItems = items.filter((item) => item.sina !== null);
  const sortedItems = items.slice().sort((left, right) => {
    const resultDifference = Number(right.sina !== null) - Number(left.sina !== null);
    if (resultDifference !== 0) return resultDifference;
    return left.code.localeCompare(right.code);
  });
  const collectedContentCount = collectedItems.reduce(
    (sum, item) => sum + (item.sina?.content_count ?? 0),
    0,
  );
  const classifiedContentCount = collectedItems.reduce(
    (sum, item) => sum + (item.sina?.classified_count ?? 0),
    0,
  );
  const conclusionCount = items.filter(
    (item) => item.sina?.direction_status === "published",
  ).length;

  function breakdownStyle(
    value: number,
    aggregate: PlatformAggregate,
  ): { width: string } {
    return {
      width: aggregate.content_count === 0
        ? "0%"
        : `${Math.max(2, value / aggregate.content_count * 100)}%`,
    };
  }

  function jobHeartbeat(job: CollectionJob): string {
    const time = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date(job.updated_at));
    if (job.status !== "running") return `更新于 ${time}`;
    const phase = job.cursor?.includes("\"phase\":\"replies\"")
      ? "正在翻取回复"
      : "正在翻取主题";
    return `${phase} · 心跳 ${time}`;
  }

  return (
    <div className="public-opinion-page">
      <section className="public-opinion-hero">
        <div>
          <p className="section-kicker">公开讨论 · DeepSeek V4 Pro 分类</p>
          <h2>舆论监测</h2>
          <p>
            自动调查对象只来自策略板块发布的有序股票代码，每天最多 10 只；不再从财经热榜批量生成任务。新浪股吧负责低频采集，DeepSeek V4 Pro 负责五分类。
          </p>
        </div>
        <div className="public-opinion-validity">
          <strong>时间效力</strong>
          <span>当日、近 3 日、近 7 日</span>
          <small>单股窗口最多 300 条；单主题回复最多 50 条。</small>
          <small>按发布时间从新到旧；相关内容少于 10 条时不发布方向。</small>
        </div>
      </section>

      {message && <p className="public-opinion-message" role="status">{message}</p>}

      <section className="public-opinion-progress-panel">
        <div className="public-opinion-progress-heading">
          <div>
            <p className="section-kicker">当前自动批次</p>
            <h3>{latestEndDate || "尚未生成任务"}</h3>
            <small>
              {batchStatus.classification_model} ·
              {batchStatus.classifier_configured ? " API 已配置" : " 等待配置 API 密钥"}
            </small>
          </div>
          <div className="public-opinion-progress-actions">
            <button
              type="button"
              disabled={
                batchRunning
                || runnableCount === 0
                || !batchStatus.classifier_configured
              }
              onClick={() => void startBatch()}
            >
              {batchRunning
                ? "后台批次运行中"
                : runningCount > 0
                  ? "恢复中断批次"
                  : "启动后台轮转"}
            </button>
            {batchRunning && (
              <button
                type="button"
                className="secondary"
                disabled={batchStatus.stop_requested}
                onClick={() => void stopBatch()}
              >
                {batchStatus.stop_requested ? "等待当前分片结束" : "停止后台轮转"}
              </button>
            )}
          </div>
        </div>
        <div className="public-opinion-stat-grid">
          <div><span>待执行</span><strong>{pendingCount}</strong></div>
          <div><span>等待下一分片</span><strong>{partialCount}</strong></div>
          <div><span>执行中</span><strong>{Math.max(runningCount, batchRunning ? 1 : 0)}</strong></div>
          <div><span>当前版成功</span><strong>{succeededCount}</strong></div>
          <div><span>失败或阻塞</span><strong>{interruptedCount}</strong></div>
          <div><span>策略目标</span><strong>{strategyTargets?.codes.length ?? 0}</strong></div>
          <div><span>形成结论</span><strong>{conclusionCount}</strong></div>
          <div><span>最新结果内容</span><strong>{collectedContentCount}</strong></div>
          <div><span>大模型已分类</span><strong>{classifiedContentCount}</strong></div>
        </div>
        <div className="public-opinion-progress-track">
          <div>
            <span>股票覆盖进度</span>
            <strong>
              {currentBatchJobs.length === 0
                ? "0%"
                : `${Math.round(coveredCount / currentBatchJobs.length * 100)}%`}
            </strong>
          </div>
          <progress
            aria-label="采集任务进度"
            value={coveredCount}
            max={Math.max(currentBatchJobs.length, 1)}
          />
          <small>
            {batchRunning
              ? `后端已执行 ${batchStatus.slices_processed} 个请求分片；当前 ${batchStatus.current_code ?? "准备中"}；剩余 ${batchStatus.remaining} 只股票`
              : `已覆盖 ${coveredCount}/${currentBatchJobs.length}；完整完成 ${finishedCount}/${currentBatchJobs.length}`}
          </small>
        </div>
      </section>

      <section className="public-opinion-grid">
        <article className="public-opinion-card public-opinion-api-card">
          <div>
            <p className="section-kicker">本机密钥</p>
            <h3>DeepSeek API 配置</h3>
            <p>
              密钥只保存到当前用户的 macOS 钥匙串，不写入数据库、
              浏览器存储、日志或代码；配置一次后后台重启仍可自动调查。
            </p>
            <small
              className={`public-opinion-api-state ${
                apiConfiguration.configured ? "configured" : ""
              }`}
            >
              {apiConfigurationLabel}
            </small>
          </div>
          <form
            className="public-opinion-api-form"
            onSubmit={(event) => void saveApiConfiguration(event)}
          >
            <label>
              DeepSeek API 密钥
              <input
                ref={apiKeyInput}
                required
                type="password"
                minLength={16}
                maxLength={512}
                name="deepseek-api-key"
                autoComplete="new-password"
                spellCheck={false}
                disabled={batchRunning || apiConfigurationSaving}
                placeholder="在此粘贴密钥"
              />
            </label>
            <div className="public-opinion-api-actions">
              <button
                type="submit"
                disabled={batchRunning || apiConfigurationSaving}
              >
                {apiConfigurationSaving ? "处理中" : "保存到本机钥匙串"}
              </button>
              {(apiConfiguration.source === "runtime"
                || apiConfiguration.source === "keychain") && (
                <button
                  type="button"
                  className="secondary"
                  disabled={batchRunning || apiConfigurationSaving}
                  onClick={() => void clearApiConfiguration()}
                >
                  清除钥匙串密钥
                </button>
              )}
            </div>
          </form>
        </article>

        <article className="public-opinion-card">
          <h3>策略目标自动调查</h3>
          <p>
            只为策略板块当天发布的目标生成新浪任务，严格保持策略顺序。没有策略目标时不建任务、不调用采集接口。
          </p>
          <small>
            {strategyTargets === null
              ? "等待策略板块发布今日目标"
              : `${strategyTargets.strategy_version} · ${strategyTargets.codes.length}/10 只`}
          </small>
          <button
            type="button"
            disabled={strategyTargets === null}
            onClick={() => void scheduleToday()}
          >
            生成策略调查任务
          </button>
        </article>

        <article className="public-opinion-card">
          <h3>手动单股调查</h3>
          <p>只创建指定股票和日期范围的独立任务，不会加入每日自动队列。</p>
          <form onSubmit={(event) => void createManualInvestigation(event)}>
            <label>
              股票代码
              <input
                required
                pattern="\d{6}"
                value={manualCode}
                onChange={(event) => setManualCode(event.target.value)}
                placeholder="000001"
              />
            </label>
            <label>
              开始日期
              <input
                required
                type="date"
                value={manualStartDate}
                onChange={(event) => setManualStartDate(event.target.value)}
              />
            </label>
            <label>
              结束日期
              <input
                required
                type="date"
                value={manualEndDate}
                onChange={(event) => setManualEndDate(event.target.value)}
              />
            </label>
            <button type="submit">创建单股调查</button>
          </form>
        </article>

      </section>

      <section className="public-opinion-table-panel">
        <div>
          <p className="section-kicker">监测总览</p>
          <h3>策略与手动调查对象</h3>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>股票</th>
                <th>进入原因</th>
                <th>监测状态</th>
                <th>新浪股吧舆论</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={4}>尚无调查对象。等待策略板块发布目标，或创建单股调查。</td></tr>
              ) : sortedItems.map((item) => (
                <tr key={item.code}>
                  <td><strong>{item.name}</strong><span>{item.code}</span></td>
                  <td>{entryReasonLabels[item.entry_reason]}</td>
                  <td>{item.monitoring_active ? "有效" : "已停止"}</td>
                  <td>
                    <span>{aggregateText(item.sina)}</span>
                    {item.sina !== null && item.sina.content_count > 0 && (
                      <div
                        className="public-opinion-breakdown"
                        aria-label={`${item.code} 内容分布`}
                      >
                        <i
                          className="favorable"
                          style={breakdownStyle(item.sina.favorable_count, item.sina)}
                          title={`利好 ${item.sina.favorable_count}`}
                        />
                        <i
                          className="unfavorable"
                          style={breakdownStyle(item.sina.unfavorable_count, item.sina)}
                          title={`利空 ${item.sina.unfavorable_count}`}
                        />
                        <i
                          className="disputed"
                          style={breakdownStyle(item.sina.disputed_count, item.sina)}
                          title={`争议 ${item.sina.disputed_count}`}
                        />
                        <i
                          className="neutral"
                          style={breakdownStyle(item.sina.neutral_count, item.sina)}
                          title={`中性 ${item.sina.neutral_count}`}
                        />
                        <i
                          className="unrelated"
                          style={breakdownStyle(item.sina.unrelated_count, item.sina)}
                          title={`无关 ${item.sina.unrelated_count}`}
                        />
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="public-opinion-table-panel">
        <div>
          <p className="section-kicker">采集执行</p>
          <h3>新浪 DeepSeek 采集任务</h3>
          <small>
            仅展示策略目标和手动调查的 {batchStatus.prompt_version} 队列；
            {archivedJobCount} 个旧版或热榜任务保留在数据库审计记录中。
          </small>
        </div>
        <div className="table-scroll">
          <table>
            <thead><tr><th>平台</th><th>股票</th><th>范围</th><th>状态</th><th>采集心跳</th><th>错误摘要</th><th>操作</th></tr></thead>
            <tbody>
              {currentVersionJobs.length === 0 ? (
                <tr><td colSpan={7}>尚无当前版本采集任务，请先生成今日任务。</td></tr>
              ) : currentVersionJobs.slice().reverse().map((job) => (
                <tr key={job.id}>
                  <td>{platformLabels[job.platform]}</td>
                  <td>{job.code}</td>
                  <td>
                    {job.start_date} 至 {job.end_date}
                    <small>
                      {job.target_source === "strategy"
                        ? `策略 · ${job.target_version}`
                        : "手动调查"}
                    </small>
                  </td>
                  <td>
                    {job.status === "succeeded" && job.sample_capped
                      ? "上限完成"
                      : job.status === "pending" && job.cursor !== null
                      ? "等待下一分片"
                      : jobStatusLabels[job.status]}
                  </td>
                  <td>{jobHeartbeat(job)}</td>
                  <td>{job.error_summary ?? "—"}</td>
                  <td>
                    {job.status === "pending" || job.status === "failed" ? (
                      <button
                        type="button"
                        disabled={runningJobId !== null || batchRunning}
                        onClick={() => void runJob(job)}
                      >
                        {runningJobId === job.id ? "执行中…" : "执行任务"}
                      </button>
                    ) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
