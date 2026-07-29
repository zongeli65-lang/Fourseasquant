import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

type BoardCandidateMember = {
  code: string;
  name: string;
};

type BoardCandidate = {
  board_id: string;
  source_board_code: string;
  name: string;
  kind: "industry" | "concept";
  members: BoardCandidateMember[];
};

type CapitalActionSignal = {
  insider_window_complete: boolean;
  insider_net_purchase_amount: number | null;
  insider_net_purchase_ratio: number | null;
  insider_adjustment: number | null;
  cancelled_buyback_amount: number;
  cancelled_buyback_ratio: number;
  buyback_bonus: number;
  dilution_ratio: number;
  dilution_penalty: number;
  newly_issued_shares: number;
  shares_before_issuance: number | null;
};

type CapitalActionEventType =
  | "insider_buy"
  | "insider_sell"
  | "cancelled_buyback"
  | "share_dilution"
  | "buyback_announcement";

type CapitalActionEvent = {
  event_key: string;
  code: string;
  name: string;
  event_type: CapitalActionEventType;
  announcement_at: string;
  effective_date: string;
  shares: number | null;
  amount_cny: number | null;
  price_cny: number | null;
  shares_before: number | null;
  reason: string | null;
  confirmed_for_score: boolean;
  exclusion_reason: string | null;
  source_name: string;
  source_url: string;
  source_record_id: string;
  content_sha256: string;
  raw_payload: Record<string, unknown>;
  collected_at: string;
};

type CapitalActionEvidence = {
  code: string;
  as_of_date: string;
  published_at: string;
  events: CapitalActionEvent[];
};

type CapitalActionStatus = {
  requested_date: string;
  publication_date: string | null;
  published_at: string | null;
  status: "ready" | "stale" | "failed" | "unavailable";
  expected_count: number;
  completed_count: number;
  event_count: number;
  confirmed_event_count: number;
  latest_attempt_date: string | null;
  latest_attempt_at: string | null;
  latest_attempt_status: "published" | "failed" | null;
  failure_stage: string | null;
  errors: Record<string, string>;
};

type MonthlySnapshot = {
  rules_version: string;
  code: string;
  as_of_date: string;
  ordinary_pe: number | null;
  adjusted_pe: number | null;
  adjusted_pe_historical_percentile: number | null;
  adjusted_pe_peer_percentile: number | null;
  five_year_adjusted_eps_cagr: number | null;
  positive_growth_years: number;
  dividend_yield: number;
  lynch_growth_value_ratio: number | null;
  lynch_growth_value_label:
    | "poor"
    | "unclassified"
    | "acceptable"
    | "good"
    | "not_applicable";
  net_cash_per_share: number;
  cash_adjusted_price: number;
  cash_adjusted_pe: number | null;
  short_term_interest_bearing_debt: number;
  long_term_interest_bearing_debt: number;
  debt_to_equity: number | null;
  short_term_debt_share: number | null;
  dividend_payout_ratio: number | null;
  consecutive_dividend_years: number;
  dividend_continuously_increased: boolean | null;
  free_cash_flow_per_share: number | null;
  price_to_free_cash_flow: number | null;
  inventory_status: "available" | "insufficient_data" | "not_applicable";
  inventory_growth: number | null;
  revenue_growth: number | null;
  inventory_growth_minus_revenue_growth: number | null;
  pretax_margin: number | null;
  pretax_margin_historical_percentile: number | null;
  pretax_margin_peer_percentile: number | null;
  main_business_name: string | null;
  main_business_profit_share: number | null;
  institution_holding_ratio: number | null;
  institution_holding_change: number | null;
  capital_action_signal: CapitalActionSignal | null;
  true_money_signal_score: number | null;
  floating_market_cap: number;
  floating_market_cap_percentile: number | null;
};

type MonthlyRecord = {
  snapshot: MonthlySnapshot;
  source_urls: string[];
  created_at: string;
};

type PlatformDiscussion = {
  platform: "eastmoney_guba" | "xueqiu";
  actual_date: string;
  code: string;
  post_count: number;
  positive_count: number;
  neutral_count: number;
  negative_count: number;
  raw_heat: number;
  weighted_sentiment: number;
  heat_percentile: number | null;
  likes_missing: boolean;
};

type DiscussionDay = {
  actual_date: string;
  code: string;
  eastmoney_guba: PlatformDiscussion | null;
  xueqiu: PlatformDiscussion | null;
  eastmoney_reference_count: number;
  xueqiu_reference_count: number;
};

type OverviewItem = {
  code: string;
  name: string;
  board_ids: string[];
  lynch: LynchItem | null;
  monthly: MonthlyRecord | null;
  discussion: DiscussionDay | null;
  data_status: "complete" | "partial" | "no_data";
};

