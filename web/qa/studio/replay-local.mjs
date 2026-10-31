// Opt-in smoke test against a disposable local demo database. Uses the supplied ZIP.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "playwright";

if (!process.env.REPLAY_ZIP || !process.env.LOCAL_DEMO_PASSWORD) throw Error("Set REPLAY_ZIP and LOCAL_DEMO_PASSWORD.");
const base = process.env.STUDIO_QA_URL || "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw Error("Local demo only.");
const out = new URL("../../../qa/runs/campaign-hq/", import.meta.url);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: process.env.STUDIO_QA_CHANNEL || "msedge", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.goto(`${base}/login?next=/mock-data`);
  await page.getByLabel("Username", { exact: true }).fill("admin");
  await page.getByLabel("Password", { exact: true }).fill(process.env.LOCAL_DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(url => url.pathname === "/mock-data");
  await page.waitForLoadState("domcontentloaded");
  const api = async path => {
    const r = await page.evaluate(async path => {
      const response = await fetch(path);
      return { status: response.status, text: await response.text() };
    }, path);
    assert.equal(r.status, 200, r.text);
    return JSON.parse(r.text);
  };
  const before = await api("/api/v1/campaign-analysis/CD002");
  const studio = await api("/api/workspace/sources");
  const uploaded = page.waitForResponse(r => r.url().endsWith("/api/v1/live/replay-archive") && r.request().method() === "POST", { timeout: 60000 });
  await page.getByLabel("Upload ZIP archive").setInputFiles(process.env.REPLAY_ZIP);
  const response = await uploaded;
  assert.equal(response.status(), 200);
  await page.getByRole("link", { name: "Mở CD002 để chạy replay →" }).waitFor();
  const manifest = await api("/api/v1/live/replay-archive");
  assert.deepEqual(await api("/api/workspace/sources"), studio);
  assert.equal((await api("/api/v1/campaign-analysis/CD002")).revision, before.revision);
  await page.screenshot({ path: new URL("replay-studio.png", out).pathname.replace(/^\/([A-Z]:)/i, "$1"), fullPage: true });
  await page.getByRole("link", { name: "Mở CD002 để chạy replay →" }).click();
  await page.waitForURL("**/campaign-hq#replay");
  await page.getByText(/Đã tải ngày 2026-11-11/).waitFor();
  const oldStatus = (await api("/api/v1/live/status")).data;
  // Never advance an already-running/user-progressed demonstration.
  if (!oldStatus.replay_running && oldStatus.replay_hour == null && oldStatus.orders_until == null) {
    const step = page.waitForResponse(r => r.url().endsWith("/api/v1/live/control") && r.request().method() === "POST", { timeout: 180000 });
    await page.getByRole("button", { name: "Phát thêm 1 giờ", exact: true }).click();
    const result = await step;
    assert.equal(result.status(), 200);
    const status = (await api("/api/v1/live/status")).data;
    assert.equal(status.data_as_of, "2026-11-11T01:00:00+07:00");
    assert.equal(status.ads_until, null);
    await page.getByRole("button", { name: "Thông báo Milo", exact: true }).click();
    await page.getByText("CD002 · Có cập nhật từ backend", { exact: true }).first().waitFor({ timeout: 180000 });
  }
  const after = await api("/api/v1/campaign-analysis/CD002");
  await page.screenshot({ path: new URL("replay-notification.png", out).pathname.replace(/^\/([A-Z]:)/i, "$1"), fullPage: true });
  assert.deepEqual(errors, []);
  const report = { manifest, before: before.dataAsOf, after: after.dataAsOf,
    studioUnchanged: true, goalUnchanged: JSON.stringify(before.goal) === JSON.stringify(after.goal), errors };
  await writeFile(new URL("replay-local.json", out), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
