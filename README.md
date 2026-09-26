# ImagePastor

A small Windows hotkey utility for quickly sending a screenshot of the window under your mouse to ChatGPT in a browser.

Default capture hotkey: `Ctrl+Alt+V`

Default target hotkey: `Ctrl+Alt+Shift+V`

Default monitor sleep guard hotkey: `Ctrl+Alt+Shift+M`

ImagePastor runs in the Windows notification area. Right-click its icon to toggle `Enabled`, set the current window as target, keep monitors asleep, set up the ChatGPT completion chime, enable `Start with Windows`, or exit.

First, activate the ChatGPT tab in Chrome or Vivaldi and press `Ctrl+Alt+Shift+V` once. This saves that browser window as the paste target, even if the tab title is the conversation topic.

When the capture hotkey fires, ImagePastor:

1. Finds the top-level window under your mouse.
2. Captures that window's visible bounds.
3. Places the screenshot on the Windows clipboard as an image.
4. Finds a Chrome or Vivaldi tab whose address belongs to `chatgpt.com`, even when the tab title is a conversation topic.
5. Verifies the window has focus and the active address is still ChatGPT before clicking the composer and sending `Ctrl+V`.

## Run

```powershell
.\run.ps1
```

Keep the PowerShell window open while using the hotkey. Press `Ctrl+C` in that window to quit.

## Build an EXE

```powershell
.\build.ps1
```

The executable is written to `dist\ImagePastor.exe`. The local ChatGPT extension and Codex notifier are copied to `dist\ImagePastor-Chime` and `dist\ImagePastor-Codex`. Put a `config.json` next to the EXE if you want to override settings after packaging.

## ChatGPT completion chime

The bundled Manifest V3 extension plays one local chime when a response finishes. It does not read or store conversation text, make network requests, or use an OpenAI account or API key.

1. Right-click ImagePastor in the notification area and choose `Set Up ChatGPT Chime`.
2. On the browser's extensions page, enable Developer mode, choose `Load unpacked`, and select the folder ImagePastor opened. ImagePastor prefers Vivaldi for this setup when it is installed.

Use the extension toolbar popup to enable or disable the chime, set its volume, optionally chime only when ChatGPT is not focused, test the sound, or enable development logging. Detailed detection and privacy notes are in `chrome-extension\README.md`.

## Codex notifications

The local Codex dispatcher uses the official `agent-turn-complete` notify event to play a distinct double chime. It preserves and forwards events to the existing Codex desktop hook and ignores prompt and response fields. Codex CLI attention events use its native `approval-requested` TUI notification; the desktop app uses its native permission and question notifications. See `codex-notifier\README.md` for details.

## Configure

Copy `config.example.json` to `config.json` and edit it:

```powershell
Copy-Item .\config.example.json .\config.json
```

Useful settings:

- `hotkey`: change the modifier keys and trigger key.
- `target_hotkey`: change the hotkey used to mark the current foreground window as the paste target.
- `monitor_sleep_hotkey`: change the hotkey used to toggle the monitor sleep guard.
- `chatgpt_allowed_hosts`: browser address hosts that are permitted to receive screenshots.
- `auto_click_composer`: whether to click near the bottom of the ChatGPT window before paste.
- `composer_click_offset_from_bottom`: fallback click position when the ChatGPT composer is not exposed to Windows accessibility.
- `monitor_sleep_delay_ms`: delay before turning off monitors, so releasing the hotkey does not immediately wake them.
- `monitor_sleep_guard_interval_seconds`: how often the guard re-sends the display-off request while enabled.
- `save_captures`: also save screenshots to `capture_directory` for debugging.
- `start_enabled`: start with capture enabled or disabled.

## Notes

- The capture is of the visible screen region occupied by the hovered window. If another window is covering it, that covered content can appear in the screenshot.
- Best workflow: keep ChatGPT in its own Chrome or Vivaldi window, activate the ChatGPT tab, then press `Ctrl+Alt+Shift+V` once to mark it as the paste target.
- Browser tabs often use the conversation topic as the window title, so title matching is only a fallback.
- In Vivaldi tab tiles, set the target hotkey while the desired ChatGPT tile is active. ImagePastor then pastes into that tile's composer instead of using the window center.
- If browser focus is blocked by Windows, the image still remains on the clipboard and can be pasted manually.
- The tray icon's `Enabled` toggle only gates capture; the app keeps running so you can turn it back on quickly.
- Press `Ctrl+Alt+Shift+M` to toggle the monitor sleep guard. While on, ImagePastor keeps Windows awake for background work and re-sends the display-off request every 15 seconds through its own hidden control window. Disabling the guard explicitly queues display-on after the final display-off command, avoiding delayed system-wide broadcasts. `Sleep Monitors Once` remains available for a one-time command.
- Use the tray menu's `Start with Windows` checkbox to launch ImagePastor automatically when you sign in.
