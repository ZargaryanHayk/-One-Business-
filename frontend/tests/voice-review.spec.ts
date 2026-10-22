import { expect, test } from "@playwright/test";

test("Edit opens a prefilled text-only editor and sends a correction for approval", async ({
  page,
}, testInfo) => {
  const requests: unknown[] = [];
  await page.route("**/api/voice-review", async (route) => {
    if (route.request().method() === "POST") {
      requests.push(route.request().postDataJSON());
      await route.fulfill({
        json: { revision: 1, message: "Sent to Telegram" },
      });
    } else {
      await route.fulfill({
        json: {
          text: "The transcribed project name",
          label: "project name",
          revision: 0,
          max_length: 120,
        },
      });
    }
  });
  await page.goto(`/voice-review#review=${"a".repeat(43)}`);
  const input = page.getByLabel("project name", { exact: true });
  await expect(input).toHaveValue("The transcribed project name");
  await expect(
    page.locator('input[type="file"], input[type="audio"]'),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /record|microphone/i }),
  ).toHaveCount(0);
  await input.fill("The corrected project name");
  await page.screenshot({
    path: `/tmp/taskline-voice-editor-${testInfo.project.name}.png`,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Send correction to Telegram" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Correction sent" }),
  ).toBeVisible();
  await expect(page.getByRole("status")).toContainText("Approve");
  expect(requests).toEqual([
    { text: "The corrected project name", revision: 0 },
  ]);
});

test("Expired editor links show a clear message without an editable draft", async ({
  page,
}) => {
  await page.route("**/api/voice-review", (route) =>
    route.fulfill({
      status: 410,
      json: { error: { message: "This editor expired. Return to Telegram." } },
    }),
  );
  await page.goto(`/voice-review#review=${"a".repeat(43)}`);
  await expect(page.getByRole("alert")).toHaveText(
    "This editor expired. Return to Telegram.",
  );
  await expect(page.locator("textarea")).toHaveCount(0);
});

test("A failed save keeps the user's typed correction", async ({ page }) => {
  await page.route("**/api/voice-review", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 503,
          json: { error: { message: "Please try again." } },
        })
      : route.fulfill({
          json: {
            text: "Original",
            label: "task description",
            revision: 0,
            max_length: 10000,
          },
        }),
  );
  await page.goto(`/voice-review#review=${"a".repeat(43)}`);
  await page
    .getByLabel("task description", { exact: true })
    .fill("My correction");
  await page
    .getByRole("button", { name: "Send correction to Telegram" })
    .click();
  await expect(page.getByRole("alert")).toHaveText("Please try again.");
  await expect(
    page.getByLabel("task description", { exact: true }),
  ).toHaveValue("My correction");
});
