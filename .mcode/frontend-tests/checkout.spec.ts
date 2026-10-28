import { test, expect } from "@playwright/test";

test.describe("Product page", () => {
  test("renders heading, photo, quantity setter and Buy", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle("Stripe Checkout Sample");
    await expect(page.locator("h1")).toHaveText("Single photo");
    await expect(page.locator("h4")).toHaveText("Purchase a Pasha original photo");
    await expect(page.locator("img").first()).toBeAttached();
    await expect(page.locator("#quantity-input")).toHaveValue("1");
    await expect(page.locator("p.sr-legal-text")).toHaveText("Number of copies (max 10)");
    await expect(page.locator("#submit")).toHaveText("Buy");
    await expect(page.locator("form[action='/create-checkout-session'][method='POST' i]")).toBeAttached();
    await expect(page.locator("#error-message")).toHaveText("");
  });

  test("'-' disabled at 1 and '+' enabled", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#subtract")).toBeDisabled();
    await expect(page.locator("#add")).toBeEnabled();
  });

  test("'+' to 10 disables '+' and enables '-'", async ({ page }) => {
    await page.goto("/");
    for (let i = 0; i < 9; i++) await page.locator("#add").click();
    await expect(page.locator("#quantity-input")).toHaveValue("10");
    await expect(page.locator("#add")).toBeDisabled();
    await expect(page.locator("#subtract")).toBeEnabled();
    await page.locator("#subtract").click();
    await expect(page.locator("#quantity-input")).toHaveValue("9");
    await expect(page.locator("#add")).toBeEnabled();
  });

  for (const [typed, expected] of [["99", "10"], ["0", "1"], ["-5", "1"], ["5", "5"]]) {
    test(`typed quantity ${typed} clamps to ${expected}`, async ({ page }) => {
      await page.goto("/");
      const input = page.locator("#quantity-input");
      await input.fill(typed);
      await input.dispatchEvent("change");
      await input.blur();
      await expect(input).toHaveValue(expected);
    });
  }

  test("CSS and favicon load", async ({ page, request }) => {
    for (const [url, type] of [["/css/normalize.css", "css"], ["/css/global.css", "css"], ["/favicon.ico", "icon"]]) {
      const r = await request.get(url);
      expect(r.status(), url).toBe(200);
      expect(r.headers()["content-type"], url).toContain(type === "css" ? "text/css" : "image/");
    }
    await page.goto("/");
    // global.css applied: body font family is not the browser default serif
    const ff = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    expect(ff.toLowerCase()).not.toContain("times");
  });
});

test.describe("Buy flow", () => {
  test("POST /create-checkout-session returns 303 to Stripe Checkout", async ({ request }) => {
    const r = await request.post("/create-checkout-session", { form: { quantity: "3" }, maxRedirects: 0 });
    expect(r.status()).toBe(303);
    expect(r.headers()["location"]).toMatch(/^https:\/\/checkout\.stripe\.com\//);
  });

  test("clicking Buy submits quantity and is redirected to Stripe", async ({ page }) => {
    let posted = "";
    await page.route("**/create-checkout-session", async (route) => {
      posted = route.request().postData() ?? "";
      const resp = await route.fetch({ maxRedirects: 0 });
      expect(resp.status()).toBe(303);
      await route.fulfill({ status: 200, contentType: "text/html", body: "<title>redirected</title>" + resp.headers()["location"] });
    });
    await page.goto("/");
    await page.locator("#quantity-input").fill("4");
    await page.locator("#quantity-input").dispatchEvent("change");
    await Promise.all([
      page.waitForResponse("**/create-checkout-session"),
      page.locator("#submit").click(),
    ]);
    expect(posted).toContain("quantity=4");
    await expect(page.locator("body")).toContainText("https://checkout.stripe.com/");
  });
});

test.describe("Success page", () => {
  test("renders session JSON for session_id", async ({ page }) => {
    await page.goto("/success.html?session_id=cs_test_abc123");
    await expect(page.locator("h2, h1").first()).toContainText("Your payment succeeded");
    await expect(page.getByText("View CheckoutSession response:")).toBeVisible();
    const pre = page.locator("pre");
    await expect(pre).not.toBeEmpty();
    const json = JSON.parse((await pre.textContent())!);
    expect(typeof json).toBe("object");
    expect(json.id).toMatch(/^cs_test_/);
  });

  test("pre empty without session_id", async ({ page }) => {
    await page.goto("/success.html");
    await expect(page.getByText("Your payment succeeded")).toBeVisible();
    await page.waitForTimeout(1000);
    await expect(page.locator("pre")).toHaveText("");
  });

  test("Restart demo returns to /", async ({ page }) => {
    await page.goto("/success.html?session_id=cs_test_abc123");
    await page.getByRole("button", { name: "Restart demo" }).click();
    await expect(page).toHaveURL("http://localhost:4242/");
    await expect(page.locator("h1")).toHaveText("Single photo");
  });
});
