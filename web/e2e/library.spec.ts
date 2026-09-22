import { expect, test } from "@playwright/test";

test("shows the upload entry point", async ({ page }) => {
  await page.route("**/api/v1/artifacts", (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /Make every field/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Inspect a template/ })).toBeVisible();
});
