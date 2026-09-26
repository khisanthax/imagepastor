import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codex-notifier"))

import codex_notify


class PayloadTypeTests(unittest.TestCase):
    def test_accepts_completion_event_without_reading_message_fields(self):
        payload = json.dumps(
            {
                "type": "agent-turn-complete",
                "input-messages": ["private prompt"],
                "last-assistant-message": "private response",
            }
        )
        self.assertEqual(codex_notify.payload_type(payload), "agent-turn-complete")

    def test_rejects_invalid_json(self):
        self.assertIsNone(codex_notify.payload_type("not json"))

    def test_ignores_non_object_json(self):
        self.assertIsNone(codex_notify.payload_type("[]"))

    def test_forwards_payload_unchanged_to_existing_hook(self):
        payload = '{"type":"agent-turn-complete","private":"unchanged"}'
        with patch.object(codex_notify.subprocess, "Popen") as popen:
            codex_notify.forward_to_existing_hook(["existing-hook.exe", "turn-ended"], payload)

        command = popen.call_args.args[0]
        self.assertEqual(command, ["existing-hook.exe", "turn-ended", payload])

    def test_non_completion_event_does_not_play_finished_chime(self):
        with (
            patch.object(sys, "argv", ["codex_notify.py", '{"type":"approval-requested"}']),
            patch.object(codex_notify, "play_finished_chime") as play,
            patch.object(codex_notify, "record_event") as record,
        ):
            self.assertEqual(codex_notify.main(), 0)
        play.assert_not_called()
        record.assert_called_once_with("approval-requested")


if __name__ == "__main__":
    unittest.main()
