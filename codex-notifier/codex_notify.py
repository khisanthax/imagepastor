from __future__ import annotations

import json
import subprocess
import sys
import winsound
from datetime import datetime
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000


def payload_type(payload_text: str) -> str | None:
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("type")
    return event_type if isinstance(event_type, str) else None


def forward_to_existing_hook(command: list[str], payload_text: str) -> None:
    if not command:
        return
    try:
        subprocess.Popen(
            [*command, payload_text],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
        pass


def play_finished_chime() -> None:
    sound = Path(__file__).resolve().parent / "sounds/finished.wav"
    if sound.is_file():
        winsound.PlaySound(str(sound), winsound.SND_FILENAME | winsound.SND_NODEFAULT)


def record_event(event_type: str | None) -> None:
    log_path = Path(__file__).resolve().parent / "codex-notify.log"
    try:
        with log_path.open("a", encoding="ascii") as handle:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"[{timestamp}] event={event_type or 'invalid'}\n")
    except OSError:
        pass


def main() -> int:
    if len(sys.argv) < 2:
        return 0

    payload_text = sys.argv[-1]
    delegate = sys.argv[1:-1]
    if delegate[:1] == ["--delegate"]:
        delegate = delegate[1:]

    event_type = payload_type(payload_text)
    record_event(event_type)

    # Preserve Codex's existing desktop integration without inspecting its payload.
    forward_to_existing_hook(delegate, payload_text)
    if event_type == "agent-turn-complete":
        play_finished_chime()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
