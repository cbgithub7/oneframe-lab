// @ts-check
// The first page, end to end (spec 017, AC4 and AC5): the app started on an empty data root, with
// the test nodes and graphs of tests/app/fixtures/ beside the repo's, and the system's open dialog
// replaced, in the main process, by one that answers the fixture photo. Every wait is on the
// page's own state, with a deadline, and says what it waited for.
import { _electron as electron, expect, test } from "@playwright/test";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const FIXTURES = path.join(ROOT, "tests", "app", "fixtures");
const PHOTO = path.join(FIXTURES, "photo.png");

/** The app on a new, empty data root, its dialog answering the fixture photo. */
async function start() {
  const data = mkdtempSync(path.join(tmpdir(), "oneframe-e2e-"));
  // A container that runs as root cannot start Chromium's sandbox (app/main/main.js); nowhere else.
  const args = process.env.ONEFRAME_NO_SANDBOX === "1" ? ["--no-sandbox", ROOT] : [ROOT];
  const app = await electron.launch({
    args,
    cwd: ROOT,
    env: {
      ...process.env,
      ONEFRAME_DATA: data,
      ONEFRAME_NODES: path.join(FIXTURES, "nodes"),
      ONEFRAME_GRAPHS: path.join(FIXTURES, "graphs"),
    },
  });
  await app.evaluate(({ dialog }, photo) => {
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [photo] });
  }, PHOTO);
  const page = await app.firstWindow();
  await expect(page.locator("#engine-status"), "the engine said it is ready").toHaveAttribute("data-state", "ready", { timeout: 90_000 });
  const close = async () => {
    await app.close();
    rmSync(data, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  };
  return { app, page, close, data };
}

/**
 * What a failed test saw, printed into the test's output (CI keeps it in the job's log): the
 * status lines, each card's phase and words, each pane's state, and the end of the app's log,
 * which holds the engine's own lines.
 * @param {import("@playwright/test").Page} page @param {string} data
 */
async function report(page, data) {
  const quick = { timeout: 2_000 };
  /** @type {Record<string, unknown>} */
  let seen;
  try {
    seen = {
      engine: await page.locator("#engine-status").textContent(quick),
      run: await page.locator("#run-status").textContent(quick),
      cards: await Promise.all((await page.locator("article.card").all()).map(async (card) => [
        await card.getAttribute("data-step", quick), await card.getAttribute("data-phase", quick),
        await card.locator(".run").innerText(quick)])),
      panes: await Promise.all((await page.locator("section.pane").all()).map(async (pane) => [
        await pane.getAttribute("data-pane", quick), await pane.getAttribute("data-state", quick),
        await pane.locator(".caption").textContent(quick)])),
    };
  } catch (error) {
    seen = { unreadable: String(error) };
  }
  const log = path.join(data, "logs", "app.log");
  const tail = existsSync(log) ? readFileSync(log, "utf8").split(/\r?\n/).slice(-80).join("\n") : "(no app.log)";
  const text = `The page:\n${JSON.stringify(seen, null, 2)}\n\nThe end of app.log:\n${tail}`;
  console.log(text);
  await test.info().attach("what the page saw", { body: text, contentType: "text/plain" });
}

/**
 * Open a graph by its title, and pick the photo for its source.
 * @param {import("@playwright/test").Page} page @param {string} title
 */
async function openWithPhoto(page, title) {
  await page.getByRole("combobox", { name: "Graph", exact: true }).selectOption({ label: title });
  await expect(page.getByRole("heading", { name: "Photo", exact: true }), `${title} opened`).toBeVisible();
  const photo = page.locator('article[data-step="photo"]');
  await photo.getByRole("button", { name: "Pick a photo" }).click();
  await expect(photo.locator(".photo .name"), "the picked photo is named").toHaveText("photo.png");
}

test("the first page runs a graph and shows two results", async () => {
  const { page, close, data } = await start();
  try {
    await openWithPhoto(page, "Test: a relief, seen two ways");
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(page.locator('article[data-step="relief"]'), "the relief step ended done").toHaveAttribute("data-phase", "done", { timeout: 60_000 });
    const panes = page.locator("section.pane");
    await expect(panes, "the graph's two panes").toHaveCount(2);
    for (const pane of await panes.all()) {
      await expect(pane, `${await pane.getAttribute("data-pane")} drew its value`).toHaveAttribute("data-state", "drawn", { timeout: 60_000 });
      const url = String(await pane.getAttribute("data-value"));
      expect(url, "read through the value protocol").toMatch(/^oneframe-value:\/\/[0-9a-f]{64}\/[a-z_]+$/);
      const status = await page.evaluate(async (u) => (await fetch(u)).status, url);
      expect(status, `${url} is served`).toBe(200);
    }
    await expect(page.locator('section.pane[data-pane="relief.points"] .caption')).toHaveText(/^drew 3,072 of 3,072 points$/);
  } catch (error) {
    await report(page, data);
    throw error;
  } finally {
    await close();
  }
});

