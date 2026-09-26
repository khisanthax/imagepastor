"use strict";

const chime = new Audio(chrome.runtime.getURL("sounds/chime.wav"));

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.target !== "offscreen" || message.type !== "play-chime") {
    return false;
  }
  Promise.resolve().then(async () => {
    chime.pause();
    chime.currentTime = 0;
    chime.volume = Math.max(0, Math.min(1, Number(message.volume)));
    await chime.play();
  }).then(
    () => sendResponse({ ok: true }),
    (error) => sendResponse({ ok: false, error: error.message })
  );
  return true;
});
