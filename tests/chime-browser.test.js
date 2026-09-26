"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { chromium } = require("playwright");

const extensionPath = path.resolve(__dirname, "../chrome-extension");
const browserOptions = {
  headless: true,
  channel: "chromium",
  executablePath: process.env.CHIME_TEST_CHROMIUM || undefined
};

test("detector uses real browser timers for scheduling, completion, and cancellation", async () => {
  const browser = await chromium.launch(browserOptions);
  try {
    const page = await browser.newPage();
    await page.addScriptTag({ path: path.join(extensionPath, "detector-core.js") });
    const result = await page.evaluate(async () => {
      const { CoalescingScheduler, GenerationStateMachine } = ImagePastorDetectorCore;
      let completed = 0;
      let scheduled = 0;
      const machine = new GenerationStateMachine({ quietMs: 30, onComplete: () => completed++ });
      const scheduler = new CoalescingScheduler(() => scheduled++);
      scheduler.schedule(0);
      const cancelledScheduler = new CoalescingScheduler(() => scheduled++);
      cancelledScheduler.schedule(10);
      cancelledScheduler.cancel();
      machine.arm("browser test");
      machine.observeGeneration(true);
      machine.observeGeneration(false);
      machine.observeGeneration(true);
      machine.observeGeneration(false);
      await new Promise(resolve => setTimeout(resolve, 100));
      machine.arm("cancelled intent");
      machine.reset();
      return { completed, scheduled, state: machine.state };
    });
    assert.deepEqual(result, { completed: 1, scheduled: 1, state: "IDLE" });
  } finally {
    await browser.close();
  }
});

test("Chrome extension detects a response and receives real audio playback confirmation", async () => {
  const context = await chromium.launchPersistentContext("", {
    ...browserOptions,
    args: [`--disable-extensions-except=${extensionPath}`, `--load-extension=${extensionPath}`]
  });
  try {
    // A local fixture exercises host-scoped content injection without contacting ChatGPT.
    await context.route("https://chatgpt.com/**", route => route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body><main>
        <div data-message-author-role="assistant">Existing response</div>
        <form><textarea aria-label="Chat with ChatGPT"></textarea>
          <button type="submit" data-testid="send-button" aria-label="Send prompt">Send</button>
        </form>
        <button id="finish" type="button">Finish fixture</button>
        <output id="activity"></output>
      </main><script>
        let ticker;
        document.querySelector('form').addEventListener('submit', event => {
          event.preventDefault();
          const stop = document.createElement('button');
          stop.dataset.testid = 'stop-button';
          stop.setAttribute('aria-label', 'Stop generating');
          document.querySelector('main').append(stop);
          ticker = setInterval(() => {
            document.querySelector('#activity').textContent = String(Date.now());
          }, 20);
        });
        document.querySelector('#finish').addEventListener('click', () => {
          clearInterval(ticker);
          document.querySelector('[data-testid="stop-button"]').remove();
        });
      </script></body></html>`
    }));
    const worker = context.serviceWorkers()[0] ||
      await context.waitForEvent("serviceworker", { timeout: 10000 });
    await assertEventually(() => worker.evaluate(async () => {
      const settings = await chrome.storage.local.get(null);
      return Object.hasOwn(settings, "enabled");
    }));
    await worker.evaluate(async () => {
      await chrome.storage.local.set({ enabled: true, volume: 0, onlyWhenUnfocused: false });
      globalThis.testCompletionCount = 0;
      chrome.runtime.onMessage.addListener(message => {
        if (message.type === "generation-complete") {
          globalThis.testCompletionCount++;
        }
        return false;
      });
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto("https://chatgpt.com/imagepastor-browser-test");

    async function status() {
      return worker.evaluate(async () => {
        const [tab] = await chrome.tabs.query({ url: "https://chatgpt.com/imagepastor-browser-test" });
        return chrome.tabs.sendMessage(tab.id, { type: "detector-status" });
      });
    }

    await assertEventually(async () => (await status()).state === "IDLE");
    assert.equal(await worker.evaluate(() => testCompletionCount), 0);
    await page.getByRole("button", { name: "Send prompt", exact: true }).click();
    await assertEventually(async () => (await status()).state === "GENERATING");
    await page.getByRole("button", { name: "Finish fixture", exact: true }).click();
    await assertEventually(async () => (await status()).notification === "Chime played");
    assert.equal(await worker.evaluate(() => testCompletionCount), 1);

    // Exercise a second response after the first completion and the disabled setting.
    await worker.evaluate(() => chrome.storage.local.set({ enabled: false }));
    await page.getByRole("button", { name: "Send prompt", exact: true }).click();
    await assertEventually(async () => (await status()).state === "GENERATING");
    await page.getByRole("button", { name: "Finish fixture", exact: true }).click();
    await assertEventually(async () => (await status()).notification === "Chime disabled");
    assert.equal(await worker.evaluate(() => testCompletionCount), 2);
    assert.deepEqual(errors, []);

    const extensionId = new URL(worker.url()).host;
    const popup = await context.newPage();
    await popup.goto(`chrome-extension://${extensionId}/popup.html`);
    await assertEventually(async () => (await popup.locator("#volume").inputValue()) === "0");
    await popup.getByRole("button", { name: "Test chime", exact: true }).click();
    await popup.getByText("Test chime played.", { exact: true }).waitFor();
    await worker.evaluate(async () => {
      const [tab] = await chrome.tabs.query({ url: "https://chatgpt.com/imagepastor-browser-test" });
      await chrome.tabs.update(tab.id, { active: true });
    });
    await popup.getByText("Chime disabled (v1.0.2)", { exact: true }).waitFor();
    assert.equal(await popup.locator("body").evaluate(e => e.scrollWidth <= e.clientWidth), true);
    if (process.env.CHIME_TEST_SCREENSHOT) {
      await popup.setViewportSize({ width: 300, height: 450 });
      await popup.screenshot({ path: process.env.CHIME_TEST_SCREENSHOT });
    }
  } finally {
    await context.close();
  }
});

async function assertEventually(predicate, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await predicate()) {
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.fail("Expected browser state did not appear before timeout");
}
