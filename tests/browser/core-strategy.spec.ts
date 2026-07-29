import { expect, test } from "@playwright/test";

test("核心策略页面展示机械机会、模拟订单与完整组合", async ({ page }) => {
  const targetDate = "2026-07-29";
  let resetRequested = false;
  await page.route("**/api/core-strategy/account", async (route) => {
    if (route.request().method() !== "DELETE") {
      await route.fallback();
      return;
    }
    resetRequested = true;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        reset_at: "2026-07-29T17:30:00+08:00",
        previous_initial_capital: 100000,
        removed_preparation_count: 1,
        removed_strategy_day_count: 1,
        removed_portfolio_count: 1,
        removed_fundamental_target_count: 1,
        removed_opinion_target_count: 1,
        detached_opinion_job_count: 1,
      }),
    });
  });
  await page.route("**/api/core-strategy/run-status*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_date: targetDate,
        actual_date: targetDate,
        strategy_version: "core-strategy-v1",
        stage: "finalized",
        message: "核心策略日和完整组合已经发布",
        account: {
          initial_capital: 100000,
          initialized_at: "2026-07-20T16:00:00+08:00",
        },
        candidate_count: 1,
        fundamental_target_count: 1,
        opinion_target_codes: ["600001"],
        opinion_jobs: {
          pending: 0,
          running: 0,
          succeeded: 1,
          failed: 0,
          blocked: 0,
        },
        prepared_at: "2026-07-29T15:10:00+08:00",
        auto_finalize_at: "2026-07-29T16:10:00+08:00",
        finalized_at: "2026-07-29T16:30:00+08:00",
        order_count: 1,
        holding_count: 1,
        available_cash: 74990,
        net_asset_value: 99990,
        portfolio_publication_complete: true,
        latest_attempt: {
          action: "finalize",
          status: "succeeded",
          attempted_at: "2026-07-29T16:30:00+08:00",
          error_summary: null,
        },
      }),
    });
  });
  await page.route("**/api/core-strategy/days/*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        snapshot: {
          actual_date: targetDate,
          strategy_version: "core-strategy-v1",
          market_state: "sideways",
          entry_planning: {
            maximum_positions: 3,
            full_position_slot: 33333.33,
            remaining_cash: 74990,
            candidates: [
              {
                code: "600001",
                name: "测试公司",
                route: "pure_technical",
                preliminary_rank: 1,
                base_grade: "B",
                grade: "A",
                net_reward_risk_ratio: 2.63,
                position_fraction: 0.75,
                open_allowed: true,
                block_reasons: [],
                fundamental_priority: "preferred",
              },
            ],
            orders: [
              {
                code: "600001",
                name: "测试公司",
                execution_price: 10,
                shares: 2500,
                total_cash: 25010,
                grade: "A",
                stop_price: 8.9,
                pressure_target: 13,
              },
            ],
          },
          position_management: { orders: [] },
        },
        published_at: "2026-07-29T16:30:00+08:00",
      }),
    });
  });
  await page.route("**/api/core-strategy/portfolio/*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        snapshot: {
          actual_date: targetDate,
          initial_capital: 100000,
          available_cash: 74990,
          net_asset_value: 99990,
          positions: [
            {
              code: "600001",
              name: "测试公司",
              shares: 2500,
              cost_price: 10,
              stop_price: 8.9,
              pressure_target: 13,
              highest_close_since_entry: 10,
              final_profit_line: null,
              pending_exit_reason: null,
            },
          ],
          marks: [
            {
              code: "600001",
              price: 10,
              mark_date: targetDate,
              source: "entry_execution",
            },
          ],
        },
        published_at: "2026-07-29T16:30:00+08:00",
      }),
    });
  });

  await page.goto(`/strategy?target_date=${targetDate}`);

  await expect(
    page.getByRole("heading", { name: "唯一核心交易策略" }),
  ).toBeVisible();
  await expect(page.getByText("策略日已完成")).toBeVisible();
  await expect(page.getByText("震荡", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "当日候选与开仓许可" }),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: "允许开仓" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "当日订单" }),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: "A 级分歧买入" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "收盘后持仓" }),
  ).toBeVisible();
  await expect(page.getByRole("cell", { name: "持有" })).toBeVisible();
  await expect(page.getByText("演示策略数据")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "按当前证据完成策略日" }),
  ).toHaveCount(0);
  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "清空全部" }).click();
  await expect(
    page.getByText("模拟账户已经清空，可以重新设置初始资金。"),
  ).toBeVisible();
  expect(resetRequested).toBe(true);
});

test("核心策略首次初始化页面在最低桌面宽度无页面级横向滚动", async ({ page }) => {
  const targetDate = "2026-07-29";
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.route("**/api/core-strategy/run-status*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_date: targetDate,
        actual_date: null,
        strategy_version: "core-strategy-v1",
        stage: "account_uninitialized",
        message: "请先设置模拟账户初始资金",
        account: null,
        candidate_count: 0,
        fundamental_target_count: 0,
        opinion_target_codes: [],
        opinion_jobs: {
          pending: 0,
          running: 0,
          succeeded: 0,
          failed: 0,
          blocked: 0,
        },
        prepared_at: null,
        auto_finalize_at: null,
        finalized_at: null,
        order_count: 0,
        holding_count: 0,
        available_cash: null,
        net_asset_value: null,
        portfolio_publication_complete: false,
        latest_attempt: null,
      }),
    });
  });

  await page.goto(`/strategy?target_date=${targetDate}`);

  await expect(
    page.getByRole("heading", { name: "唯一核心交易策略" }),
  ).toBeVisible();
  await expect(page.getByLabel("初始资金（元）")).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    page: document.documentElement.scrollWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);
});
