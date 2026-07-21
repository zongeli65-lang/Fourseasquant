import { expect, test } from "@playwright/test";

test("用户可以从仪表盘运行并查看目标日期的每日快照", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Fourseasquant" })).toBeVisible();
  await page.getByRole("textbox", { name: "目标日期" }).fill("2026-07-20");
  await expect(page.getByTestId("task-status")).toHaveText("尚未运行");
  await expect(page.getByTestId("actual-data-date")).toHaveText("尚无已发布快照");
  await expect(page.getByText("尚无板块快照，请先运行目标日期任务。")).toBeVisible();

  await page.getByRole("button", { name: "运行目标日期任务" }).click();

  await expect(page.getByTestId("task-status")).toHaveText("运行成功");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-20");
  await expect(page.getByText("确定性模拟快照")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "沪深主板市场概览" }),
  ).toBeVisible();
  await expect(page.getByTestId("eligible-security-count")).toHaveText("5 只");
  await expect(page.getByTestId("index-上证指数")).toContainText("+0.62%");
  await expect(page.getByTestId("index-深证成指")).toContainText("-0.31%");
  await expect(page.getByTestId("breadth-advancers")).toHaveText(
    "2 只 · 40.00%",
  );
  await expect(page.getByTestId("limit-up-count")).toHaveText("1 只");
  await expect(page.getByTestId("limit-down-count")).toHaveText("1 只");
  await expect(page.getByTestId("limit-up-streak")).toHaveText("3 板");
  await expect(page.getByTestId("market-turnover")).toContainText("2700.00 亿元");
  await expect(page.getByTestId("market-turnover")).toContainText(
    "较近 20 日 +12.50%",
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
});
