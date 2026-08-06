import { expect, test } from "@playwright/test";

const snapshot = {
  schema_version: "market-environment-snapshot-v2",
  actual_data_date: "2026-07-24",
  rules_version: "market-environment-contextual-momentum-v8",
  trend_id: "market-env-2026-07-22-sideways",
  trend_state: "sideways",
  trend_changed: false,
  trend_start_date: "2026-07-22",
  validation_state: "not_required",
  validation_deadline: null,
  fast_bull_streak: 0,
  fast_bear_streak: 0,
  sideways_streak: 4,
  extreme_decline: false,
  contextual_momentum: {
    baseline_state: "rising",
    momentum_phase: "bullish_exhaustion",
    momentum_event: "top_exhaustion",
    prior_directional_streak: 8,
    prior_move_atr: 4.8,
    prior_directional_event_count: 3,
    overextended_context: "bullish",
    reversal_candidate: "bearish",
    candidate_age: 0,
    strong_reversal_verified: false,
    contextual_takeover: false,
    active_override: null,
    released_to_sideways: false,
    close_impulse_atr: -0.4,
    body_impulse_atr: -0.55,
    close_location: -0.6,
    advancing_index_count: 0,
    declining_index_count: 5,
    bullish_one_atr_count: 0,
    bearish_one_atr_count: 2,
  },
  indices: [
    {
      code: "sh000001",
      name: "上证指数",
      close: 3814.2,
      change_pct: -1.61,
      fast_direction: "neutral",
      fast_bull_votes: 0,
      fast_bear_votes: 1,
      return_3d_standardized: -0.68,
      slope_5d_standardized: 0.07,
      breakout_10d: false,
      breakdown_10d: false,
      slow_bull_votes: 0,
      slow_bear_votes: 3,
      return_10d_standardized: -2.38,
      slope_10d_standardized: -0.16,
      swing_structure: "bearish",
      breakout_held_2d: false,
      breakdown_held_2d: false,
    },
    {
      code: "sz399001",
      name: "深证成指",
      close: 13774.68,
      change_pct: -2.47,
      fast_direction: "neutral",
      fast_bull_votes: 0,
      fast_bear_votes: 1,
      return_3d_standardized: -1.11,
      slope_5d_standardized: 0.04,
      breakout_10d: false,
      breakdown_10d: false,
      slow_bull_votes: 0,
      slow_bear_votes: 3,
      return_10d_standardized: -2.74,
      slope_10d_standardized: -0.23,
      swing_structure: "bearish",
      breakout_held_2d: false,
      breakdown_held_2d: false,
    },
  ],
  breadth: {
    state: "weak",
    eligible_count: 3036,
    advancers: 247,
    decliners: 2773,
    unchanged: 16,
    advancer_ratio: 8.14,
    advancer_ratio_5d_average: 42.1,
    positive_breadth_days_5d: 1,
    new_high_20d: 51,
    new_low_20d: 630,
    high_low_ratio: 0.081,
    strong_votes: 0,
    weak_votes: 3,
  },
  capacity: {
    state: "insufficient",
    turnover_cny: 1_180_640_550_882,
    turnover_20d_median_cny: 1_576_701_722_370,
    ratio_to_20d_median: 0.7488,
  },
  cost_pressure: {
    lookback_days: 120,
    upper_trapped_pressure_pct: 93.5,
    lower_profit_pressure_pct: 5.86,
    display_only: true,
    explanation: "仅展示",
  },
  warnings: [],
  reasons: ["快速方向连续两日未形成一致上升或下降"],
  data_sources: [
    "akshare_index_sh000001",
    "akshare_index_sz399001",
    "akshare_index_sh000300",
    "akshare_index_sz399006",
    "akshare_index_sh000688",
    "akshare_sina_daily",
  ],
  generated_at: "2026-07-24T16:30:00+08:00",
};

test("市场环境板块独立展示机械趋势和证据", async ({ page }) => {
  let refreshCount = 0;
  await page.route(/\/api\/market-environment\?target_date=.*/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(snapshot),
    });
  });
  await page.route(/\/api\/market-environment\/history\?.*/, async (route) => {
    expect(new URL(route.request().url()).searchParams.get("limit")).toBe("320");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_end_date: "2026-07-24",
        rules_version: "market-environment-contextual-momentum-v8",
        items: [
          {
            ...snapshot,
            actual_data_date: "2025-07-24",
            trend_state: "rising",
            trend_changed: true,
          },
          {
            ...snapshot,
            actual_data_date: "2025-07-25",
            trend_state: "rising",
          },
          {
            ...snapshot,
            actual_data_date: "2026-01-05",
            trend_state: "sideways",
            trend_changed: true,
          },
          {
            ...snapshot,
            actual_data_date: "2026-03-04",
            trend_state: "falling",
            trend_changed: true,
          },
          { ...snapshot, trend_changed: true },
        ],
      }),
    });
  });
  await page.route("**/api/market-environment/refresh", async (route) => {
    refreshCount += 1;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: "market-env-run-test",
        requested_date: "2026-07-24",
        actual_data_date: "2026-07-24",
        rules_version: "market-environment-contextual-momentum-v8",
        inserted_count: 0,
        snapshot_count: 283,
        completed_at: "2026-07-27T13:00:00+08:00",
      }),
    });
  });

  await page.goto("/market-environment?target_date=2026-07-24");

  await expect(page.getByRole("heading", { name: "机械市场环境" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "震荡环境" })).toBeVisible();
  await expect(page.getByText("与个股技术分独立")).toBeVisible();
  await expect(page.getByRole("heading", { name: "上证指数" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "深证成指" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "广度弱" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "容量不足" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "多头衰竭" })).toBeVisible();
  await expect(page.getByText("第八版 · 五指数动能与趋势背景已计算")).toBeVisible();
  await expect(page.getByText("仅展示，不改判")).toBeVisible();
  await expect(page.getByRole("heading", { name: "市场环境历史标注" })).toBeVisible();
  const yearSection = page.locator(".market-environment-year");
  await expect(yearSection).toContainText("共 5 个交易日");
  await expect(
    yearSection.locator('.market-environment-year__summary article[data-state="rising"]'),
  ).toContainText("2日");
  await expect(yearSection.locator(".market-environment-year__track span")).toHaveCount(5);
  await expect(yearSection.locator(".market-environment-year__periods article")).toHaveCount(4);

  await page.getByRole("button", { name: "重新生成" }).click();
  await expect.poll(() => refreshCount).toBe(1);
});
