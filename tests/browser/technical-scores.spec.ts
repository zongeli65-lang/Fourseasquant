import { expect, test } from "@playwright/test";

const status = {
  status: "ready",
  publication: {
    version: "technical-v1",
    official_start: "2025-07-25",
    official_end: "2026-07-24",
    qfq_source: "test-qfq",
    symbol_count: 101,
    score_count: 24000,
    published_at: "2026-07-24T16:30:00+08:00",
  },
  parameters: {
    structure_weight: 55,
    breakout_weight: 20,
    relative_strength_weight: 15,
    turnover_weight: 10,
    minimum_leader_score: 65,
    ema_span: 3,
    atr_period: 10,
  },
};

function score(
  code: string,
  name: string,
  options: {
    rank: number | null;
    current: boolean;
    date?: string;
    board?: "main" | "chinext" | "star";
  },
) {
  return {
    version: "technical-v1",
    actual_data_date: options.date ?? "2026-07-24",
    code,
    name,
    board: options.board ?? "main",
    ema3: 10,
    derivative: 0.1,
    derivative_state: "positive",
    zero_threshold: 0.01,
    atr10: 0.5,
    structure_state: "strong",
    structure_valid: true,
    active_breakout: false,
    structure_score: 50,
    breakout_score: 15,
    relative_strength_score: 10,
    turnover_score: 5,
    total_score: options.current ? 80 : 75,
    maxima: [],
    minima: [],
    evidence: {},
    rank: options.rank,
    is_current: options.current,
  };
}

test("技术评分页可以搜索和翻阅全市场评分，并与策略页保持独立", async ({ page }) => {
  const requestedPages: number[] = [];
  const requestedSearches: string[] = [];
  await page.route("**/api/technical-scores/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(status),
    });
  });
  await page.route(/\/api\/technical-scores\?.*/, async (route) => {
    const url = new URL(route.request().url());
    const requestedPage = Number(url.searchParams.get("page") ?? "1");
    const search = url.searchParams.get("search") ?? "";
    requestedPages.push(requestedPage);
    requestedSearches.push(search);
    const stale = score("002036", "联创电子", {
      rank: null,
      current: false,
      date: "2026-07-22",
    });
    const items = search.includes("联创")
      ? [stale]
      : requestedPage === 2
        ? [stale]
        : [
            score("600001", "当前高分", { rank: 1, current: true }),
            score("300001", "当前次高", {
              rank: 2,
              current: true,
              board: "chinext",
            }),
          ];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        requested_date: "2026-07-24",
        actual_data_date: "2026-07-24",
        total: search ? 1 : 101,
        universe_count: 101,
        current_score_count: 100,
        stale_score_count: 1,
        page: requestedPage,
        page_size: 100,
        items,
      }),
    });
  });

  await page.goto("/technical-scores?target_date=2026-07-24");

  await expect(page.getByRole("heading", { name: "全市场个股技术评分" })).toBeVisible();
  await expect(page.getByRole("link", { name: "技术评分" })).toHaveClass(
    /primary-nav__link--active/,
  );
  await expect(page.getByText("当前高分", { exact: true })).toBeVisible();
  await expect(page.getByText("第 1 / 2 页")).toBeVisible();

  const searchBox = page.getByRole("searchbox", { name: "搜索技术评分" });
  await searchBox.click();
  await page.keyboard.insertText("联");
  await expect.poll(() => requestedSearches.includes("联")).toBe(true);
  await expect(searchBox).toBeFocused();
  await page.keyboard.insertText("创");
  await expect(searchBox).toHaveValue("联创");
  await searchBox.fill("");
  await expect(page.getByText("当前高分", { exact: true })).toBeVisible();

  await expect
    .poll(() =>
      page.locator("html").evaluate(
        (element) => getComputedStyle(element).overscrollBehaviorX,
      ),
    )
    .toBe("none");
  await expect
    .poll(() =>
      page.locator(".technical-table-wrap").evaluate(
        (element) => getComputedStyle(element).overscrollBehaviorX,
      ),
    )
    .toBe("contain");

  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("联创电子", { exact: true })).toBeVisible();
  await expect(page.getByText("目标日无行情 · 最近评分 2026-07-22")).toBeVisible();
  await expect.poll(() => requestedPages.includes(2)).toBe(true);

  await searchBox.fill("联创");
  await expect(page.getByText("匹配 1 只")).toBeVisible();
  await expect(page.getByText("联创电子", { exact: true })).toBeVisible();
  await expect.poll(() => requestedSearches.includes("联创")).toBe(true);

  await page.getByRole("link", { name: "策略" }).click();
  await expect(page).toHaveURL(/\/strategy\?target_date=2026-07-24$/);
  await expect(page.getByRole("heading", { name: "策略复盘" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "全市场个股技术评分" }),
  ).toHaveCount(0);
});
