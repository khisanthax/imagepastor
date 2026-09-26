"use strict";

const DEFAULTS = {
  enabled: true,
  volume: 0.55,
  onlyWhenUnfocused: false,
  debug: false
};
const enabled = document.querySelector("#enabled");
const volume = document.querySelector("#volume");
const volumeValue = document.querySelector("#volume-value");
const onlyUnfocused = document.querySelector("#only-unfocused");
const debug = document.querySelector("#debug");
const testButton = document.querySelector("#test");
const detectorStatus = document.querySelector("#detector-status");
const detectorDetail = document.querySelector("#detector-detail");
const testResult = document.querySelector("#test-result");

async function refreshDetectorStatus() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url || !/^https:\/\/(chatgpt\.com|chat\.openai\.com)\//.test(tab.url)) {
      detectorStatus.textContent = "Select a ChatGPT tab to see its status.";
      detectorDetail.textContent = "";
      return;
    }
    const status = await chrome.tabs.sendMessage(tab.id, { type: "detector-status" });
    let label = "Ready";
    if (status.state === "GENERATING") {
      label = "Response in progress";
    } else if (status.armed) {
      label = "Waiting for response to start";
    } else if (status.state === "COMPLETE") {
      label = status.notification || "Response complete";
    }
    detectorStatus.textContent = `${label} (v${status.version})`;
    detectorDetail.textContent = `${status.state}: ${status.reason}`;
  } catch (_error) {
    detectorStatus.textContent = "Reload this ChatGPT tab to reconnect.";
    detectorDetail.textContent = "Detector is not connected.";
  }
}

refreshDetectorStatus();
setInterval(refreshDetectorStatus, 1000);

function showVolume(value) {
  volumeValue.value = `${value}%`;
}

chrome.storage.local.get(DEFAULTS).then((settings) => {
  enabled.checked = settings.enabled;
  volume.value = Math.round(settings.volume * 100);
  showVolume(volume.value);
  onlyUnfocused.checked = settings.onlyWhenUnfocused;
  debug.checked = settings.debug;
});

enabled.addEventListener("change", () => {
  chrome.storage.local.set({ enabled: enabled.checked });
});
volume.addEventListener("input", () => showVolume(volume.value));
volume.addEventListener("change", () => {
  chrome.storage.local.set({ volume: Number(volume.value) / 100 });
});
onlyUnfocused.addEventListener("change", () => {
  chrome.storage.local.set({ onlyWhenUnfocused: onlyUnfocused.checked });
});
debug.addEventListener("change", () => {
  chrome.storage.local.set({ debug: debug.checked });
});
testButton.addEventListener("click", async () => {
  testButton.disabled = true;
  testResult.hidden = false;
  testResult.textContent = "Playing...";
  try {
    const result = await chrome.runtime.sendMessage({
      type: "test-chime",
      volume: Number(volume.value) / 100
    });
    if (!result?.ok) {
      throw new Error(result?.error || "Audio player did not respond");
    }
    testResult.textContent = "Test chime played.";
  } catch (error) {
    testResult.textContent = `Playback failed: ${error.message}`;
  } finally {
    testButton.disabled = false;
  }
});
