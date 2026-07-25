import { expect, test } from "@playwright/test";

const capitalAction = {
  insider_window_complete: false,
  insider_net_purchase_amount: null,
  insider_net_purchase_ratio: null,
  insider_adjustment: null,
  cancelled_buyback_amount: 10_000_000,
  cancelled_buyback_ratio: 0.01,
  buyback_bonus: 20,
  dilution_ratio: 0.1,
  dilution_penalty: 20,
  newly_issued_shares: 10_000_000,
  shares_before_issuance: 100_000_000,
};

const completeSnapshot = {
  rules_version: "personal-fundamental-v1",
  code: "600000",
  as_of_date: "2026-06-30",
  ordinary_pe: 8,
  adjusted_pe: 10,
  adjusted_pe_historical_percentile: 50,
  adjusted_pe_peer_percentile: 60,
  five_year_adjusted_eps_cagr: 0.2,
  positive_growth_years: 4,
  dividend_yield: 0.02,
  lynch_growth_value_ratio: 2.2,
  lynch_growth_value_label: "good",
  net_cash_per_share: 3,
  cash_adjusted_price: 17,
  cash_adjusted_pe: 8.5,
  short_term_interest_bearing_debt: 100_000_000,
  long_term_interest_bearing_debt: 100_000_000,
  debt_to_equity: 0.2,
  short_term_debt_share: 0.5,
  dividend_payout_ratio: 0.16,
  consecutive_dividend_years: 5,
  dividend_continuously_increased: true,
  free_cash_flow_per_share: 2,
  price_to_free_cash_flow: 10,
  inventory_status: "available",
  inventory_growth: 0.2,
  revenue_growth: 0.3,
  inventory_growth_minus_revenue_growth: -0.1,
  pretax_margin: 0.12,
  pretax_margin_historical_percentile: 60,
  pretax_margin_peer_percentile: 50,
  main_business_name: "零售银行",
  main_business_profit_share: 0.6,
  institution_holding_ratio: 0.12,
  institution_holding_change: 0.02,
  capital_action_signal: capitalAction,
  true_money_signal_score: 80,
  floating_market_cap: 100_000_000_000,
  floating_market_cap_percentile: 66.67,
};

const partialSnapshot = {
  ...completeSnapshot,
  code: "000001",
  adjusted_pe: null,
  lynch_growth_value_ratio: null,
  lynch_growth_value_label: "not_applicable",
  inventory_status: "insufficient_data",
  inventory_growth: null,
  revenue_growth: null,
  inventory_growth_minus_revenue_growth: null,
  main_business_name: null,
  main_business_profit_share: null,
};

const eastmoneyDiscussion = {
  platform: "eastmoney_guba",
  actual_date: "2026-07-24",
  code: "600000",
  post_count: 12,
  positive_count: 7,
  neutral_count: 3,
  negative_count: 2,
  raw_heat: 19.4,
  weighted_sentiment: 0.42,
  heat_percentile: 82.5,
  likes_missing: false,
};

const xueqiuDiscussion = {
  ...eastmoneyDiscussion,
  platform: "xueqiu",
  post_count: 8,
  positive_count: 4,
  neutral_count: 3,
  negative_count: 1,
  raw_heat: 12.2,
  weighted_sentiment: 0.31,
  heat_percentile: 74,
};

const combinedDiscussion = {
  actual_date: "2026-07-24",
  code: "600000",
  heat_percentile: 78.25,
  weighted_sentiment: 0.365,
};

const completeDay = {
  actual_date: "2026-07-24",
  code: "600000",
  eastmoney_guba: eastmoneyDiscussion,
  xueqiu: xueqiuDiscussion,
  combined: combinedDiscussion,
  eastmoney_reference_count: 12,
  xueqiu_reference_count: 8,
};

const partialDay = {
  actual_date: "2026-07-24",
  code: "000001",
  eastmoney_guba: { ...eastmoneyDiscussion, code: "000001" },
  xueqiu: null,
  combined: null,
  eastmoney_reference_count: 12,
  xueqiu_reference_count: 0,
};

const boards = [
  {
    board_id: "em:industry:BK001",
    source_board_code: "BK001",
    name: "银行",
    kind: "industry",
    members: [
      { code: "600000", name: "浦发银行" },
      { code: "000001", name: "平安银行" },
    ],
  },
];

const items = [
  {
    code: "600000",
    name: "浦发银行",
    board_ids: ["em:industry:BK001"],
    monthly: {
      snapshot: completeSnapshot,
      source_urls: ["https://www.cninfo.com.cn/report-1"],
      created_at: "2026-06-30T16:30:00+08:00",
    },
    discussion: completeDay,
    data_status: "complete",
  },
  {
    code: "000001",
    name: "平安银行",
    board_ids: ["em:industry:BK001"],
    monthly: {
      snapshot: partialSnapshot,
      source_urls: [],
      created_at: "2026-06-30T16:30:00+08:00",
    },
    discussion: partialDay,
    data_status: "partial",
  },
];

