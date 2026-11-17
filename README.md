# ImagePastor

A Windows tray utility for screenshot-to-ChatGPT workflows, keeping monitors asleep
while the PC continues working, optional local face-triggered wake, and completion
chimes. Python is bundled in the installer; no OpenAI API key is required.

## What It Does

| Feature | Behavior |
| --- | --- |
| Screenshot and paste | Captures the visible window under the pointer and attaches the image to a verified ChatGPT browser composer. It does not send the message. |
| Vivaldi tiles | Captures just the web page in the tile under the pointer and can pin a separate ChatGPT destination tile, including tiles with identical titles. |
| Monitor sleep guard | Keeps Windows running for background work while repeatedly enforcing display-off when necessary. |
| Optional face wake | Uses a local color webcam to detect any face after you have stepped away, disable the guard, and wake the monitors. Disabled on fresh installs. |
| ChatGPT completion chime | Optional Chrome/Vivaldi extension plays a local sound after a response finishes. Requires manual browser setup. |
| Codex completion chime | Bundled dispatcher assets support a separately configured completion hook; they are not automatically installed into Codex. |
| Startup and recovery | Optional sign-in startup, bounded recovery after abnormal app exits, and an optional independent process-lifecycle observer. |
| LPHA Autofill | Specialized owner-specific macro, disabled by the installer. Requires accessible form controls; the tested Citrix session does not expose them and is unsupported. |

The main app runs in the Windows notification area, not as a browser extension.
The chime extension is a separate, optional component. Monitor sleep does not lock
or suspend the PC; use Windows' normal lock and authentication separately.

## Install on Windows

