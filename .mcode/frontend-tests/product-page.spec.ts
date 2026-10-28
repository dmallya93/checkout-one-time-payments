import { test, expect } from "@playwright/test";

test.describe("Product page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  test("renders product details and form", async ({ page }) => {
    await expect(page.locator("h1")).toHaveText("Single photo");
    await expect(page.locator("h4").first()).toHaveText("Purchase a Pasha original photo");
    await expect(page.locator("img").first()).toBeVisible();
    const form = page.locator("form");
    await expect(form).toHaveAttribute("action", "/create-checkout-session");
    await expect(form).toHaveAttribute("method", /post/i);
    await expect(page.locator("p.sr-legal-text")).toHaveText("Number of copies (max 10)");
    await expect(page.locator("button#submit")).toHaveText("Buy");
  });

  test("quantity starts at 1 with minus disabled", async ({ page }) => {
    await expect(page.locator("#quantity-input")).toHaveValue("1");
    await expect(page.locator("#subtract")).toBeDisabled();
    await expect(page.locator("#add")).toBeEnabled();
  });

  test("plus and minus change quantity and toggle disabled state", async ({ page }) => {
    const q = page.locator("#quantity-input");
    await page.locator("#add").click();
    await expect(q).toHaveValue("2");
    await expect(page.locator("#subtract")).toBeEnabled();
    await page.locator("#subtract").click();
    await expect(q).toHaveValue("1");
    await expect(page.locator("#subtract")).toBeDisabled();
  });

  test("plus disables at 10", async ({ page }) => {
    for (let i = 0; i < 9; i++) await page.locator("#add").click();
    await expect(page.locator("#quantity-input")).toHaveValue("10");
    await expect(page.locator("#add")).toBeDisabled();
    await expect(page.locator("#subtract")).toBeEnabled();
  });

  test("typed values are clamped to 1..10", async ({ page }) => {
    const q = page.locator("#quantity-input");
    await q.fill("50");
    await q.dispatchEvent("change");
    await expect(q).toHaveValue("10");
    await q.fill("-4");
    await q.dispatchEvent("change");
    await expect(q).toHaveValue("1");
  });

  test("static assets load", async ({ request }) => {
    for (const p of ["/css/normalize.css", "/css/global.css", "/index.js", "/favicon.ico"]) {
      const r = await request.get(p);
      expect(r.status(), p).toBe(200);
    }
  });
});
