import { expect, test } from "@playwright/test";

test("用户可以从仪表盘运行并查看目标日期的每日快照", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(/Fourseasquant/);
  await expect(
    page.getByRole("heading", { name: "个股技术评分尚未初始化" }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "目标日期" }).fill("2026-07-20");
  await expect(page.getByTestId("task-status")).toHaveText("尚未运行");
  await expect(page.getByTestId("actual-data-date")).toHaveText("尚无已发布快照");
  await expect(page.getByText("当前不会生成模拟板块龙头")).toBeVisible();

  await page.getByRole("button", { name: "设置" }).click();
  await page.getByLabel("自动更新时间").fill("16:45");
  await page.getByLabel("默认基准").selectOption("中证 500");
  await page.getByLabel("数据源适配器").selectOption("simulation_conservative");
  await page.getByLabel("新股排除交易日").fill("15");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByTestId("settings-save-status")).toHaveText("保存成功");
  await page.getByRole("button", { name: "关闭设置" }).click();

  await page.getByRole("link", { name: "任务" }).click();
  await page.getByRole("button", { name: "运行目标日期任务" }).click();

  await expect(page.getByTestId("task-status")).toHaveText("运行成功");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-20");
  await expect(page.getByText("保守模拟快照")).toBeVisible();

  await page.getByRole("link", { name: "市场", exact: true }).click();
  await expect(page.getByRole("heading", { name: "沪深主板市场概览" })).toHaveCount(0);
  await expect(page.getByText("目标日期前尚无 AKShare 真实历史数据。")).toBeVisible();
  await expect(page.getByText("综合情绪分数")).toHaveCount(0);

  await expect(page.getByRole("heading", { name: "板块与概念强弱" })).toHaveCount(0);

  await page.getByRole("link", { name: "策略" }).click();
  await expect(
    page.getByRole("heading", { name: "唯一核心交易策略" }),
  ).toBeVisible();
  await expect(page.getByText("账户未初始化")).toBeVisible();
  await expect(page.getByLabel("初始资金（元）")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "初始化模拟账户" }),
  ).toBeVisible();
  await expect(page.getByText("演示策略数据")).toHaveCount(0);

  await page.getByRole("link", { name: "总览" }).click();
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
  await page.getByRole("link", { name: "任务" }).click();
  await page.getByRole("button", { name: "运行目标日期任务" }).click();
  await expect(page.getByRole("heading", { name: "今日更新失败" })).toBeVisible();
  await expect(page.getByTestId("failure-stage")).toHaveText("事务发布");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-20");
  await page.getByRole("button", { name: "重新运行今日任务" }).click();
  await expect(page.getByTestId("task-status")).toHaveText("运行成功");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-21");
  await expect(page.getByRole("heading", { name: "今日更新失败" })).toHaveCount(0);

  await page.getByRole("button", { name: "设置" }).click();
  await page.getByLabel("补算开始日期").fill("2026-07-17");
  await page.getByLabel("补算结束日期").fill("2026-07-21");
  await page.getByRole("button", { name: "预览交易日" }).click();
  await expect(page.getByTestId("backfill-preview-count")).toHaveText("预计处理 3 个交易日");
  await page.getByRole("button", { name: "确认补算" }).click();
  await expect(page.getByTestId("backfill-result")).toHaveText("补算完成：成功 3 日，失败 0 日。");
  await expect(page.getByTestId("backfill-day-results").locator(":scope > div")).toHaveCount(3);
  await page.getByRole("button", { name: "关闭设置" }).click();
  await expect(page.getByRole("button", { name: "关闭设置" })).toHaveCount(0);
  await page.getByRole("link", { name: "总览" }).click();
  await expect(page).toHaveURL(/\/overview\?/);
  await expect(
    page.getByRole("heading", { name: "收盘后的全局视图" }),
  ).toBeVisible();
  await page.getByRole("textbox", { name: "目标日期" }).fill("2026-07-20");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-20");
  await expect(page.getByRole("textbox", { name: "复盘笔记" })).toHaveValue(
    "指数分化，关注主板成交持续性。",
  );
});
