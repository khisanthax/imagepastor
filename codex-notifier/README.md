# ImagePastor Codex Completion Chime

`codex_notify.py` is a local dispatcher for Codex's supported top-level `notify` hook.

- It forwards the original payload to the pre-existing Codex desktop hook.
- It parses only the top-level `type` field.
- It plays `sounds/finished.wav` only for `agent-turn-complete`.
- It logs only timestamp and event type to `codex-notify.log` for delivery diagnostics.
- It does not log, store, or transmit prompts, source code, responses, secrets, or environment variables.

The top-level hook currently supports completion events only. For the Codex CLI/TUI, `approval-requested` uses the built-in TUI notification system. In the Codex desktop app, permission and question alerts use the app's native notification settings.
