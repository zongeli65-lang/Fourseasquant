import { expect, test } from "@playwright/test";

test("产业链页面可以持久控制开关并提交真实请求队列", async ({ page }) => {
  let enabled = false;
  const hunts: Record<string, unknown>[] = [];
  const status = () => ({
    enabled,
    state: enabled ? "running" : "paused",
    paused_at: "2026-07-26T18:00:00+08:00",
    resumed_at: enabled ? "2026-07-26T18:10:00+08:00" : null,
    catchup_from: enabled ? "2026-07-26T18:00:00+08:00" : null,
    worker_heartbeat_at: null,
    worker_online: false,
    last_poll_at: null,
    last_model_run_at: null,
    last_cleanup_at: null,
    error_summary: null,
    queued_count: hunts.length + (enabled ? 1 : 0),
    running_count: 0,
    discovered_count: 156,
    triaged_discovery_count: 28,
    untriaged_count: 128,
    research_material_count: 12,
    event_cluster_count: 17,
    investigated_company_count: 9,
    freshness_cutoff: "2026-07-23T12:00:00+08:00",
    deep_hunt_count: 1,
    completed_selection_count: 0,
    invalid_event_count: 1,
    failed_hunt_count: 0,
    published_count: 0,
    next_scheduled_scan_at: enabled ? "2026-07-26T21:00:00+08:00" : null,
    updated_at: "2026-07-26T18:10:00+08:00",
  });

  await page.route("**/api/industry-chain/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(status()),
    });
  });
  await page.route("**/api/industry-chain/control", async (route) => {
    enabled = Boolean((route.request().postDataJSON() as { enabled: boolean }).enabled);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(status()),
    });
  });
  await page.route(/\/api\/industry-chain\/hunts(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      const payload = route.request().postDataJSON() as {
        trigger_type: string;
        content: string;
      };
      const record = {
        request_id: "ich-manual-001",
        trigger_method: "manual",
        trigger_type: payload.trigger_type,
        trigger_content: payload.content,
        source_url: null,
        as_of_time: "2026-07-26T18:11:00+08:00",
        priority: 1,
        status: "queued",
        requested_at: "2026-07-26T18:11:00+08:00",
        started_at: null,
        completed_at: null,
        error_summary: null,
      };
      hunts.unshift(record);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(record),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(hunts),
    });
  });
  await page.route("**/api/industry-chain/sources", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          source_id: "cninfo",
          source_version: 1,
          source_name: "巨潮资讯网",
          base_url: "https://www.cninfo.com.cn/",
          domain: "cninfo.com.cn",
          source_tier: 1,
          source_type: "exchange_disclosure",
          categories: ["company_disclosure"],
          access_class: "public_no_login",
          lifecycle_state: "active",
          poll_interval_minutes: 3,
          allow_browser: true,
          config_version: "industry-chain-sources-v0.2",
          effective_at: "2026-07-26T18:00:00+08:00",
        },
      ]),
    });
  });
  await page.route("**/api/industry-chain/selections?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });

  await page.goto("/industry-chain-leaders");

  const toggle = page.getByRole("switch");
  await expect(page.getByRole("heading", { name: "产业链供需狩猎" })).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  await expect(
    page.getByText("本地模型已配置，但后台执行器当前不在线"),
  ).toBeVisible();
  await expect(page.getByText("本轮新闻仍在初筛")).toBeVisible();
  await expect(page.getByText("还有 128 条待处理")).toBeVisible();
  await expect(page.getByText("28 / 156")).toBeVisible();
  await expect(page.getByRole("button", { name: "立即狩猎" })).toBeDisabled();

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await page.getByPlaceholder("例如：铜精矿供应中断").fill("铜精矿供应中断");
  await page.getByRole("button", { name: "立即狩猎" }).click();

  await expect(page.getByText("狩猎请求已进入队列。")).toBeVisible();
  await expect(page.getByText("铜精矿供应中断")).toBeVisible();
  await expect(page.getByText("等待分析")).toBeVisible();
  await expect(page.getByText("巨潮资讯网")).toBeVisible();
});

test("用户可以从候选池删除分析且无需删除正式证据", async ({ page }) => {
  let selections = [
    {
      selection_id: "selection-browser-001",
      selection_version: 1,
      event_id: "event-browser-001",
      as_of_time: "2026-07-26T18:00:00+08:00",
      completed_at: "2026-07-26T18:05:00+08:00",
      status: "selected",
      event: {
        title: "铜精矿供应收缩",
        affected_product_or_service: "铜精矿",
        gap_node: "矿山供给",
        trigger_types: ["supply_contraction"],
      },
      candidates: [],
      summary: "测试候选分析。",
      unresolved_items: [],
    },
  ];
  await page.route("**/api/industry-chain/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        enabled: false,
        state: "paused",
        paused_at: "2026-07-26T18:00:00+08:00",
        resumed_at: null,
        catchup_from: null,
        worker_heartbeat_at: "2026-07-26T18:05:00+08:00",
        worker_online: true,
        last_poll_at: "2026-07-26T18:04:00+08:00",
        last_model_run_at: "2026-07-26T18:05:00+08:00",
        last_cleanup_at: "2026-07-26T18:03:00+08:00",
        error_summary: null,
        queued_count: 0,
        running_count: 0,
        discovered_count: 1,
        triaged_discovery_count: 1,
        untriaged_count: 0,
        research_material_count: 0,
        event_cluster_count: 1,
        investigated_company_count: 1,
        freshness_cutoff: "2026-07-23T12:00:00+08:00",
        deep_hunt_count: 1,
        completed_selection_count: 1,
        invalid_event_count: 0,
        failed_hunt_count: 0,
        published_count: selections.length,
        next_scheduled_scan_at: null,
        updated_at: "2026-07-26T18:05:00+08:00",
      }),
    });
  });
  await page.route("**/api/industry-chain/hunts?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });
  await page.route("**/api/industry-chain/sources", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });
  await page.route(/\/api\/industry-chain\/selections(?:\/.*|\?.*)?$/, async (route) => {
    if (route.request().method() === "DELETE") {
      selections = [];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          selection_id: "selection-browser-001",
          selection_version: 1,
          deleted: true,
          dismissed_at: "2026-07-26T18:06:00+08:00",
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(selections),
    });
  });
  page.on("dialog", (dialog) => void dialog.accept());

  await page.goto("/industry-chain-leaders");

  await expect(page.getByText("铜精矿供应收缩")).toBeVisible();
  await page.getByRole("button", { name: "删除分析：铜精矿供应收缩" }).click();
  await expect(page.getByText("已从候选池删除该分析。")).toBeVisible();
  await expect(page.getByText("铜精矿供应收缩")).toHaveCount(0);
});
