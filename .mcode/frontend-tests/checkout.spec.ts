import { test, expect } from "@playwright/test";

async function startCheckout(page, clicks = 2) {
  await page.goto("/");
  for (let i = 0; i < clicks; i++) await page.locator("#add").click();
  await page.locator("button#submit").click();
  await page.waitForURL(/checkout\.stripe\.com/, { timeout: 45000 });
}

test.describe("Checkout", () => {
  test("Buy redirects to Stripe Checkout with product and total", async ({ page }) => {
    await startCheckout(page);
    expect(page.url()).toContain("checkout.stripe.com");
    await expect(page.getByText("Stubborn Attachments").first()).toBeVisible({ timeout: 30000 });
    await expect(page.getByText("$15.00").first()).toBeVisible();
    await expect(page.getByText(/Qty 3/).first()).toBeVisible();
  });

  test("paying with test card lands on success page and Restart returns home", async ({ page }) => {
    await startCheckout(page);
    await page.getByRole("textbox", { name: "Email" }).fill("test@example.com");
    const cardNumber = page.getByRole("textbox", { name: "Card number" });
    if (!(await cardNumber.isVisible())) {
      // Card accordion collapsed: open it via its (visually hidden) button.
      await page.getByTestId("card-accordion-item-button").dispatchEvent("click");
    }
    await cardNumber.waitFor({ state: "visible", timeout: 20000 });
    await page.getByRole("textbox", { name: "Card number" }).fill("4242424242424242");
    await page.getByRole("textbox", { name: "Expiration" }).fill("12 / 34");
    await page.getByRole("textbox", { name: /CVC/ }).fill("123");
    const name = page.getByRole("textbox", { name: "Cardholder name" });
    if (await name.count()) await name.fill("Test User");
    const zip = page.getByRole("textbox", { name: "ZIP" });
    if (await zip.count()) await zip.fill("12345");
    const link = page.getByRole("checkbox", { name: /Save my information/i });
    if (await link.count() && (await link.isChecked())) await link.uncheck();
    const pay = page.getByTestId("hosted-payment-submit-button");
    await pay.scrollIntoViewIfNeeded();
    await pay.click();
    await page.waitForURL(/\/success\.html\?session_id=cs_test_/, { timeout: 60000 });
    await expect(page.locator("h1")).toHaveText("Your payment succeeded");
    await expect(page.locator("h4")).toContainText("View CheckoutSession response");
    await expect.poll(async () => (await page.locator("div.sr-callout pre").first().textContent()) || "").toContain("checkout.session");
    const json = (await page.locator("div.sr-callout pre").first().textContent()) || "";
    expect(JSON.parse(json).amount_total).toBe(1500);
    await page.getByRole("button", { name: "Restart demo" }).click();
    await expect(page).toHaveURL(/localhost:4242\/?$/);
    await expect(page.locator("h1")).toHaveText("Single photo");
  });
});
