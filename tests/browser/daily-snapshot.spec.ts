import { expect, test } from "@playwright/test";

test("用户可以从仪表盘运行并查看目标日期的每日快照", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Fourseasquant" })).toBeVisible();
  await page.getByRole("textbox", { name: "目标日期" }).fill("2026-07-20");
  await expect(page.getByTestId("task-status")).toHaveText("尚未运行");
  await expect(page.getByTestId("actual-data-date")).toHaveText("尚无已发布快照");
  await expect(page.getByText("尚无板块快照，请先运行目标日期任务。")).toBeVisible();

  await page.getByRole("button", { name: "设置" }).click();
  await page.getByLabel("自动更新时间").fill("16:45");
  await page.getByLabel("默认基准").selectOption("中证 500");
  await page.getByLabel("数据源适配器").selectOption("simulation_conservative");
  await page.getByLabel("新股排除交易日").fill("15");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByTestId("settings-save-status")).toHaveText("保存成功");
  await page.getByRole("button", { name: "关闭设置" }).click();

  await page.getByRole("button", { name: "运行目标日期任务" }).click();

  await expect(page.getByTestId("task-status")).toHaveText("运行成功");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-20");
  await expect(page.getByText("保守模拟快照")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "沪深主板市场概览" }),
  ).toBeVisible();
  await expect(page.getByTestId("eligible-security-count")).toHaveText("6 只");
  await expect(page.getByTestId("index-上证指数")).toContainText("+0.62%");
  await expect(page.getByTestId("index-深证成指")).toContainText("-0.31%");
  await expect(page.getByTestId("breadth-advancers")).toHaveText(
    "3 只 · 50.00%",
  );
  await expect(page.getByTestId("limit-up-count")).toHaveText("1 只");
  await expect(page.getByTestId("limit-down-count")).toHaveText("1 只");
  await expect(page.getByTestId("limit-up-streak")).toHaveText("3 板");
  await expect(page.getByTestId("market-turnover")).toContainText("10530.00 亿元");
  await expect(page.getByTestId("market-turnover")).toContainText(
    "较近 20 日 +244.12%",
  );
  await expect(page.getByTestId("new-high-20d")).toHaveText("2 只");
  await expect(page.getByTestId("new-low-20d")).toHaveText("2 只");
  await expect(page.getByText("综合情绪分数")).toHaveCount(0);

  await expect(page.getByRole("heading", { name: "板块与概念强弱" })).toBeVisible();
  await expect(page.getByTestId("sector-leaders").locator("li")).toHaveCount(10);
  await expect(page.getByTestId("sector-laggards").locator("li")).toHaveCount(10);
  await page.getByRole("button", { name: "概念板块" }).click();
  await expect(page.getByTestId("sector-category-label")).toContainText("概念板块");
  await page.getByRole("button", { name: "热力图" }).click();
  await expect(page.getByTestId("sector-heatmap").locator("div")).toHaveCount(24);

  await expect(page.getByRole("heading", { name: "策略业绩复盘" })).toBeVisible();
  await expect(page.getByText("演示策略数据")).toBeVisible();
  await expect(page.getByText("基准 · 中证 500")).toBeVisible();
  await expect(page.getByTestId("strategy-daily-return")).toBeVisible();
  await expect(page.getByTestId("benchmark-daily-return")).toBeVisible();
  await expect(page.getByTestId("excess-daily-return")).toBeVisible();
  await expect(page.getByTestId("nav-chart")).toBeVisible();
  await expect(page.getByTestId("drawdown-chart")).toBeVisible();
  await expect(page.getByTestId("strategy-statistics")).toContainText("夏普比率");
  const allRangeStatistics = await page.getByTestId("strategy-statistics").textContent();
  await page.getByRole("button", { name: "近 3 月" }).click();
  await expect(page.getByTestId("selected-range")).toHaveText("近 3 月");
  await expect(page.getByTestId("strategy-statistics")).not.toHaveText(
    allRangeStatistics ?? "",
  );

  await expect(page.getByRole("heading", { name: "持仓与交易复盘" })).toBeVisible();
  await expect(page.getByText("演示组合数据")).toBeVisible();
  await expect(page.getByTestId("holdings-table").locator("tbody tr")).toHaveCount(24);
  await page.getByRole("button", { name: "当日交易" }).click();
  await expect(page.getByTestId("trades-table").locator("tbody tr")).toHaveCount(6);
  await page.getByRole("button", { name: "收益贡献" }).click();
  await expect(page.getByTestId("contributions-table").locator("tbody tr")).toHaveCount(24);
  await page.getByRole("button", { name: "按贡献排序" }).click();

  await expect(page.getByTestId("review-save-status")).toHaveText("尚无笔记");
  await page.getByRole("textbox", { name: "复盘笔记" }).fill(
    "指数分化，关注主板成交持续性。",
  );
  await page.getByRole("textbox", { name: "新标签" }).fill("放量");
  await page.getByRole("button", { name: "添加标签" }).click();
  await page.getByRole("textbox", { name: "新标签" }).fill("观察");
  await page.getByRole("button", { name: "添加标签" }).click();
  await page.getByRole("button", { name: "保存复盘" }).click();
  await expect(page.getByTestId("review-save-status")).toHaveText("保存成功");

  await page.reload();
  await page.getByRole("textbox", { name: "目标日期" }).fill("2026-07-20");
  await expect(page.getByRole("textbox", { name: "复盘笔记" })).toHaveValue(
    "指数分化，关注主板成交持续性。",
  );
  await expect(page.getByRole("button", { name: "移除 放量" })).toBeVisible();
  await expect(page.getByRole("button", { name: "移除 观察" })).toBeVisible();

  await page.getByRole("textbox", { name: "目标日期" }).fill("2026-07-21");
  await page.route("**/api/tasks/daily", async (route) => {
    await page.request.post("/api/testing/tasks/daily", {
      data: { target_date: "2026-07-21", stage: "transactional_publish" },
    });
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "测试环境模拟真实服务异常" }),
    });
  });
  await page.getByRole("button", { name: "运行目标日期任务" }).click();
  await expect(page.getByRole("heading", { name: "今日更新失败" })).toBeVisible();
  await expect(page.getByTestId("failure-stage")).toHaveText("事务发布");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-20");
  await page.getByRole("button", { name: "重新运行今日任务" }).click();
  await expect(page.getByTestId("task-status")).toHaveText("运行成功");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-21");
  await expect(page.getByRole("heading", { name: "今日更新失败" })).toHaveCount(0);
});