test("Stop ends a run and the page says so", async () => {
  const { page, close, data } = await start();
  try {
    await openWithPhoto(page, "Test: a node that waits");
    const card = page.locator('article[data-step="wait"]');
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(card, "the waiting node started").toHaveAttribute("data-phase", "running");
    await page.getByRole("button", { name: "Stop", exact: true }).click();
    await expect(card, "its card says stopped within 5 s").toHaveAttribute("data-phase", "stopped", { timeout: 5_000 });
    await expect(card.locator(".phase")).toHaveText("Stopped");
    await expect(page.locator("#run-status")).toHaveText("The run was stopped.");
  } catch (error) {
    await report(page, data);
    throw error;
  } finally {
    await close();
  }
});

test("a pane shows a collection one item at a time", async () => {
  // Spec 021, AC8: a node that makes three images; its card says three items, its pane shows the
  // first, read by that item's own key, and its Next, pressed from the keyboard, shows the second.
  const { page, close, data } = await start();
  try {
    await page.getByRole("combobox", { name: "Graph", exact: true }).selectOption({ label: "Test: three images, one at a time" });
    const card = page.locator('article[data-step="three"]');
    await expect(card, "the graph opened").toBeVisible();
    await page.getByRole("button", { name: "Run", exact: true }).click();
    await expect(card, "the step ended done").toHaveAttribute("data-phase", "done", { timeout: 60_000 });
    await expect(card.locator(".items"), "its card counts its items").toHaveText("3 items");
    const pane = page.locator('section.pane[data-pane="three.images"]');
    await expect(pane, "the pane drew its first item").toHaveAttribute("data-state", "drawn", { timeout: 60_000 });
    await expect(pane.locator("nav.items .item")).toHaveText("Item 1 of 3");
    await expect(pane.locator(".caption")).toHaveText("1 x 1 px");
    const first = String(await pane.getAttribute("data-value"));
    expect(first, "read by the item's own key").toMatch(/^oneframe-value:\/\/[0-9a-f]{64}\/images%3A0$/);
    expect(await page.evaluate(async (u) => (await fetch(u)).status, first), `${first} is served`).toBe(200);

    const next = pane.getByRole("button", { name: "Next item of Three: images" });
    await next.focus();
    await page.keyboard.press("Enter");
    await expect(pane.locator("nav.items .item"), "Next showed the second").toHaveText("Item 2 of 3");
    await expect(pane, "the second item drawn").toHaveAttribute("data-value", /\/images%3A1$/, { timeout: 30_000 });
    await expect(pane.locator(".caption")).toHaveText("2 x 2 px");
    await expect(next, "Next keeps the keyboard's focus").toBeFocused();
    await expect(pane.getByRole("button", { name: "Previous item of Three: images" })).toBeEnabled();
  } catch (error) {
    await report(page, data);
    throw error;
  } finally {
    await close();
  }
});

test("Run to here runs up to a card", async () => {
  // Spec 022, AC5: Run to here, pressed from the keyboard on the card in the middle of a line of
  // three, ends with that card done and the card after it never started.
  const { page, close, data } = await start();
  try {
    await openWithPhoto(page, "Test: three steps in a line");
    const middle = page.locator('article[data-step="hand"]');
    const after = page.locator('article[data-step="relief"]');
    const here = middle.getByRole("button", { name: "Run to here: Hand on", exact: true });
    await here.focus();
    await page.keyboard.press("Enter");
    await expect(middle, "the middle card ended done").toHaveAttribute("data-phase", "done", { timeout: 60_000 });
    await expect(page.locator("#run-status"), "the run ended").toHaveText("The run is done.", { timeout: 30_000 });
    await expect(page.locator('article[data-step="photo"]'), "what it depends on ran").toHaveAttribute("data-phase", "done");
    await expect(after, "the card after it never started").toHaveAttribute("data-phase", "idle");
    await expect(after.locator(".run"), "and says nothing of a run").toHaveText("");
  } catch (error) {
    await report(page, data);
    throw error;
  } finally {
    await close();
  }
});