type FundamentalOverview = {
  board_source: string | null;
  board_effective_date: string | null;
  board_collected_at: string | null;
  board_complete: boolean | null;
  boards: BoardCandidate[];
  selected_board_id: string | null;
  lynch_actual_data_date: string | null;
  lynch_financial_base_date: string | null;
  lynch_published_at: string | null;
  lynch_total_count: number;
  lynch_calculable_count: number;
  lynch_ranking_eligible_count: number;
  total: number;
  limit: number;
  offset: number;
  items: OverviewItem[];
};

type MonthlySeries = {
  code: string;
  records: MonthlyRecord[];
};

type DiscussionSeries = {
  code: string;
  days: DiscussionDay[];
};

type AnnualAdjustedEps = {
  year: number;
  value: number;
};

type LynchGrade =
  | "exceptional"
  | "excellent"
  | "reasonable"
  | "weak"
  | "earnings_contraction"
  | "insufficient_data";

type LynchItem = {
  target_date: string;
  code: string;
  name: string;
  close: number;
  financial_as_of: string | null;
  latest_notice_date: string | null;
  annual_adjusted_eps: AnnualAdjustedEps[];
  ttm_adjusted_eps: number | null;
  prior_ttm_adjusted_eps: number | null;
  ttm_dividend_per_share: number;
  net_debt_to_equity: number | null;
  financial_safety_status:
    | "available"
    | "invalid_equity"
    | "insufficient_data";
  three_year_cagr: number | null;
  growth_status:
    | "continuous_growth"
    | "non_continuous_growth"
    | "earnings_contraction"
    | "loss_or_nonpositive"
    | "insufficient_data";
  dividend_yield: number | null;
  adjusted_pe: number | null;
  valuation_status:
    | "applicable"
    | "loss_making"
    | "invalid_price"
    | "insufficient_data";
  lynch_ratio: number | null;
  absolute_grade: LynchGrade;
  warnings: string[];
  calculable: boolean;
  unavailable_reason: string | null;
  audit_status: string;
  performance_forecast_blocked: boolean;
  major_risk_blocked: boolean;
  risk_reasons: string[];
  ranking_eligible: boolean;
  ranking_exclusion_reason: string | null;
  market_percentile: number | null;
  percentile_universe_size: number;
  core_data_status:
    | "complete"
    | "economic_not_applicable"
    | "source_missing";
};

type LoadState<T> =
  | { kind: "loading" }
  | { kind: "ready"; data: T }
  | { kind: "error"; message: string };

type SortField =
  | "code"
  | "floating_market_cap_percentile"
  | "adjusted_pe"
  | "lynch_growth_value_ratio"
  | "lynch_ratio"
  | "lynch_market_percentile"
  | "true_money_signal_score"
  | "heat_percentile"
  | "weighted_sentiment";

const emptyMonthlySeries: MonthlySeries = { code: "", records: [] };
const emptyDiscussionSeries: DiscussionSeries = { code: "", days: [] };
const boardSourceLabels: Record<string, string> = {
  eastmoney: "东方财富",
  sina: "新浪",
};

const lynchLabels: Record<MonthlySnapshot["lynch_growth_value_label"], string> = {
  poor: "较差",
  unclassified: "未分类",
  acceptable: "可以接受",
  good: "良好",
  not_applicable: "不适用",
};

const lynchGradeLabels: Record<LynchGrade, string> = {
  exceptional: "卓越",
  excellent: "优秀",
  reasonable: "合理",
  weak: "偏弱",
  earnings_contraction: "盈利收缩",
  insufficient_data: "数据不足",
};

const growthStatusLabels: Record<LynchItem["growth_status"], string> = {
  continuous_growth: "连续增长",
  non_continuous_growth: "增长不连续",
  earnings_contraction: "盈利收缩",
  loss_or_nonpositive: "存在亏损或非正盈利",
  insufficient_data: "数据不足",
};

const valuationStatusLabels: Record<LynchItem["valuation_status"], string> = {
  applicable: "适用",
  loss_making: "亏损，不适用",
  invalid_price: "价格无效",
  insufficient_data: "数据不足",
};

const coreDataStatusLabels: Record<LynchItem["core_data_status"], string> = {
  complete: "公共底座完整",
  economic_not_applicable: "数据完整，但经济上不适用",
  source_missing: "上游数据缺失",
};

const capitalActionLabels: Record<CapitalActionEventType, string> = {
  insider_buy: "内部人士主动买入",
  insider_sell: "内部人士主动卖出",
  cancelled_buyback: "已完成注销式回购",
  share_dilution: "外部融资导致股本稀释",
  buyback_announcement: "回购已完成但注销待确认",
};

function compactNumber(value: number | null, unit = ""): string {
  if (value === null) return "不适用";
  return `${new Intl.NumberFormat("zh-CN", {
    notation: "compact",
    maximumFractionDigits: 2,
  }).format(value)}${unit}`;
}

function decimal(value: number | null, digits = 2): string {
  if (value === null) return "不适用";
  return value.toFixed(digits);
}

function ratio(value: number | null): string {
  if (value === null) return "不适用";
  return `${(value * 100).toFixed(2)}%`;
}

function percentile(value: number | null): string {
  if (value === null) return "暂无数据";
  return `${value.toFixed(2)}%`;
}