Download `ImagePastor-Setup-1.1.0-windows-x64.exe` from the
[GitHub release](https://github.com/khisanthax/imagepastor/releases/tag/v1.1.0).
The repository and release are public; no GitHub account or repository invitation
is required to download. You can also transfer the installer to another PC.
No Python installation is needed.

Run the installer under the Windows account that will use ImagePastor. It installs
to `%LOCALAPPDATA%\Programs\ImagePastor`, creates a Start menu shortcut, and offers
optional startup, desktop-shortcut, and independent-diagnostics settings. Quit
ImagePastor and its diagnostic companion before installing an upgrade or uninstalling.
Existing settings are retained. Uninstall through Windows Installed Apps; local
settings and logs remain unless you remove them yourself.

Fresh installs have face wake, screenshot saving, and the owner-specific LPHA macro
disabled. Your camera index, local logs, browser targets, and personal configuration
are not included in the installer. To deliberately enable LPHA on an appropriate
workstation, set `"lpha_enabled": true` in its local `config.json` and restart.
The provider identity is the one documented below; it is not a generic signature tool.

The installer is unsigned. Windows may display an unknown-publisher warning.
Verify the download source and compare its SHA-256 with `SHA256SUMS.txt`; do not
disable Windows security protections. Windows 10/11 x64 is supported; Linux is not.
The browser chime extension still requires manual setup in the browser; the installer
does not access browser profiles or change browser permissions.

### Build the Installer

Install Inno Setup 6, then run `./build-installer.ps1`. The installer and checksum
are written to `releases/`. The build uses an isolated allowlisted payload under
`build/installer-1.1.0/`, never the developer's live `dist/` directory. Provide
`-Compiler` when Inno Setup is installed elsewhere. Third-party license notices
and dependency versions are included in the package. Each version uses a fresh
build directory to avoid stale files. Installer compilation uses Inno Setup's
[command-line compiler](https://jrsoftware.org/inno-setup/documentation/topic_compilercmdline.htm).

## Hotkeys and Quick Start

| Shortcut | Action |
| --- | --- |
| `Ctrl+Alt+V` | Capture the source window or Vivaldi tile under the pointer and paste into the saved ChatGPT destination. |
| `Ctrl+Alt+Shift+V` | Select the ChatGPT destination tile under the pointer; does not capture or paste. |
| `Ctrl+Alt+Shift+M` | Toggle monitor sleep guard: off displays while enabled, cancel sleep and wake when disabled. |
| `Ctrl+Alt+L` | Optional LPHA Autofill; disabled on fresh installs. Press again to cancel an active run. |
| `Ctrl+Alt+Shift+L` | Optional read-only LPHA structural check; also disabled when LPHA is disabled. |

Hotkey combinations can be changed in the local `config.json`.

ImagePastor runs in the Windows notification area. Right-click its icon to toggle `Enabled`, set the current window as target, keep monitors asleep, set up the ChatGPT completion chime, enable `Start with Windows`, or exit.

1. Open a ChatGPT conversation in Chrome or Vivaldi.
2. Point inside its page or destination tile and press `Ctrl+Alt+Shift+V` once.
3. Point inside the source window or Vivaldi page you want to capture, then press
   `Ctrl+Alt+V`.
4. Review the attachment and write/send your message yourself. Reselect the
   destination after navigation or an app restart when needed.

When the capture hotkey fires, ImagePastor:

1. Finds the source window and pointer location.
2. Captures the visible window bounds, or only the hovered Vivaldi web-page tile.
3. Places the screenshot on the Windows clipboard as an image.
4. Resolves the saved, URL-verified ChatGPT document and composer without scanning
   or selecting tabs. An unavailable or ambiguous saved target cancels paste.
5. Waits for modifier release, focuses only the verified editor, rechecks document
   identity and keyboard focus, and sends `Ctrl+V`. No Enter or Send is used.

If paste cannot be verified, the image remains on the clipboard for manual use.
Attaching an image may upload it to ChatGPT even before you send a message, so
check the source content before using the capture shortcut. Screenshot activity
logs can include source window titles; review logs before sharing them.

## Run

```powershell
.\run.ps1
```

Keep the PowerShell window open while using the app. Use the tray menu's **Exit**
for a clean shutdown of the app and its supervisor.

## Build an EXE

```powershell
.\build.ps1
```

The executables are written to `dist\ImagePastor.exe` and
`dist\ImagePastorDiagnostics.exe`. The local ChatGPT extension and Codex notifier
are copied to `dist\ImagePastor-Chime` and `dist\ImagePastor-Codex`. Put a
`config.json` next to the main EXE to override settings after packaging.

## ChatGPT completion chime

The bundled Manifest V3 extension plays one local chime when a response finishes. It does not read or store conversation text, make network requests, or use an OpenAI account or API key.

1. Right-click ImagePastor in the notification area and choose `Set Up ChatGPT Chime`.
2. On the browser's extensions page, enable Developer mode, choose `Load unpacked`, and select the folder ImagePastor opened. ImagePastor prefers Vivaldi for this setup when it is installed.

Use the extension toolbar popup to enable or disable the chime, set its volume, optionally chime only when ChatGPT is not focused, test the sound, or enable development logging. Detailed detection and privacy notes are in `chrome-extension\README.md`.

## Codex notifications

The local Codex dispatcher uses the official `agent-turn-complete` notify event to play a distinct double chime. It preserves and forwards events to the existing Codex desktop hook and ignores prompt and response fields. Codex CLI attention events use its native `approval-requested` TUI notification; the desktop app uses its native permission and question notifications. See `codex-notifier\README.md` for details.

## Configure

For a source/portable build with no existing settings, copy `config.example.json`
to `config.json`. For an installer-based installation, edit the existing file in
`%LOCALAPPDATA%\Programs\ImagePastor` instead of replacing it:

```powershell
Copy-Item .\config.example.json .\config.json
```

Useful settings:

- `hotkey`: change the modifier keys and trigger key.
- `target_hotkey`: change the hotkey used to mark the ChatGPT tile under the pointer as the paste target.
- `monitor_sleep_hotkey`: change the hotkey used to toggle the monitor sleep guard.
- `capture_vivaldi_tile`: capture only the hovered Vivaldi page (default `true`).
- `face_wake_enabled`: opt in to local camera-based wake (default `false`).
- `face_wake_camera_index`: choose the DirectShow camera number (default `0`).
- `lpha_enabled`: enable the specialized LPHA hotkeys/menu; the installer sets this to `false`.
- `chatgpt_allowed_hosts`: browser address hosts that are permitted to receive screenshots.
- `auto_click_composer` and `composer_click_offset_from_bottom`: legacy settings; unverified window-center/bottom clicks are no longer used for paste.
- `monitor_sleep_delay_ms`: delay before turning off monitors, so releasing the hotkey does not immediately wake them.
- `monitor_sleep_guard_interval_seconds`: how often the guard re-sends the display-off request while enabled.
- `save_captures`: also save screenshots to `capture_directory` for debugging.
- `start_enabled`: start with capture enabled or disabled.

## Notes

- Vivaldi capture uses the web-page bounds of the tile under the pointer, excluding other tiles and browser chrome. Nested iframes stay within their outer tab capture. Point inside the page, not the tab strip. Missing/unverified tile bounds cancel capture rather than capturing the whole browser. `capture_vivaldi_tile: false` explicitly restores whole-window capture.
- Other apps retain visible hovered-window capture. Covered content may appear if another window overlaps the capture area.
- Point anywhere inside the desired ChatGPT tile and press `Ctrl+Alt+Shift+V` to set it as the paste target, even if another window has focus. With the pointer outside a browser, the foreground browser's unique active composer is used instead. Target selection does not type or paste anything.
- Conversation titles can be arbitrary or identical across tiles. Targeting verifies the ChatGPT document URL and pins its UI Automation document instance where available; it recognizes both current `Ask ChatGPT` and older `Chat with ChatGPT` editors. If the saved tile disappears or becomes ambiguous, paste is cancelled instead of silently redirected. Reselect after navigation/restart when necessary.
- If Vivaldi does not expose a unique verified composer, the screenshot remains on the clipboard for manual paste; there is no window-center fallback.
- Target lookup does not select or scan tabs. Automatic paste waits for all modifier keys to be released, verifies editor focus, and cancels rather than redirecting to another tile or clicking voice controls.
- If browser focus is blocked by Windows, the image still remains on the clipboard and can be pasted manually.
- The tray icon's `Enabled` toggle only gates capture; the app keeps running so you can turn it back on quickly.
- Press `Ctrl+Alt+Shift+M` to toggle the monitor sleep guard. While on, ImagePastor keeps Windows awake for background work and checks the display every 15 seconds. It only sends another off request when Windows does not report the display as off.
- Press the monitor hotkey once to wake the monitors. ImagePastor cancels pending sleep commands and holds the display awake for ten seconds while it reconnects. Extra hotkey presses during those ten seconds keep the monitors awake. Normal Windows power policy resumes afterward.
- Unlocking Windows also cancels monitor sleep and wakes the displays automatically.
- `Wake on Face` in the tray is an opt-in, saved setting. While the guard is active,
  the local RGB camera starts after a three-second delay and first needs about
  two seconds with no face, then one second
  of consistent face detection to disable the guard and wake the screens. Step away
  after pressing the sleep hotkey; simply remaining seated will not wake them again.
  Any face (or a convincing face image) may trigger this: it is not authentication.
- Face detection saves/uploads no images or face templates. The camera indicator may
  stay lit while the guard watches. Camera capture stops when you wake manually,
  disable the feature, exit, or lock Windows. It does not operate on the locked
  sign-in screen or unlock Windows; use normal wake/Windows Hello there. The existing
  unlock handler then disables the guard. Camera/privacy/driver failures leave the
  hotkey working and are logged without opening a camera preview.
- `face_wake_camera_index` selects a local RGB camera (default `0`, allowed `0`-`16`).
  Close video-call apps if they hold the camera exclusively. Low light, profiles,
  camera angle and face-like images can affect detection. A helper process isolates
  camera-driver stalls from the monitor-control thread. OpenCV 4.x and its bundled
  face cascade are included in the Windows build; no model download is needed.
- Monitor power commands run on a separate thread so a slow driver transition cannot block the hotkey. `Sleep Monitors Once` and the explicit `Wake Monitors` action are also available in the tray menu. The log includes Windows' reported session display state and the duration of each power call.
- Use the tray menu's `Start with Windows` checkbox to launch ImagePastor automatically when you sign in.

## Webcam Compatibility and Setup

No particular camera brand or Windows Hello model is required. The face-wake
worker opens a normal color camera through Windows DirectShow. Standard USB
webcams and built-in cameras can work if their Windows driver exposes a compatible
video stream; compatibility with every device is not guaranteed.

With a single webcam, you can usually just enable **Wake on Face** in the tray.
ImagePastor tries camera index `0`; it does not scan cameras or automatically pick
one by model. If a laptop has a built-in camera, additional USB webcams, or virtual
cameras, index `0` may not be the one you intended. There is no camera-selection
dialog in this release. Edit `face_wake_camera_index` in the local `config.json`
to the correct index (`0` through `16`) and restart ImagePastor. Indexes can change
when devices or drivers change; your other PC's camera number is not transferable.

To use face wake, enable it, press the monitor sleep hotkey, and step away. After
about two seconds without a detected face, a sustained face for about one second
disables the sleep guard and wakes the displays. Remaining seated immediately
after enabling the guard should not wake them again. Re-enable the guard with the
hotkey when you next leave.

This is **face detection, not recognition or authentication**: any face, including
a convincing image, may trigger wake. It neither replaces Windows Hello nor
unlocks Windows. No camera frames or face templates are saved or uploaded. Camera
use stops when the guard is disabled or the Windows session locks. Close other
camera apps if the webcam is busy; a camera/driver failure leaves the hotkey
available but can prevent automatic face wake.

## Process Diagnostics

The installed app writes local diagnostics beside `ImagePastor.exe`:

- `imagepastor.log`: startup PID, five-minute process heartbeat, exit requests,
  message-loop shutdown, and exception frame locations.
- `imagepastor-runtime.json`: latest process receipt (`running`, `clean`, or
  `error`). A subsequent launch reports a missing clean-exit receipt without
  guessing the cause. This is not a live process-status API.
- `imagepastor-fault.log`: native-fault Python stack traces when the runtime
  can capture them, not memory dumps or screen contents.

Windows COM exception traces can be handled/nonterminating even if the runtime
labels them fatal. Correlate them with later heartbeats and supervisor exit codes
before treating them as the cause of a shutdown.

The additional diagnostics do not record draft text, camera frames, or exception
messages. They cannot identify who forcibly terminates the process or guarantee
evidence for power loss, failures before diagnostics initialize, or hard kills.
The external supervisor records launcher exit codes in
`imagepastor-supervisor.log` and restarts abnormal exits (at most two retries
within ten minutes). A clean exit, including tray Exit, is never restarted.
Recovery starts with the monitor guard disabled to avoid unexpected display-off
loops; re-enable it manually. Terminating the entire process tree, session
shutdown, or killing the supervisor itself can still prevent recovery or logging.

### Independent Lifecycle Observer

`ImagePastorDiagnostics.exe` is a separate, observation-only companion. It keeps
Windows process handles for ImagePastor processes and records PID, parent PID,
observed exit code, and matching Application Hang/Error event IDs in
`imagepastor-lifecycle.jsonl` beside its executable (`dist/` in a development
build). It can survive termination aimed only at the
ImagePastor executable/process tree. It polls every two seconds and logs its own
heartbeat every five minutes. It does not restart apps, change monitor state,
inspect windows, or collect screenshots, drafts, command lines, or other app data.

The observer cannot identify the process that requested termination from an exit
code alone. If it is also killed or Windows shuts down abruptly, a final event
may be unavailable. Processes too short-lived to be observed can be missed.
The installer offers independent diagnostics as an optional sign-in task. It is
not enabled on every PC automatically. To stop this companion before upgrading
or uninstalling, end `ImagePastorDiagnostics.exe` in Task Manager; exit the main
app through its tray menu. Development incident evidence is summarized separately
in [Diagnostic History](https://github.com/khisanthax/imagepastor/blob/main/docs/diagnostic-history.md).

## LPHA Autofill

This specialized feature is disabled on fresh installer-based installations and
uses the fixed provider values below. It is not a generally working Citrix
autofill tool: the tested remote session exposes no local text fields or dropdowns.
Do not enable it on another person's workstation without intentionally reviewing
the provider identity and workflow.

When enabled and its accessibility checks pass, `Ctrl+Alt+L` attempts to fill the
currently active Citrix form using keyboard input and Tab, without coordinates.
It does not press Enter, Save, Submit, or Sign. The other shortcuts are unchanged.

1. Use an approved scratch/test form first. Bring the Citrix form to the foreground.
2. Leave Printed Name, Credential, and NPI# genuinely empty. Focus the LPHA dropdown
   yourself. All four fields must be visible, enabled, and accessible.
3. Release other keys, turn Caps Lock off, and press `Ctrl+Alt+L` once. Keep hands
   off the keyboard/mouse while it runs. The sequence must be LPHA, Printed Name,
   Credential, NPI#, with exactly one Tab between each.
4. Review the result manually. The final focus remains on NPI#; there is no final
   Tab or submission. The values are:

| Field | Value |
| --- | --- |
| LPHA dropdown | Vega, Michael |
| Printed Name | michael vega |
| Credential | lcsw |
| NPI# | 1699090837 |

Press the hotkey again, hold Escape briefly, or choose **Cancel LPHA Autofill** in
the tray to cancel. Cancellation is checked between inputs and during navigation
waits. It cannot interrupt a blocked Citrix accessibility call, but no further
input is sent until that call returns and safety checks pass. Partial entries
are left untouched for manual review, never cleared or automatically retried.
Prefer the hotkey again: Escape is not intercepted and can also dismiss Citrix UI.

The optional tray **LPHA Autofill (3-second delay)** command gives you three
seconds to return focus to the Citrix LPHA dropdown. Set `lpha_show_menu` to
`false` to hide it. Configure `lpha_hotkey` in `config.json` like the existing
hotkeys. A conflicting LPHA hotkey is disabled without disabling existing ones.

The implementation requires UI Automation exposure of exactly one ComboBox named
`LPHA` and three Edit controls named `Printed Name`, `Credential`, and `NPI#` in
the foreground `wfica32.exe`, `CDViewer.exe`, or current
`Citrix.DesktopViewer.App.exe` session window. Accessible label
relationships are accepted; whitespace, case, and a trailing colon are normalized.
Exact local labels can be configured, for example:

```json
"lpha_labels": {
  "lpha": "LPHA",
  "name": "Printed Name",
  "credential": "Credential",
  "npi": "NPI#"
}
```

Never guess labels to bypass a failure. Missing/duplicate fields, inaccessible
values, existing text, focus changes, keyboard layout mismatches, held modifiers,
and readback failures stop the operation. A noneditable dropdown uses Home and
type-ahead; an editable dropdown uses Ctrl+A and typing. Exact selected-value
readback is required before proceeding and again after Tab. Unsupported dropdown
behavior stops safely rather than trying Enter or a different technique.

Tray notifications and the local log contain fixed status/error codes, not form
values, patient names, window titles, screenshots, or accessibility exception
messages. Coordinates and window size are not used. Accessibility/focus changes
still introduce a small check-to-input race; do not interact during autofill.

### Citrix Limitation

For a read-only diagnosis, keep the actual Citrix LPHA form foreground and press
`Ctrl+Alt+Shift+L` (configurable as `lpha_inspect_hotkey`). This is **not** the
autofill shortcut. It records two snapshots of control-type counts and exact
expected-label match counts in `imagepastor.log`. It does not read field values,
print accessible names, focus anything, or send keyboard/mouse input. A missing
field match does not by itself prove that remote accessibility is absent. Zero
Edit/ComboBox controls suggests no usable local form exposure; present controls
with missing label matches require further label verification, not relaxed checks.

The real read-only check on the tested Citrix session found two consistent
18-node snapshots, zero Edit/ComboBox controls, zero expected-field matches, and
zero metadata errors. The viewer was exposed but the remote form fields were not;
that session cannot safely be autofilled by this local implementation. No live
form entry was performed. Many VDI
sessions expose only a bitmap to local accessibility. In that case this feature
aborts with `FIELDS_NOT_UNIQUE_OR_EXPOSED` or
`ACCESSIBILITY_OR_INPUT_UNAVAILABLE`; it does not fall back to blind Tabs, OCR,
coordinates, or clipboard pasting. The smallest practical alternative is an
IT-approved helper running **inside** the VDI session where the actual form
controls can be verified. Until then, enter these four values manually.

The form itself may persist edits automatically on typing or blur. ImagePastor
cannot disable that server-side behavior; use only a form/workflow approved for
draft editing. It never deliberately activates a save, submit, or signature
control. Live validation must use an approved scratch form, not a patient record.
