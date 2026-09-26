"use strict";

const DEFAULTS = Object.freeze({
  enabled: true,
  volume: 0.55,
  onlyWhenUnfocused: false,
  debug: false
});
const OFFSCREEN_PATH = "offscreen.html";
let creatingOffscreen = null;

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.local.get(DEFAULTS);
  await chrome.storage.local.set(current);
});

async function offscreenDocumentExists() {
  const offscreenUrl = chrome.runtime.getURL(OFFSCREEN_PATH);
  if (chrome.runtime.getContexts) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
      documentUrls: [offscreenUrl]
    });
    return contexts.length > 0;
  }
  const extensionClients = await clients.matchAll();
  return extensionClients.some((client) => client.url === offscreenUrl);
}

async function ensureOffscreenDocument() {
  if (await offscreenDocumentExists()) {
    return;
  }
  if (!creatingOffscreen) {
    creatingOffscreen = chrome.offscreen.createDocument({
      url: OFFSCREEN_PATH,
      reasons: ["AUDIO_PLAYBACK"],
      justification: "Play the local ChatGPT response completion chime"
    }).catch((error) => {
      if (!/single offscreen document|already exists/i.test(error.message)) {
        throw error;
      }
    }).finally(() => {
      creatingOffscreen = null;
    });
  }
  await creatingOffscreen;
}

async function chatTabIsFocused(sender) {
  if (!sender.tab || !sender.tab.active) {
    return false;
  }
  try {
    const window = await chrome.windows.get(sender.tab.windowId);
    return Boolean(window.focused);
  } catch (_error) {
    return false;
  }
}

async function playChime(volume) {
  await ensureOffscreenDocument();
  const result = await chrome.runtime.sendMessage({
    target: "offscreen",
    type: "play-chime",
    volume: Math.max(0, Math.min(1, Number(volume)))
  });
  if (!result?.ok) {
    throw new Error(result?.error || "Audio player did not respond");
  }
  return { played: true };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  let task;
  if (message.type === "generation-complete") {
    task = chrome.storage.local.get(DEFAULTS).then(async (settings) => {
      if (!settings.enabled) {
        return { played: false, reason: "Chime disabled" };
      }
      if (settings.onlyWhenUnfocused && await chatTabIsFocused(sender)) {
        return { played: false, reason: "Skipped while ChatGPT is focused" };
      }
      return playChime(settings.volume);
    });
  } else if (message.type === "test-chime") {
    task = playChime(message.volume ?? DEFAULTS.volume);
  } else {
    return false;
  }

  task.then(
    (result) => sendResponse({ ok: true, ...result }),
    (error) => sendResponse({ ok: false, error: error.message })
  );
  return true;
});
