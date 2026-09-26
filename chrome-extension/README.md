# ImagePastor ChatGPT Completion Chime

This local Chromium extension plays one short chime after a ChatGPT response stops generating. It works in Chrome and Vivaldi.

## Enable it

1. In ImagePastor's notification-area menu, click `Set Up ChatGPT Chime`.
2. On `chrome://extensions` in Chrome or Vivaldi, turn on Developer mode, click `Load unpacked`, and select the `ImagePastor-Chime` folder that ImagePastor opened. Each browser keeps a separate extension profile, so install it once in every browser where you want the chime.

Click the extension's toolbar icon to enable or disable it, change its volume, limit sounds to times when ChatGPT is unfocused, or test the sound.

After an update, reload the extension on `chrome://extensions` and then reload each ChatGPT tab. Open the popup while a ChatGPT tab is selected: its status should show `Ready` and the current version. During a response it shows `Response in progress`; after completion it shows the playback result. A disconnected tab displays a reload message. `Test chime` also reports playback failures instead of silently ignoring them.

## Detection

The content script arms only after a send, retry, regenerate, or continue action. It then checks semantic stop controls and streaming/busy attributes throughout the response, including during continuous page updates. A response must enter generating state and remain inactive for 2.5 seconds before the chime plays. This quiet period bridges short thinking and tool transitions. Manually stopping a response produces one chime after the same delay.

The state machine ignores page load, refresh, history navigation, old conversations, and unrelated mutations. ChatGPT can change its page markup; all page-specific selectors are centralized in `detectGenerationActive()` in `content.js`.

## Privacy and permissions

- Runs only on `chatgpt.com` and the legacy `chat.openai.com` host.
- Reads generation controls and state attributes, not prompts or response text.
- Stores only enabled, volume, focus-only, and development logging settings in `chrome.storage.local`.
- Keeps detector status and the last playback result in the tab's memory only. No conversation content is included in diagnostics.
- Makes no network requests and includes no analytics or telemetry.
- Uses Chrome's `offscreen` permission only to play the packaged `sounds/chime.wav` file.

Development logging is off by default. Enable it under `Development` in the popup, then inspect the ChatGPT tab's console to troubleshoot detection.

## Browser regression tests

With Node.js, Playwright, and its Chromium browser available, run:

```powershell
node --test tests/detector-core.test.js tests/chime-browser.test.js
```

Run this command from the ImagePastor project root. `NODE_PATH` can point to an existing Playwright installation, and `CHIME_TEST_CHROMIUM` can select an installed Chromium executable. The browser tests use a temporary profile and a locally intercepted ChatGPT fixture; they do not access your Chrome profile or send a prompt. They exercise native browser timers, extension injection, completion messages, actual audio playback at zero volume, the enabled setting, and the popup's test button.