function beijingDateTime(value: string | null): string {
  if (!value) return "暂无数据";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}

function inventoryLabel(snapshot: MonthlySnapshot): string {
  if (snapshot.inventory_status === "not_applicable") return "不适用";
  if (snapshot.inventory_status === "insufficient_data") return "数据不足";
  return "可用";
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="fundamental-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function PillarHeader({
  number,
  title,
  description,
}: {
  number: string;
  title: string;
  description: string;
}) {
  return (
    <header className="fundamental-pillar__header">
      <span>{number}</span>
      <div>
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
    </header>
  );
}

function CapitalActionLedger({
  state,
}: {
  state: LoadState<CapitalActionEvidence>;
}) {
  if (state.kind === "loading") {
    return <p className="fundamental-inline-empty">正在读取资本行为证据链…</p>;
  }
  if (state.kind === "error") {
    return <p className="fundamental-inline-error">{state.message}</p>;
  }
  if (state.data.events.length === 0) {
    return (
      <p className="fundamental-inline-empty">
        该完整批次已覆盖此股票，但最近十二个月没有符合规则的资本行为事件。
      </p>
    );
  }
  return (
    <div className="capital-action-ledger">
      <header>
        <span>证据批次 {state.data.as_of_date}</span>
        <span>发布于 {beijingDateTime(state.data.published_at)}</span>
      </header>
      {state.data.events.map((event) => (
        <article key={event.event_key}>
          <div>
            <strong>{capitalActionLabels[event.event_type]}</strong>
            <span>{event.effective_date}</span>
          </div>
          <p>
            {event.amount_cny === null
              ? event.shares === null
                ? "公告未提供可核验金额或股数"
                : `${compactNumber(event.shares, "股")}`
              : compactNumber(event.amount_cny, "元")}
            {event.reason ? ` · ${event.reason}` : ""}
          </p>
          <footer>
            <span className={event.confirmed_for_score ? "is-confirmed" : "is-pending"}>
              {event.confirmed_for_score
                ? "已纳入机械评分"
                : event.exclusion_reason ?? "暂不纳入机械评分"}
            </span>
            <a href={event.source_url} target="_blank" rel="noreferrer">
              查看{event.source_name}证据
            </a>
          </footer>
        </article>
      ))}
    </div>
  );
}

