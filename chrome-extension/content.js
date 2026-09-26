(function () {
  "use strict";

  const {
    CoalescingScheduler,
    GenerationStateMachine
  } = globalThis.ImagePastorDetectorCore;
  const GENERATION_SELECTORS = [
    '[data-testid="stop-button"]',
    'button[aria-label="Stop generating"]',
    'button[aria-label="Stop streaming"]',
    'button[aria-label="Stop response"]',
    'main [data-message-author-role="assistant"][data-is-streaming="true"]',
    'main [data-message-author-role="assistant"][aria-busy="true"]'
  ];
  const STOP_LABEL = /^stop (generating|streaming|response|thinking|researching)\b/i;
  const START_ACTION = /^(send|regenerate|retry|try again|continue generating|continue response)\b/i;
  const COMPOSER_SELECTOR = 'textarea, [contenteditable="true"]';
  let debugEnabled = false;
  let lastUrl = location.href;
  let lastStateReason = "detector loaded";
  let lastNotification = "";

  function debug(...values) {
    if (debugEnabled) {
      console.debug("[ImagePastor Chime]", ...values);
    }
  }

  function isVisible(element) {
    return Boolean(
      element &&
      element.isConnected &&
      !element.hidden &&
      element.getAttribute("aria-hidden") !== "true" &&
      element.getClientRects().length
    );
  }

  // Keep all ChatGPT-specific DOM knowledge here so UI changes are easy to repair.
  function detectGenerationActive() {
    for (const selector of GENERATION_SELECTORS) {
      const element = document.querySelector(selector);
      if (isVisible(element)) {
        return true;
      }
    }

    for (const control of document.querySelectorAll('main button, main [role="button"]')) {
      const label = control.getAttribute("aria-label") ||
        control.getAttribute("title") ||
        control.getAttribute("data-testid") || "";
      if (STOP_LABEL.test(label.trim()) && isVisible(control)) {
        return true;
      }
    }
    return false;
  }

  const machine = new GenerationStateMachine({
    quietMs: 2500,
    intentTimeoutMs: 30000,
    onComplete: () => {
      lastNotification = "Sending chime";
      Promise.resolve().then(() => chrome.runtime.sendMessage({
        type: "generation-complete"
      })).then((result) => {
        if (!result?.ok) {
          throw new Error(result?.error || "Chime service did not respond");
        }
        lastNotification = result.played ? "Chime played" : result.reason;
        debug(lastNotification);
      }).catch((error) => {
        lastNotification = `Chime failed: ${error.message}`;
        console.warn("[ImagePastor Chime]", lastNotification);
      });
    },
    onStateChange: (state, reason) => {
      lastStateReason = reason;
      debug(state, reason);
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message.type !== "detector-status") {
      return false;
    }
    sendResponse({
      version: chrome.runtime.getManifest().version,
      state: machine.state,
      armed: machine.armed,
      reason: lastStateReason,
      notification: lastNotification
    });
    return false;
  });

  function arm(reason) {
    lastNotification = "";
    machine.arm(reason);
    scheduleEvaluation(0);
  }

  function controlLabel(control) {
    return (
      control.getAttribute("aria-label") ||
      control.getAttribute("title") ||
      control.getAttribute("data-testid") ||
      control.textContent ||
      ""
    ).trim();
  }

  function isStartControl(control) {
    const testId = control.getAttribute("data-testid") || "";
    return testId === "send-button" ||
      testId.includes("regenerate") ||
      START_ACTION.test(controlLabel(control));
  }

  document.addEventListener("click", (event) => {
    const control = event.target instanceof Element
      ? event.target.closest('button, [role="button"]')
      : null;
    if (control && isStartControl(control)) {
      arm("button");
    }
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.defaultPrevented) {
      return;
    }
    const target = event.target;
    if (target instanceof Element && target.closest(COMPOSER_SELECTOR)) {
      arm("composer Enter");
    }
  }, true);

  document.addEventListener("submit", (event) => {
    if (event.target instanceof Element && event.target.querySelector(COMPOSER_SELECTOR)) {
      arm("composer submit");
    }
  }, true);

  function evaluate() {
    if (location.href !== lastUrl) {
      const isInitialConversationRoute = machine.isPending() &&
        Date.now() - machine.lastIntentAt < 10000;
      if (!isInitialConversationRoute) {
        machine.reset("navigation");
      }
      lastUrl = location.href;
    }
    machine.observeGeneration(detectGenerationActive());
    if (machine.isPending()) {
      evaluationScheduler.schedule(200);
    }
  }

  const evaluationScheduler = new CoalescingScheduler(evaluate);

  function scheduleEvaluation(delay = 120) {
    evaluationScheduler.schedule(delay);
  }

  const observer = new MutationObserver(() => scheduleEvaluation());
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["aria-busy", "aria-hidden", "aria-label", "data-is-streaming", "data-testid"]
  });

  addEventListener("pageshow", (event) => {
    if (event.persisted) {
      machine.reset("page restored");
      lastUrl = location.href;
    }
  });
  addEventListener("popstate", () => {
    machine.reset("history navigation");
    lastUrl = location.href;
  });

  chrome.storage.local.get({ debug: false }).then((settings) => {
    debugEnabled = Boolean(settings.debug);
    debug("detector loaded");
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.debug) {
      debugEnabled = Boolean(changes.debug.newValue);
    }
  });

  evaluate();
})();
