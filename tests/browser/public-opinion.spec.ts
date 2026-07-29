import { expect, test } from "@playwright/test";

test("舆论监测页由后端批次运行并可视化结果和错误", async ({ page }) => {
  const jobs = [
    {
      id: 1,
      platform: "sina",
      code: "688825",
      start_date: "2026-07-25",
      end_date: "2026-07-27",
      trigger: "automatic",
      classification_version: "legacy",
      status: "succeeded",
      cursor: null,
      updated_at: "2026-07-27T09:00:00+08:00",
      error_summary: null,
      target_source: "legacy_discovery",
      target_version: "",
    },
    {
      id: 2,
      platform: "sina",
      code: "000001",
      start_date: "2026-07-25",
      end_date: "2026-07-27",
      trigger: "automatic",
      classification_version: "public-opinion-deepseek-v1",
      status: "succeeded",
      cursor: null,
      updated_at: "2026-07-27T10:00:00+08:00",
      error_summary: null,
      target_source: "strategy",
      target_version: "core-strategy-v1",
      sample_capped: true,
      sample_limit: 300,
    },
    {
      id: 3,
      platform: "sina",
      code: "600000",
      start_date: "2026-07-25",
      end_date: "2026-07-27",
      trigger: "automatic",
      classification_version: "public-opinion-deepseek-v1",
      status: "blocked",
      cursor: null,
      updated_at: "2026-07-27T10:00:00+08:00",
      error_summary: "新浪股吧公开回复接口暂时失败",
      target_source: "strategy",
      target_version: "core-strategy-v1",
    },
    {
      id: 4,
      platform: "sina",
      code: "600519",
      start_date: "2026-07-25",
      end_date: "2026-07-27",
      trigger: "automatic",
      classification_version: "public-opinion-deepseek-v1",
      status: "pending",
      cursor: null,
      updated_at: "2026-07-27T10:00:00+08:00",
      error_summary: null,
      target_source: "strategy",
      target_version: "core-strategy-v1",
    },
    {
      id: 5,
      platform: "sina",
      code: "601606",
      start_date: "2026-07-25",
      end_date: "2026-07-27",
      trigger: "automatic",
      classification_version: "legacy",
      status: "blocked",
      cursor: null,
      updated_at: "2026-07-27T09:00:00+08:00",
      error_summary: "旧版任务不应出现在当前队列",
      target_source: "legacy_discovery",
      target_version: "",
    },
  ];
  let individualRuns = 0;
  let batchRunning = false;
  let apiConfigured = false;
  const requestedStrategyTargetDates: string[] = [];
  let scheduledTargetDate: string | null = null;

  await page.route("**/api/public-opinion/overview", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        items: [{
          code: "000001",
          name: "平安银行",
          entry_reason: "strategy_target",
          monitoring_active: true,
          eastmoney: null,
          sina: {
            actual_date: "2026-07-27",
            content_count: 300,
            valid_count: 16,
            favorable_count: 8,
            unfavorable_count: 4,
            disputed_count: 0,
            unknown_count: 0,
            classified_count: 300,
            neutral_count: 4,
            unrelated_count: 0,
            low_confidence_count: 0,
            rules_version: "public-opinion-deepseek-v1",
            direction_status: "published",
            direction: "favorable",
            sample_capped: true,
            sample_limit: 300,
          },
          tonghuashun: null,
        }],
      }),
    });
  });
  await page.route("**/api/public-opinion/jobs", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(jobs),
    });
  });
  await page.route("**/api/public-opinion/strategy-targets/latest?*", async (route) => {
    requestedStrategyTargetDates.push(
      new URL(route.request().url()).searchParams.get("actual_date") ?? "",
    );
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        actual_date: "2026-07-27",
        strategy_version: "core-strategy-v1",
        codes: ["000001"],
        published_at: "2026-07-27T09:30:00+08:00",
      }),
    });
  });
  await page.route("**/api/public-opinion/schedule", async (route) => {
    const body = route.request().postDataJSON() as { actual_date: string };
    scheduledTargetDate = body.actual_date;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });
  await page.route("**/api/public-opinion/api-configuration", async (route) => {
    if (route.request().method() === "PUT") {
      apiConfigured = true;
    } else if (route.request().method() === "DELETE") {
      apiConfigured = false;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        configured: apiConfigured,
        source: apiConfigured ? "keychain" : "unconfigured",
        transient: !apiConfigured,
        updated_at: apiConfigured ? "2026-07-27T10:00:00+08:00" : null,
      }),
    });
  });
  await page.route("**/api/public-opinion/jobs/batch-status", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        running: batchRunning,
        stop_requested: false,
        current_job_id: batchRunning ? 4 : null,
        current_code: batchRunning ? "600519" : null,
        latest_end_date: "2026-07-27",
        started_at: batchRunning ? "2026-07-27T10:01:00+08:00" : null,
        updated_at: "2026-07-27T10:01:05+08:00",
        error_summary: null,
        slices_processed: batchRunning ? 2 : 0,
        succeeded: 0,
        blocked: 0,
        failed: 0,
        remaining: 1,
        stopped: false,
        classification_model: "deepseek-v4-pro",
        prompt_version: "public-opinion-deepseek-v1",
        classifier_configured: apiConfigured,
      }),
    });
  });
  await page.route("**/api/public-opinion/jobs/run-batch", async (route) => {
    batchRunning = true;
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        running: true,
        stop_requested: false,
        current_job_id: null,
        current_code: null,
        latest_end_date: "2026-07-27",
        started_at: "2026-07-27T10:01:00+08:00",
        updated_at: "2026-07-27T10:01:00+08:00",
        error_summary: null,
        slices_processed: 0,
        succeeded: 0,
        blocked: 0,
        failed: 0,
        remaining: 1,
        stopped: false,
        classification_model: "deepseek-v4-pro",
        prompt_version: "public-opinion-deepseek-v1",
        classifier_configured: true,
      }),
    });
  });
  await page.route("**/api/public-opinion/jobs/*/run", async (route) => {
    individualRuns += 1;
    await route.fulfill({ status: 500 });
  });

  await page.goto("/public-opinion?target_date=2026-07-24");
  await expect(page.getByRole("heading", { name: "舆论监测", exact: true })).toBeVisible();
  await expect.poll(() => requestedStrategyTargetDates.at(-1)).toBe("2026-07-24");
  await expect(page.getByLabel("开始日期")).toHaveValue("2026-07-24");
  await expect(page.getByLabel("结束日期")).toHaveValue("2026-07-24");
  await expect(page.getByText("公开讨论 · DeepSeek V4 Pro 分类")).toBeVisible();
  await expect(page.getByText(/自动调查对象只来自策略板块/)).toBeVisible();
  await expect(
    page.getByText("单股窗口最多 300 条；单主题回复最多 50 条。"),
  ).toBeVisible();
  await expect(page.getByText("雪球")).toHaveCount(0);
  await expect(page.getByText("东方财富热榜")).toHaveCount(0);
  await expect(page.getByText("core-strategy-v1 · 1/10 只")).toBeVisible();
  await page.getByRole("button", { name: "生成策略调查任务" }).click();
  await expect.poll(() => scheduledTargetDate).toBe("2026-07-24");
  await expect(page.getByRole("heading", { name: "DeepSeek API 配置" })).toBeVisible();
  await expect(page.getByText("尚未配置", { exact: true })).toBeVisible();
  await page.getByLabel("DeepSeek API 密钥").fill("sk-browser-test-secret");
  await page.getByRole("button", { name: "保存到本机钥匙串" }).click();
  await expect(page.getByText("macOS 钥匙串配置已生效")).toBeVisible();
  await expect(
    page.getByText("DeepSeek API 密钥已保存到 macOS 钥匙串，后台会自动采集。"),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "创建单股调查" })).toBeVisible();
  await expect(
    page.getByText(/已按采集上限截断 · 偏利好 · 相关 16\/300 条/),
  ).toBeVisible();
  await expect(page.getByLabel("000001 内容分布")).toBeVisible();
  await expect(page.getByText("上限完成")).toBeVisible();
  await expect(page.getByText("新浪股吧公开回复接口暂时失败")).toBeVisible();
  await expect(page.getByText("旧版任务不应出现在当前队列")).toHaveCount(0);
  await expect(
    page.locator(".public-opinion-stat-grid > div").filter({ hasText: "当前版成功" }),
  ).toHaveText("当前版成功1");

  await page.getByRole("button", { name: "启动后台轮转" }).click();

  await expect(page.getByRole("button", { name: "后台批次运行中" })).toBeDisabled();
  await expect(page.getByText(/后端已执行 2 个请求分片；当前 600519/)).toBeVisible();
  expect(individualRuns).toBe(0);
});