function FundamentalDetail({
  item,
  monthlyState,
  discussionState,
  capitalActionState,
}: {
  item: OverviewItem;
  monthlyState: LoadState<MonthlySeries>;
  discussionState: LoadState<DiscussionSeries>;
  capitalActionState: LoadState<CapitalActionEvidence>;
}) {
  const latest = item.monthly?.snapshot ?? null;
  const lynch = item.lynch;
  const discussion = item.discussion;
  const monthlyHistory =
    monthlyState.kind === "ready" ? monthlyState.data.records : [];

  return (
    <section className="fundamental-detail" aria-labelledby="fundamental-detail-title">
      <header className="fundamental-detail__title">
        <div>
          <p className="section-kicker">林奇核心底座 · 机械结果</p>
          <h2 id="fundamental-detail-title">
            {item.name} <span>{item.code}</span>
          </h2>
          <p>
            只展示已落库快照，不生成基本面总分、目标价或投资建议。
          </p>
        </div>
        <div className="fundamental-detail__dates">
          <span>三年林奇行情</span>
          <strong>{lynch?.target_date ?? "暂无数据"}</strong>
          <span>月度快照</span>
          <strong>{latest?.as_of_date ?? "暂无数据"}</strong>
          <span>舆情日期</span>
          <strong>{discussion?.actual_date ?? "暂无数据"}</strong>
        </div>
      </header>

      <section className="fundamental-pillar">
        <PillarHeader
          number="I"
          title="林奇核心底座"
          description="只保留盈利增长、估值、财务安全三个独立维度；林奇比是派生结果。"
        />
        {lynch ? (
          <>
            <div className="fundamental-metric-grid">
              <Metric
                label="盈利增长 · 三年扣非复合增长"
                value={ratio(lynch.three_year_cagr)}
                detail={growthStatusLabels[lynch.growth_status]}
              />
              <Metric
                label="估值 · 扣非市盈率"
                value={decimal(lynch.adjusted_pe)}
                detail={valuationStatusLabels[lynch.valuation_status]}
              />
              <Metric
                label="财务安全 · 有息净负债率"
                value={ratio(lynch.net_debt_to_equity)}
                detail={
                  lynch.financial_safety_status === "available"
                    ? "负值代表净现金"
                    : lynch.financial_safety_status === "invalid_equity"
                      ? "归母净资产非正，不适用"
                      : "资产负债表数据不足"
                }
              />
              <Metric
                label="派生结果 · 三年林奇比"
                value={decimal(lynch.lynch_ratio)}
                detail={lynchGradeLabels[lynch.absolute_grade]}
              />
              <Metric
                label="林奇比正式市场百分位"
                value={percentile(lynch.market_percentile)}
                detail={`有效排名样本 ${lynch.percentile_universe_size} 只`}
              />
              <Metric label="林奇比输入 · 股息率" value={ratio(lynch.dividend_yield)} />
              <Metric
                label="公共底座状态"
                value={coreDataStatusLabels[lynch.core_data_status]}
              />
            </div>
            <p className="fundamental-note">
              年度扣非每股收益：
              {lynch.annual_adjusted_eps.length > 0
                ? lynch.annual_adjusted_eps
                    .map((entry) => `${entry.year} 年 ${decimal(entry.value)}`)
                    .join(" · ")
                : "数据不足"}
              。财务基准日 {lynch.financial_as_of ?? "暂无"}。
              {lynch.warnings.length > 0
                ? ` 风险警告 ${lynch.warnings.length} 项。`
                : " 暂无机械风险警告。"}
            </p>
          </>
        ) : (
          <p className="fundamental-inline-empty">
            当前完整发布批次中没有该股票的三年林奇结果。
          </p>
        )}
      </section>

      {!latest ? (
        <section className="fundamental-detail-empty">
          <h3>尚无个股月度基本面快照</h3>
          <p>
            该股票已有全量林奇记录，但真实月度财务采集和计算可能尚未完成。空值不会被当作 0。
          </p>
        </section>
      ) : (
        <details className="fundamental-enhancement">
          <summary>
            查看非核心增强信息
            <span>月度历史、主营业务、资本行为、市值与讨论情绪</span>
          </summary>
          <div className="fundamental-enhancement__content">
          <section className="fundamental-pillar">
            <PillarHeader
              number="I·扩展"
              title="月度基本面历史字段"
              description="五年历史契约、现金负债、股息、现金流与资本行为分项。"
            />
            <div className="fundamental-metric-grid">
              <Metric label="普通滚动市盈率" value={decimal(latest.ordinary_pe)} />
              <Metric label="调整后滚动市盈率" value={decimal(latest.adjusted_pe)} />
              <Metric
                label="公司历史市盈率百分位"
                value={percentile(latest.adjusted_pe_historical_percentile)}
              />
              <Metric
                label="同行市盈率百分位"
                value={percentile(latest.adjusted_pe_peer_percentile)}
              />
              <Metric
                label="五年调整后每股收益增长"
                value={ratio(latest.five_year_adjusted_eps_cagr)}
                detail={`正增长年度 ${latest.positive_growth_years} 个`}
              />
              <Metric
                label="旧五年机械比（历史契约）"
                value={decimal(latest.lynch_growth_value_ratio)}
                detail={lynchLabels[latest.lynch_growth_value_label]}
              />
              <Metric label="每股净现金" value={decimal(latest.net_cash_per_share)} />
              <Metric label="现金调整股价" value={decimal(latest.cash_adjusted_price)} />
              <Metric label="现金调整市盈率" value={decimal(latest.cash_adjusted_pe)} />
              <Metric label="负债权益比" value={ratio(latest.debt_to_equity)} />
              <Metric label="短期债务占比" value={ratio(latest.short_term_debt_share)} />
              <Metric label="股息率" value={ratio(latest.dividend_yield)} />
              <Metric
                label="股利支付率"
                value={ratio(latest.dividend_payout_ratio)}
                detail={`连续分红 ${latest.consecutive_dividend_years} 年`}
              />
              <Metric
                label="每股自由现金流代理值"
                value={decimal(latest.free_cash_flow_per_share)}
              />
              <Metric
                label="股价自由现金流比"
                value={decimal(latest.price_to_free_cash_flow)}
              />
              <Metric label="税前利润率" value={ratio(latest.pretax_margin)} />
              <Metric
                label="税前利润率历史百分位"
                value={percentile(latest.pretax_margin_historical_percentile)}
              />
              <Metric
                label="税前利润率同行百分位"
                value={percentile(latest.pretax_margin_peer_percentile)}
              />
              <Metric
                label="真金白银信号分"
                value={decimal(latest.true_money_signal_score, 1)}
                detail="唯一允许展示的机械评分，不是基本面总分"
              />
            </div>

            <div className="fundamental-evidence-grid">
              <article>
                <h4>存货与收入</h4>
                <dl>
                  <div><dt>数据状态</dt><dd>{inventoryLabel(latest)}</dd></div>
                  <div><dt>存货增长</dt><dd>{ratio(latest.inventory_growth)}</dd></div>
                  <div><dt>收入增长</dt><dd>{ratio(latest.revenue_growth)}</dd></div>
                  <div><dt>增长差</dt><dd>{ratio(latest.inventory_growth_minus_revenue_growth)}</dd></div>
                </dl>
              </article>
              <article>
                <h4>资本行为原始证据</h4>
                {latest.capital_action_signal ? (
                  <dl>
                    <div><dt>内部人士数据</dt><dd>{latest.capital_action_signal.insider_window_complete ? "窗口完整" : "无法确定"}</dd></div>
                    <div><dt>内部人士净买入</dt><dd>{latest.capital_action_signal.insider_window_complete ? compactNumber(latest.capital_action_signal.insider_net_purchase_amount, "元") : "无法确定"}</dd></div>
                    <div><dt>内部人士调整</dt><dd>{latest.capital_action_signal.insider_window_complete ? decimal(latest.capital_action_signal.insider_adjustment, 1) : "无法确定"}</dd></div>
                    <div><dt>注销式回购</dt><dd>{compactNumber(latest.capital_action_signal.cancelled_buyback_amount, "元")}</dd></div>
                    <div><dt>回购奖励</dt><dd>{decimal(latest.capital_action_signal.buyback_bonus, 1)}</dd></div>
                    <div><dt>股本稀释比例</dt><dd>{ratio(latest.capital_action_signal.dilution_ratio)}</dd></div>
                    <div><dt>稀释扣分</dt><dd>{decimal(latest.capital_action_signal.dilution_penalty, 1)}</dd></div>
                  </dl>
                ) : (
                  <p className="fundamental-inline-empty">
                    尚无经过验证的内部人、注销回购与稀释数据。
                  </p>
                )}
              </article>
            </div>
            <CapitalActionLedger state={capitalActionState} />
          </section>

          <section className="fundamental-pillar">
            <PillarHeader
              number="II"
              title="主营业务"
              description="优先采用全部业务均披露的分部利润，否则仅在全部未披露时使用毛利润。"
            />
            <div className="fundamental-primary-business">
              <div>
                <span>主营业务判断</span>
                <strong>{latest.main_business_name ?? "无法确定"}</strong>
              </div>
              <div>
                <span>利润贡献比例</span>
                <strong>{ratio(latest.main_business_profit_share)}</strong>
              </div>
              <p>
                当前机械快照只持久化主营业务名称与利润贡献比例；利润贡献金额和判断依据字段尚未进入输出契约，因此页面不会推算或补造。
              </p>
            </div>
          </section>

          <section className="fundamental-pillar">
            <PillarHeader
              number="III"
              title="流通市值"
              description="用于观察股票能否形成较充分的多空博弈，不代表公司质量。"
            />
            <div className="fundamental-market-cap">
              <Metric
                label="流通市值"
                value={compactNumber(latest.floating_market_cap, "元")}
              />
              <Metric
                label="正常 A 股横截面百分位"
                value={percentile(latest.floating_market_cap_percentile)}
              />
              <Metric
                label="市场横截面日期"
                value={latest.as_of_date}
                detail="当前输出契约未单独保存横截面日期"
              />
              <Metric
                label="有效样本数量"
                value="暂未保存"
                detail="不使用猜测值"
              />
            </div>
          </section>

          <section className="fundamental-pillar">
            <PillarHeader
              number="IV"
              title="舆论监测"
              description="舆论已拆为独立模块：东方财富负责热榜发现，新浪股吧负责低频采集主题、回复和点赞。"
            />
            <p className="fundamental-note">
              旧的跨平台综合值已经移除。新模块按平台展示采集完整性、有效样本和方向，不回写基本面排序。
            </p>
            <a className="fundamental-source-link" href="/public-opinion">
              打开舆论监测
            </a>
          </section>

          <section className="fundamental-history">
            <header>
              <div>
                <p className="section-kicker">历史与证据</p>
                <h3>最近 12 个月度快照</h3>
              </div>
              <span>{latest.rules_version}</span>
            </header>
            {monthlyState.kind === "loading" ? (
              <p className="fundamental-inline-empty">正在恢复月度变化快照…</p>
            ) : monthlyState.kind === "error" ? (
              <p className="fundamental-inline-error">{monthlyState.message}</p>
            ) : monthlyHistory.length === 0 ? (
              <p className="fundamental-inline-empty">尚无月度历史。</p>
            ) : (
              <div className="fundamental-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>日期</th>
                      <th>调整后市盈率</th>
                      <th>林奇比</th>
                      <th>流通市值分位</th>
                      <th>真金白银信号分</th>
                      <th>证据</th>
                    </tr>
                  </thead>
                  <tbody>
                    {monthlyHistory.map((record) => (
                      <tr key={`${record.snapshot.rules_version}-${record.snapshot.as_of_date}`}>
                        <td>{record.snapshot.as_of_date}</td>
                        <td>{decimal(record.snapshot.adjusted_pe)}</td>
                        <td>{decimal(record.snapshot.lynch_growth_value_ratio)}</td>
                        <td>{percentile(record.snapshot.floating_market_cap_percentile)}</td>
                        <td>{decimal(record.snapshot.true_money_signal_score, 1)}</td>
                        <td>
                          {record.source_urls.length === 0 ? (
                            "暂无来源网址"
                          ) : (
                            record.source_urls.map((url, index) => (
                              <a key={url} href={url} target="_blank" rel="noreferrer">
                                来源 {index + 1}
                              </a>
                            ))
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <p className="fundamental-note">
              当前机械输出保留五年复合增长率和正增长年度数，但未持久化五个年度的逐年每股收益序列；页面明确标记此契约缺口，不从摘要反推明细。
            </p>
          </section>
          </div>
        </details>
      )}
    </section>
  );
}

export function FundamentalsPage({ targetDate }: { targetDate: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [overviewState, setOverviewState] = useState<LoadState<FundamentalOverview>>({
    kind: "loading",
  });
  const [monthlyState, setMonthlyState] = useState<LoadState<MonthlySeries>>({
    kind: "ready",
    data: emptyMonthlySeries,
  });
  const [discussionState, setDiscussionState] = useState<LoadState<DiscussionSeries>>({
    kind: "ready",
    data: emptyDiscussionSeries,
  });
  const [capitalActionState, setCapitalActionState] = useState<
    LoadState<CapitalActionEvidence>
  >({ kind: "loading" });
  const [capitalStatusState, setCapitalStatusState] = useState<
    LoadState<CapitalActionStatus>
  >({ kind: "loading" });
  const [capitalRetrying, setCapitalRetrying] = useState(false);
  const [capitalRefreshRevision, setCapitalRefreshRevision] = useState(0);
  const [selectedBoard, setSelectedBoard] = useState(
    () => searchParams.get("fundamental_board") ?? "",
  );
  const [selectedCode, setSelectedCode] = useState(
    () => searchParams.get("fundamental_symbol") ?? "",
  );
  const [searchDraft, setSearchDraft] = useState(
    () => searchParams.get("fundamental_search") ?? "",
  );
  const [search, setSearch] = useState(searchDraft);
  const [sortField, setSortField] = useState<SortField>("code");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");

  useEffect(() => {
    const controller = new AbortController();
    async function loadOverview() {
      setOverviewState({ kind: "loading" });
      const params = new URLSearchParams({
        target_date: targetDate,
        search,
        sort_by: sortField,
        sort_order: sortOrder,
        limit: "100",
        offset: "0",
      });
      if (selectedBoard) params.set("board_code", selectedBoard);
      try {
        const response = await fetch(`/api/fundamentals/overview?${params}`, {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`基本面总览接口返回 ${response.status}`);
        const data = (await response.json()) as FundamentalOverview;
        setOverviewState({ kind: "ready", data });
        setSelectedCode((current) => {
          if (current && data.items.some((item) => item.code === current)) return current;
          return data.items[0]?.code ?? "";
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setOverviewState({
          kind: "error",
          message: "基本面数据读取失败，请检查本机服务后重试。",
        });
      }
    }
    void loadOverview();
    return () => controller.abort();
  }, [search, selectedBoard, sortField, sortOrder, targetDate]);

  useEffect(() => {
    const controller = new AbortController();
    async function loadCapitalStatus() {
      setCapitalStatusState({ kind: "loading" });
      try {
        const params = new URLSearchParams({ target_date: targetDate });
        const response = await fetch(
          `/api/fundamentals/capital-actions/status?${params}`,
          { signal: controller.signal },
        );
        if (!response.ok) {
          throw new Error(`资本行为状态接口返回 ${response.status}`);
        }
        setCapitalStatusState({
          kind: "ready",
          data: (await response.json()) as CapitalActionStatus,
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setCapitalStatusState({
          kind: "error",
          message: "资本行为批次状态读取失败。",
        });
      }
    }
    void loadCapitalStatus();
    return () => controller.abort();
  }, [capitalRefreshRevision, targetDate]);

  useEffect(() => {
    if (!selectedCode) {
      setMonthlyState({ kind: "ready", data: emptyMonthlySeries });
      setDiscussionState({ kind: "ready", data: emptyDiscussionSeries });
      setCapitalActionState({
        kind: "error",
        message: "请选择股票后查看资本行为证据。",
      });
      return;
    }
    const controller = new AbortController();
    async function loadDetail() {
      setMonthlyState({ kind: "loading" });
      setDiscussionState({ kind: "loading" });
      setCapitalActionState({ kind: "loading" });
      const monthlyParams = new URLSearchParams({
        target_date: targetDate,
        limit: "12",
      });
      const discussionParams = new URLSearchParams({
        end_date: targetDate,
        limit: "30",
      });
      const capitalParams = new URLSearchParams({
        target_date: targetDate,
      });
      const [monthlyResult, discussionResult, capitalActionResult] =
        await Promise.allSettled([
        fetch(
          `/api/fundamentals/securities/${selectedCode}/monthly?${monthlyParams}`,
          { signal: controller.signal },
        ).then(async (response) => {
          if (!response.ok) throw new Error(`月度接口返回 ${response.status}`);
          return (await response.json()) as MonthlySeries;
        }),
        fetch(
          `/api/fundamentals/securities/${selectedCode}/discussion?${discussionParams}`,
          { signal: controller.signal },
        ).then(async (response) => {
          if (!response.ok) throw new Error(`舆情接口返回 ${response.status}`);
          return (await response.json()) as DiscussionSeries;
        }),
        fetch(
          `/api/fundamentals/securities/${selectedCode}/capital-actions?${capitalParams}`,
          { signal: controller.signal },
        ).then(async (response) => {
          if (!response.ok) {
            throw new Error(`资本行为接口返回 ${response.status}`);
          }
          return (await response.json()) as CapitalActionEvidence;
        }),
      ]);
      if (controller.signal.aborted) return;
      setMonthlyState(
        monthlyResult.status === "fulfilled"
          ? { kind: "ready", data: monthlyResult.value }
          : { kind: "error", message: "月度历史读取失败。" },
      );
      setDiscussionState(
        discussionResult.status === "fulfilled"
          ? { kind: "ready", data: discussionResult.value }
          : { kind: "error", message: "舆情历史读取失败。" },
      );
      setCapitalActionState(
        capitalActionResult.status === "fulfilled"
          ? { kind: "ready", data: capitalActionResult.value }
          : {
              kind: "error",
              message: "该股票尚无已发布的完整资本行为证据。",
            },
      );
    }
    void loadDetail();
    return () => controller.abort();
  }, [capitalRefreshRevision, selectedCode, targetDate]);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    if (selectedBoard) next.set("fundamental_board", selectedBoard);
    else next.delete("fundamental_board");
    if (selectedCode) next.set("fundamental_symbol", selectedCode);
    else next.delete("fundamental_symbol");
    if (search) next.set("fundamental_search", search);
    else next.delete("fundamental_search");
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  }, [search, searchParams, selectedBoard, selectedCode, setSearchParams]);

  const overview = overviewState.kind === "ready" ? overviewState.data : null;
  const selectedItem = useMemo(
    () => overview?.items.find((item) => item.code === selectedCode) ?? null,
    [overview, selectedCode],
  );

  function submitSearch(event: FormEvent) {
    event.preventDefault();
    setSearch(searchDraft.trim());
  }

  async function retryCapitalActions() {
    setCapitalRetrying(true);
    try {
      const response = await fetch(
        "/api/fundamentals/capital-actions/retry",
        { method: "POST" },
      );
      if (!response.ok) {
        throw new Error(`资本行为重试接口返回 ${response.status}`);
      }
      setCapitalRefreshRevision((current) => current + 1);
    } catch {
      setCapitalStatusState({
        kind: "error",
        message: "资本行为重新运行失败，请查看本机完整日志。",
      });
    } finally {
      setCapitalRetrying(false);
    }
  }

  return (
    <div className="fundamentals-page">
      <section className="fundamentals-intro">
        <div>
          <p className="section-kicker">独立证据体系 · 纯机械化</p>
          <h2>个人基本面分析</h2>
          <p>
            全量股票共用一套基本面记录；三年林奇比是个股字段，板块与概念仅用于筛选。
            月度财务、流通市值与每日讨论仍独立展示，不合成基本面总分。
          </p>
        </div>
        <dl>
          <div>
            <dt>林奇行情日期</dt>
            <dd>{overview?.lynch_actual_data_date ?? "尚未初始化"}</dd>
          </div>
          <div>
            <dt>全量股票</dt>
            <dd>{overview?.lynch_total_count ?? 0} 只</dd>
          </div>
          <div>
            <dt>三年林奇可计算</dt>
            <dd>{overview?.lynch_calculable_count ?? 0} 只</dd>
          </div>
          <div>
            <dt>板块与概念筛选</dt>
            <dd>{overview?.boards.length ?? 0} 个</dd>
          </div>
        </dl>
      </section>

      {capitalStatusState.kind === "ready" &&
        capitalStatusState.data.status === "failed" && (
          <section className="capital-action-status capital-action-status--failed" role="alert">
            <div>
              <strong>今日基本面更新失败</strong>
              <p>
                继续展示 {capitalStatusState.data.publication_date ?? "上一交易日"} 的完整结果，
                绝不使用本次残缺数据覆盖。
              </p>
            </div>
            <dl>
              <div>
                <dt>失败阶段</dt>
                <dd>{capitalStatusState.data.failure_stage ?? "资本行为采集"}</dd>
              </div>
              <div>
                <dt>失败时间</dt>
                <dd>{beijingDateTime(capitalStatusState.data.latest_attempt_at)}</dd>
              </div>
              <div>
                <dt>失败股票</dt>
                <dd>{Object.keys(capitalStatusState.data.errors).length} 只</dd>
              </div>
            </dl>
            <button
              type="button"
              disabled={capitalRetrying}
              onClick={() => void retryCapitalActions()}
            >
              {capitalRetrying ? "正在重新运行…" : "重新运行今日基本面任务"}
            </button>
          </section>
        )}
      {capitalStatusState.kind === "ready" &&
        capitalStatusState.data.status === "stale" && (
          <section className="capital-action-status capital-action-status--stale">
            当前日期尚无资本行为完整批次，正在展示{" "}
            {capitalStatusState.data.publication_date ?? "最近一次"} 成功结果。
          </section>
        )}

      <section className="fundamental-controls" aria-label="基本面筛选">
        <label>
          <span>板块与概念筛选</span>
          <select
            value={selectedBoard}
            onChange={(event) => setSelectedBoard(event.target.value)}
          >
            <option value="">全部股票</option>
            {overview?.boards.map((board) => (
              <option key={board.board_id} value={board.board_id}>
                {board.kind === "industry" ? "行业" : "概念"} · {board.name} · {board.members.length} 只
              </option>
            ))}
          </select>
        </label>
        <form onSubmit={submitSearch}>
          <label htmlFor="fundamental-search">股票搜索</label>
          <div>
            <input
              id="fundamental-search"
              value={searchDraft}
              onChange={(event) => setSearchDraft(event.target.value)}
              placeholder="输入六位代码或中文名称"
            />
            <button type="submit">搜索</button>
          </div>
        </form>
        <label>
          <span>排序指标</span>
          <select
            value={sortField}
            onChange={(event) => setSortField(event.target.value as SortField)}
          >
            <option value="code">股票代码</option>
            <option value="floating_market_cap_percentile">流通市值百分位</option>
            <option value="adjusted_pe">调整后市盈率</option>
            <option value="lynch_ratio">三年林奇比</option>
            <option value="lynch_market_percentile">三年林奇正式百分位</option>
            <option value="lynch_growth_value_ratio">旧五年机械比</option>
            <option value="true_money_signal_score">真金白银信号分</option>
            <option value="heat_percentile">综合热度百分位</option>
            <option value="weighted_sentiment">综合情绪</option>
          </select>
        </label>
        <button
          className="fundamental-sort-order"
          type="button"
          aria-label={sortOrder === "asc" ? "当前升序，切换为降序" : "当前降序，切换为升序"}
          onClick={() => setSortOrder((current) => (current === "asc" ? "desc" : "asc"))}
        >
          {sortOrder === "asc" ? "升序" : "降序"}
        </button>
      </section>

      {overviewState.kind === "loading" ? (
        <section className="fundamental-page-state">正在读取基本面已发布快照…</section>
      ) : overviewState.kind === "error" ? (
        <section className="fundamental-page-state fundamental-page-state--error" role="alert">
          {overviewState.message}
        </section>
      ) : overviewState.data.items.length === 0 ? (
        <section className="fundamental-page-state">
          <h2>尚无可展示的基本面结果</h2>
          <p>
            请先运行全量三年林奇任务或其他真实基本面任务。页面不会使用模拟数据填充正式结论。
          </p>
        </section>
      ) : (
        <div className="fundamental-workspace">
          <aside className="fundamental-universe" aria-labelledby="fundamental-universe-title">
            <header>
              <div>
                <p className="section-kicker">全量基本面股票库 · 板块筛选</p>
                <h2 id="fundamental-universe-title">
                  {overviewState.data.selected_board_id
                    ? overviewState.data.boards.find(
                        (board) => board.board_id === overviewState.data.selected_board_id,
                      )?.name ?? "筛选结果"
                    : "全部股票"}
                </h2>
              </div>
              <span>{overviewState.data.total} 只</span>
            </header>
            <div className="fundamental-universe__table">
              <table>
                <thead>
                  <tr>
                    <th>股票</th>
                    <th>三年增长</th>
                    <th>扣非市盈率</th>
                    <th>有息净负债率</th>
                    <th>林奇比 / 百分位</th>
                    <th>公共底座</th>
                  </tr>
                </thead>
                <tbody>
                  {overviewState.data.items.map((item) => (
                    <tr
                      key={item.code}
                      className={item.code === selectedCode ? "is-selected" : undefined}
                    >
                      <td>
                        <button type="button" onClick={() => setSelectedCode(item.code)}>
                          <strong>{item.name}</strong>
                          <span>{item.code}</span>
                        </button>
                      </td>
                      <td>{ratio(item.lynch?.three_year_cagr ?? null)}</td>
                      <td>{decimal(item.lynch?.adjusted_pe ?? null)}</td>
                      <td>{ratio(item.lynch?.net_debt_to_equity ?? null)}</td>
                      <td>
                        {decimal(item.lynch?.lynch_ratio ?? null)} ·{" "}
                        {percentile(item.lynch?.market_percentile ?? null)}
                      </td>
                      <td>
                        {item.lynch
                          ? coreDataStatusLabels[item.lynch.core_data_status]
                          : "当前批次无记录"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <footer>
              <span>
                板块来源 ·{" "}
                {overviewState.data.board_source
                  ? (boardSourceLabels[overviewState.data.board_source] ??
                    overviewState.data.board_source)
                  : "暂无分类快照"}
              </span>
              <span>
                林奇发布 · {beijingDateTime(overviewState.data.lynch_published_at)}
              </span>
            </footer>
          </aside>

          {selectedItem ? (
            <FundamentalDetail
              item={selectedItem}
              monthlyState={monthlyState}
              discussionState={discussionState}
              capitalActionState={capitalActionState}
            />
          ) : (
            <section className="fundamental-page-state">请选择一只股票查看林奇核心底座。</section>
          )}
        </div>
      )}
    </div>
  );
}