test("用户可以筛选候选板块并查看四支柱基本面详情", async ({ page }) => {
  let capitalRetryRequested = false;
  await page.route("**/api/fundamentals/overview?*", async (route) => {
    const url = new URL(route.request().url());
    const search = url.searchParams.get("search") ?? "";
    const filtered = search
      ? items.filter((item) => item.code.includes(search) || item.name.includes(search))
      : items;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        board_source: "eastmoney",
        board_effective_date: "2026-07-24",
        board_collected_at: "2026-07-24T16:30:00+08:00",
        board_complete: true,
        boards,
        selected_board_id: url.searchParams.get("board_code"),
        total: filtered.length,
        limit: 100,
        offset: 0,
        items: filtered,
      }),
    });
  });

  await page.route("**/api/fundamentals/securities/*/monthly?*", async (route) => {
    const code = route.request().url().includes("/000001/") ? "000001" : "600000";
    const snapshot = code === "000001" ? partialSnapshot : completeSnapshot;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        code,
        records: [
          {
            snapshot,
            source_urls: code === "600000" ? ["https://www.cninfo.com.cn/report-1"] : [],
            created_at: "2026-06-30T16:30:00+08:00",
          },
        ],
      }),
    });
  });

  await page.route("**/api/fundamentals/securities/*/discussion?*", async (route) => {
    const code = route.request().url().includes("/000001/") ? "000001" : "600000";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        code,
        days: [code === "000001" ? partialDay : completeDay],
      }),
    });
  });

  await page.route("**/api/fundamentals/capital-actions/status?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_date: "2026-07-24",
        publication_date: "2026-07-23",
        published_at: "2026-07-23T16:30:00+08:00",
        status: "failed",
        expected_count: 2,
        completed_count: 1,
        event_count: 1,
        confirmed_event_count: 1,
        latest_attempt_date: "2026-07-24",
        latest_attempt_at: "2026-07-24T16:31:00+08:00",
        latest_attempt_status: "failed",
        failure_stage: "资本行为采集",
        errors: { "000001": "TimeoutError: 巨潮请求超时" },
      }),
    });
  });

  await page.route("**/api/fundamentals/securities/*/capital-actions?*", async (route) => {
    const code = route.request().url().includes("/000001/") ? "000001" : "600000";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        code,
        as_of_date: "2026-07-23",
        published_at: "2026-07-23T16:30:00+08:00",
        events:
          code === "600000"
            ? [
                {
                  event_key: "a".repeat(64),
                  code,
                  name: "浦发银行",
                  event_type: "insider_buy",
                  announcement_at: "2026-07-21T00:00:00+08:00",
                  effective_date: "2026-07-20",
                  shares: 10000,
                  amount_cny: 125000,
                  price_cny: 12.5,
                  shares_before: null,
                  reason: "竞价交易",
                  confirmed_for_score: true,
                  exclusion_reason: null,
                  source_name: "巨潮资讯",
                  source_url: "https://www.cninfo.com.cn/capital-1",
                  source_record_id: "capital-1",
                  content_sha256: "b".repeat(64),
                  raw_payload: {},
                  collected_at: "2026-07-23T16:30:00+08:00",
                },
              ]
            : [],
      }),
    });
  });
  await page.route("**/api/fundamentals/capital-actions/retry", async (route) => {
    capitalRetryRequested = true;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        status: "succeeded",
        target_date: "2026-07-24",
        stage: "complete",
        reason: "资本行为已发布",
      }),
    });
  });

  await page.goto("/fundamentals?target_date=2026-07-24");

  await expect(page.getByRole("heading", { name: "个人基本面分析" }).first()).toBeVisible();
  await expect(page.getByText("今日基本面更新失败", { exact: true })).toBeVisible();
  await expect(page.getByText("资本行为采集", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新运行今日基本面任务" }).click();
  await expect.poll(() => capitalRetryRequested).toBe(true);
  await expect(page.getByRole("heading", { name: "彼得·林奇机械数据" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "主营业务" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "流通市值" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "讨论热度与情绪" })).toBeVisible();
  await expect(page.getByText("良好", { exact: true })).toBeVisible();
  await expect(page.getByText("双平台综合情绪 · 最近 1 日")).toBeVisible();
  await expect(page.getByText("基本面总分", { exact: true })).toHaveCount(0);
  await expect(page.getByText("机构持股比例", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "查看巨潮资讯证据" })).toHaveAttribute(
    "href",
    "https://www.cninfo.com.cn/capital-1",
  );
  await expect(page.getByText("12.5万元")).toBeVisible();

  await page.getByLabel("候选板块").selectOption("em:industry:BK001");
  await expect(page.getByRole("heading", { name: "银行", exact: true })).toBeVisible();

  await page.getByLabel("股票搜索").fill("平安");
  await page.getByRole("button", { name: "搜索" }).click();
  await expect(page.getByRole("button", { name: /平安银行/ })).toBeVisible();
  await page.getByRole("button", { name: /平安银行/ }).click();

  await expect(page.getByRole("heading", { name: /平安银行/ })).toBeVisible();
  await expect(
    page.locator(".fundamental-metric").filter({ hasText: "调整后滚动市盈率" }),
  ).toContainText("不适用");
  await expect(
    page.locator(".fundamental-evidence-grid article").filter({ hasText: "存货与收入" }),
  ).toContainText("数据不足");
  await expect(page.getByText("无法确定", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("等待另一平台数据")).toBeVisible();
  await expect(
    page.locator(".discussion-platform--empty").filter({ hasText: "雪球" }),
  ).toContainText("暂无数据");
});
