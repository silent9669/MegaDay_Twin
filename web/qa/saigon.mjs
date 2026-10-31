/* global document, innerWidth, requestAnimationFrame, getComputedStyle */
import { chromium } from "playwright";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";
const base = process.env.QA_BASE_URL || "http://127.0.0.1:5179";
assert(
  new URL(base).hostname === "127.0.0.1" ||
    new URL(base).hostname === "localhost",
  "This QA script mutates local fixtures only.",
);
const password = await readFile(
  process.env.QA_PASSWORD_FILE || "/tmp/megaday-qa-password",
  "utf8",
);
const out = new URL("../../qa/runs/saigon-city-milo/", import.meta.url)
  .pathname;
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: process.env.QA_HEADED !== "1" });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 1,
});
const page = await context.newPage();
const errors = [];
const calls = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});
page.on("request", (r) => {
  if (r.url().endsWith("/api/pm/chat")) calls.push(r);
});
const results = { sizes: [], errors, performance: {}, monitor: {} };
try {
  await page.goto(base + "/login");
  await page.screenshot({ path: out + "login-1440.png" });
  await page.getByLabel("Username", { exact: true }).fill("admin");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL("**/city-demo");
  await page.getByRole("button", { name: "Close tutorial" }).click();
  await page.getByRole("button", { name: "Close workspace" }).click();
  await page.getByRole("link", { name: "View city", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector(".city-canvas")?.dataset.assets === "ready",
  );
  await page.screenshot({ path: out + "city-1440.png" });
  const originalAppearance = await page.getByRole("button", { name: "Night mode", exact: true }).getAttribute("aria-pressed");
  const setNight = async (night) => {
    const toggle = page.getByRole("button", { name: "Night mode", exact: true });
    if ((await toggle.getAttribute("aria-pressed") === "true") !== night) await toggle.click();
    await page.waitForFunction(value => document.querySelector(".city-canvas")?.dataset.appearance === value, night ? "night" : "morning");
    await page.waitForFunction(() => !document.querySelector('[aria-label="Night mode"]').disabled);
  };
  const clockBefore = await page.evaluate(async () => (await (await fetch("/api/workspace/sandbox")).json()).revision);
  const originalCanvas = await page.locator(".city-canvas canvas").elementHandle();
  await setNight(true);
  assert(await originalCanvas.evaluate(el => el.isConnected), "Appearance recreated the canvas");
  assert.equal(await page.evaluate(async () => (await (await fetch("/api/workspace/sandbox")).json()).revision), clockBefore, "Appearance changed the source clock");
  await page.reload();
  await page.waitForFunction(() => document.querySelector(".city-canvas")?.dataset.assets === "ready" && document.querySelector(".city-canvas")?.dataset.appearance === "night");
  assert.equal(await page.getByRole("button", { name: "Night mode", exact: true }).getAttribute("aria-pressed"), "true", "Night preference lost after reload");
  await page.screenshot({ path: out + "city-night-1440.png" });
  await page.getByRole("button", { name: "Visit Landmark 81", exact: true }).click();
  await page.waitForTimeout(2200);
  assert.equal(await page.getByRole("button", { name: "Visit Landmark 81", exact: true }).getAttribute("aria-current"), "true");
  await page.screenshot({ path: out + "landmark81-night.png" });
  await page.getByRole("link", { name: "View city", exact: true }).click();
  await page.waitForTimeout(1800);
  await page.getByRole("button", { name: "Visit MegaDay Twin", exact: true }).click();
  await page.getByRole("dialog", { name: "MegaDay HQ", exact: true }).waitFor();
  await page.getByRole("button", { name: "Close workspace" }).click();
  await page.screenshot({ path: out + "hq-night.png" });
  await page.getByRole("link", { name: "View city", exact: true }).click();
  results.appearance = { persisted: true, panels: [] };

  for (const [width, height] of [
    [1440, 900],
    [1024, 768],
    [768, 1024],
    [390, 844],
    [844, 390],
  ]) {
    await page.setViewportSize({ width, height });
    await page.waitForTimeout(200);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      `Overflow at ${width}`,
    );
    await page.getByRole("button", { name: "Warehouse", exact: true }).click();

    await page.getByRole("dialog", { name: "Warehouse" }).waitFor();
    const box = await page
      .getByRole("dialog", { name: "Warehouse" })
      .boundingBox();
    assert(
      box.x >= 0 &&
        box.x + box.width <= width &&
        box.y >= 0 &&
        box.y + box.height <= height,
      `Inspector outside ${width}`,
    );
    await page.screenshot({ path: out + `warehouse-${width}.png` });
    assert.equal(await page.locator(".district-workspace").evaluate(el => getComputedStyle(el).colorScheme), "dark");
    await page.getByText("Label settings", { exact: true }).click();
    await page.getByLabel("Market", { exact: true }).waitFor();
    assert.deepEqual(await page.getByLabel("Market", { exact: true }).evaluate(el => ({
      color: getComputedStyle(el).color,
      background: getComputedStyle(el).backgroundColor,
    })), { color: "rgb(224, 233, 226)", background: "rgb(24, 42, 43)" });
    await page.getByText(/^Evidence \(/).click();
    await page.getByLabel("Evidence source", { exact: true }).fill("Local QA observation");
    assert(await page.getByRole("button", { name: "Add evidence", exact: true }).isVisible());
    await page.screenshot({ path: out + `warehouse-evidence-${width}.png` });
    results.appearance.panels.push({ width, scheme: "dark" });

    await page.getByRole("button", { name: "Close workspace" }).click();
    await page
      .getByRole("button", { name: "Open PM assistant", exact: true })
      .first()
      .click();
    await page
      .getByRole("textbox", { name: "Message Milo" })
      .fill("Keep my draft");
    const composer = await page
      .getByRole("textbox", { name: "Message Milo" })
      .boundingBox();
    assert(
      composer.width > 120 &&
        composer.x >= 0 &&
        composer.x + composer.width <= width,
      `Composer not visible at ${width}`,
    );
    if (width < 900) {
      await page.keyboard.press("Shift+Tab");
      assert(
        await page.evaluate(
          () => !!document.activeElement.closest('[role="dialog"]'),
        ),
        "Focus escaped mobile sheet",
      );
    }
    await page.screenshot({ path: out + `milo-${width}.png` });
    await page.getByRole("button", { name: "Close chat" }).click();
    await page
      .getByRole("button", { name: "Open PM assistant", exact: true })
      .first()
      .click();
    assert.equal(
      await page.getByRole("textbox", { name: "Message Milo" }).inputValue(),
      "Keep my draft",
    );
    await page.getByRole("button", { name: "Close chat" }).click();
    await page.getByRole("link", { name: "View city", exact: true }).click();
    results.sizes.push({
      width,
      height,
      overflow: false,
      panelsInside: true,
      draftRetained: true,
    });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: "Replay tutorial" }).click();
  for (const [index, title] of ["Start at HQ", "Choose ad platforms", "Check the warehouse"].entries()) {
    await page.getByRole("heading", { name: title, exact: true }).waitFor();
    await page.getByRole("region", { name: "City tutorial" }).getByRole("button", { name: index === 2 ? "Done" : "Next", exact: true }).click();
  }
  assert.equal(await page.evaluate(() => localStorage.getItem("megaday.city.tour.v4")), "complete");
  await page.getByRole("button", { name: "Close workspace" }).click();
  await page.getByRole("link", { name: "View city", exact: true }).click();
  await page.waitForTimeout(1500);
  results.performance = await page.evaluate(async () => {
    const samples = [];
    let previous = performance.now();
    for (let i = 0; i < 180; i++) {
      await new Promise(requestAnimationFrame);
      const now = performance.now();
      samples.push(now - previous);
      previous = now;
    }
    const sorted = samples.slice(10).sort((a, b) => a - b);
    return {
      ...document.querySelector(".city-canvas").dataset,
      fps: Math.round(
        1000 / (samples.slice(10).reduce((a, b) => a + b, 0) / 170),
      ),
      p95FrameMs: sorted[Math.floor(sorted.length * 0.95)],
    };
  });
  assert(Number(results.performance.drawCalls) <= 200, "Draw call budget");
  assert(Number(results.performance.triangles) <= 300000, "Triangle budget");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForTimeout(800);
  const canvas = page.locator(".city-canvas");
  const a = await canvas.screenshot();
  await page.waitForTimeout(400);
  const b = await canvas.screenshot();
  results.reducedMotionStatic =
    createHash("sha256").update(a).digest("hex") ===
    createHash("sha256").update(b).digest("hex");
  if (!results.reducedMotionStatic) {
    await writeFile(out + "reduced-motion-before.png", a);
    await writeFile(out + "reduced-motion-after.png", b);
  }
  assert(results.reducedMotionStatic, "Reduced motion still animates");
  await page.screenshot({ path: out + "city-reduced-motion.png" });
  await setNight(false);
  await page.screenshot({ path: out + "city-morning-1440.png" });
  const morning = await canvas.screenshot();
  await page.waitForTimeout(400);
  assert.equal(createHash("sha256").update(morning).digest("hex"), createHash("sha256").update(await canvas.screenshot()).digest("hex"), "Morning animates under reduced motion");
  results.appearance.reducedMotionBothModes = true;
  await setNight(originalAppearance === "true");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const second = await context.newPage();
  await second.goto(base + "/mock-data");
  await page.waitForTimeout(1300);
  const original = await second.evaluate(
    async () => await (await fetch("/api/workspace/sources")).json(),
  );
  const saveInventory = async (stock) =>
    second.evaluate(
      async ({ stock }) => {
        const d = await (await fetch("/api/workspace/sources")).json();
        const text = `Mã nội bộ,Tồn thực tế,Tên hàng,Thời điểm cập nhật\nQA-SKU,${stock},QA kettle,2026-11-11 00:00\n`;
        const hash = Array.from(
          new Uint8Array(
            await crypto.subtle.digest(
              "SHA-256",
              new TextEncoder().encode(text),
            ),
          ),
        )
          .map((b) => b.toString(16).padStart(2, "0"))
          .join("");
        const data = {
          ...d.data,
          files: d.data.files
            .filter((f) => f.kind !== "inventory")
            .concat({
              kind: "inventory",
              name: "inventory.csv",
              origin: "upload",
              hash,
              text,
            }),
        };
        const r = await fetch("/api/workspace/sources", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ revision: d.revision, data }),
        });
        return r.status;
      },
      { stock },
    );
  try {
    assert.equal(await saveInventory(10), 200);
    await page.waitForTimeout(1300);
    assert.equal(await saveInventory(0), 200);
    await page
      .getByRole("complementary", { name: "Workspace update" })
      .getByText("Stock reached zero")
      .waitFor();
    assert.equal(
      await page.getByRole("dialog").count(),
      0,
      "Update opened chat",
    );
    assert.equal(calls.length, 0, "Update called AI");
    await page.getByRole("complementary", { name: "Workspace update" }).getByRole("button", { name: "Read all", exact: true }).click();
    await page.getByRole("complementary", { name: "Workspace update" }).waitFor({ state: "hidden" });
    assert.equal(await page.locator(".pm-unread").count(), 0);
    assert.equal(await saveInventory(8), 200);
    await page.waitForTimeout(1300);
    assert.equal(await saveInventory(0), 200);
    await page.getByRole("complementary", { name: "Workspace update" }).getByText("Stock reached zero").waitFor();
    assert.equal(await page.locator('.pm-companion[data-speaking="true"]').count(), 1);
    const bubble = await page.getByRole("complementary", { name: "Workspace update" }).boundingBox();
    assert(bubble.x >= 0 && bubble.y >= 0 && bubble.x + bubble.width <= 1440 && bubble.y + bubble.height <= 900, "Milo speech bubble outside viewport");
    await page.screenshot({ path: out + "milo-notification.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    const mobileBubble = await page.getByRole("complementary", { name: "Workspace update" }).boundingBox();
    assert(mobileBubble.x >= 0 && mobileBubble.y >= 0 && mobileBubble.x + mobileBubble.width <= 390 && mobileBubble.y + mobileBubble.height <= 844, "Milo speech bubble outside mobile viewport");
    await page.screenshot({ path: out + "milo-notification-mobile.png" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page
      .getByRole("complementary", { name: "Workspace update" })
      .getByRole("button", { name: "Review update" })
      .click();
    await page
      .getByRole("region", { name: "Workspace updates" })
      .getByText("Stock reached zero")
      .waitFor();
    const notice = page
      .locator(".milo-updates article")
      .filter({ hasText: "Stock reached zero" });
    await notice.getByRole("button", { name: "Explain", exact: true }).click();
    assert.equal(
      await page.getByRole("textbox", { name: "Message Milo" }).inputValue(),
      "Keep my draft",
    );
    await page.getByText("Workspace change attached").waitFor();
    assert.equal(calls.length, 0);
    await page.screenshot({ path: out + "milo-explain.png" });
    await page.getByRole("tab", { name: /Updates/ }).click();
    await notice.getByRole("link", { name: "View source" }).click();
    await page.waitForURL("**/*stock=QA-SKU");
    await page.getByRole("dialog", { name: "Warehouse" }).waitFor();
    assert.equal(
      await page.locator(".stock-highlight code").innerText(),
      "QA-SKU",
    );
    assert.equal(await page.getByLabel("Search products", { exact: true }).inputValue(), "QA-SKU");
    results.monitor = {
      crossTab: true,
      quiet: true,
      automaticAICalls: calls.length,
      explainDraft: true,
      sourceNavigation: true,
    };
  } finally {
    await second.evaluate(async (original) => {
      const current = await (await fetch("/api/workspace/sources")).json();
      await fetch("/api/workspace/sources", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: current.revision,
          data: original.data,
        }),
      });
    }, original);
    await second.close();
  }
  await page.getByRole("button", { name: "Close workspace" }).click();
  await page.getByRole("link", { name: "Data Studio", exact: true }).click();
  await page
    .getByRole("button", { name: "Open PM assistant", exact: true })
    .first()
    .click();
  await page.getByRole("tab", { name: /Updates/ }).click();
  assert(
    (await page.locator(".milo-updates article").count()) > 0,
    "Updates lost during navigation",
  );
  await page.getByRole("button", { name: "Read all", exact: true }).click();
  assert.equal(await page.locator(".milo-updates article").count(), 0);
  await page.getByRole("heading", { name: "No changes yet" }).waitFor();
  await page.getByRole("button", { name: "Close chat" }).click();
  assert.equal(await page.getByRole("complementary", { name: "Workspace update" }).count(), 0);
  results.monitor.readAll = true;
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await page.waitForURL("**/login");
  for (const [width, height] of [
    [390, 844],
    [768, 1024],
    [1024, 768],
  ]) {
    await page.setViewportSize({ width, height });
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({ path: out + `login-${width}.png` });
  }
  assert.equal(errors.length, 0, errors.join("\n"));
  results.passed = true;
} catch (error) {
  results.failure = error.message;
  await page.screenshot({ path: out + "failure.png" });
  throw error;
} finally {
  await writeFile(out + "results.json", JSON.stringify(results, null, 2));
  await browser.close();
  console.log(JSON.stringify(results, null, 2));
}
