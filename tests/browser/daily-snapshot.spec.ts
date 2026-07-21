import { expect, test } from "@playwright/test";

test("用户可以从仪表盘运行并查看目标日期的每日快照", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Fourseasquant" })).toBeVisible();
  await page.getByRole("textbox", { name: "目标日期" }).fill("2026-07-20");
  await expect(page.getByTestId("task-status")).toHaveText("尚未运行");
  await expect(page.getByTestId("actual-data-date")).toHaveText("尚无已发布快照");

  await page.getByRole("button", { name: "运行目标日期任务" }).click();

  await expect(page.getByTestId("task-status")).toHaveText("运行成功");
  await expect(page.getByTestId("actual-data-date")).toHaveText("2026-07-20");
  await expect(page.getByText("确定性模拟快照")).toBeVisible();
});
