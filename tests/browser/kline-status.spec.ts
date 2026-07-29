import { expect, test } from "@playwright/test";

test("K线页面明确显示四种发布状态", async ({ page }) => {
  const cases = [
    {
      status: "ready",
      latest: "2026-07-24",
      message: "目标日期 2026-07-24 的K线已经完整发布。",
    },
    {
      status: "updating",
      latest: "2026-07-23",
      message: "K线正在更新：目标日期 2026-07-24，当前完整版本 2026-07-23。",
    },
    {
      status: "stale",
      latest: "2026-07-23",
      message: "K线尚未完整：目标日期 2026-07-24，当前完整版本 2026-07-23。请运行或重试数据更新。",
    },
    {
      status: "unavailable",
      latest: null,
      message: "目标日期 2026-07-24 前尚无完整K线版本。",
    },
  ] as const;
  let selectedCase: (typeof cases)[number] = cases[0];
  await page.route("**/api/market-data/candles/status?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_date: "2026-07-24",
        latest_published_date: selectedCase.latest,
        status: selectedCase.status,
        message: selectedCase.message,
      }),
    });
  });
  await page.route("**/api/market-data/candles?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        source: "akshare",
        instrument_type: "index",
        code: "sh000001",
        name: "上证指数",
        adjustment: "raw",
        requested_date: "2026-07-24",
        actual_data_date: "2026-07-23",
        coverage_start: "2026-07-23",
        coverage_end: "2026-07-23",
        candles: [{
          date: "2026-07-23",
          open: 3600,
          high: 3620,
          low: 3590,
          close: 3610,
          volume: 100000000,
          turnover_cny: 0,
          change_pct: 0.3,
          rsi14: null,
        }],
        trades: [],
      }),
    });
  });

  for (const scenario of cases) {
    selectedCase = scenario;
    await page.goto(
      `/quotes?target_date=2026-07-24&status_case=${scenario.status}`,
    );
    await expect(page.getByTestId("candle-availability")).toHaveText(
      scenario.message,
    );
  }
});

test("浏览器前进后退后行情选择与网址股票参数保持一致", async ({ page }) => {
  await page.route("**/api/market-data/candles/status?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_date: "2026-07-24",
        latest_published_date: "2026-07-24",
        status: "ready",
        message: "目标日期 2026-07-24 的K线已经完整发布。",
      }),
    });
  });
  await page.route("**/api/market-data/candles?*", async (route) => {
    const url = new URL(route.request().url());
    const instrumentType = url.searchParams.get("instrument_type") ?? "index";
    const code = url.searchParams.get("code") ?? "sh000001";
    const name = code === "000001" ? "平安银行" : "沪深 300";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        source: "akshare",
        instrument_type: instrumentType,
        code,
        name,
        adjustment: instrumentType === "stock" ? "qfq" : "raw",
        requested_date: "2026-07-24",
        actual_data_date: "2026-07-24",
        coverage_start: "2026-07-24",
        coverage_end: "2026-07-24",
        candles: [{
          date: "2026-07-24",
          open: 10,
          high: 10.2,
          low: 9.9,
          close: 10.1,
          volume: 1000000,
          turnover_cny: 10000000,
          change_pct: 1,
          rsi14: 50,
        }],
        trades: [],
      }),
    });
  });

  await page.goto(
    "/quotes?target_date=2026-07-24"
      + "&instrument=stock%3A000001&instrument_name=%E5%B9%B3%E5%AE%89%E9%93%B6%E8%A1%8C",
  );
  await expect(
    page.getByRole("heading", { name: "平安银行 000001" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "沪深 300" }).click();
  await expect(page).toHaveURL(/instrument=index%3Ash000300/);
  await expect(
    page.getByRole("heading", { name: "沪深 300 sh000300" }),
  ).toBeVisible();

  await page.goBack();

  await expect(page).toHaveURL(/instrument=stock%3A000001/);
  await expect(
    page.getByRole("heading", { name: "平安银行 000001" }),
  ).toBeVisible();
});
