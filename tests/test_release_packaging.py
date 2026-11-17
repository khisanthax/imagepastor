import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))
from stage_release import stage


class ReleasePackagingTests(unittest.TestCase):
    def test_payload_uses_clean_defaults_never_live_config_or_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "raw"
            raw.mkdir()
            for name in ("ImagePastor.exe", "ImagePastorDiagnostics.exe"):
                (raw / name).write_bytes(b"MZ-test")
            (raw / "imagepastor.log").write_text("PRIVATE")
            (raw / "config.json").write_text('{"face_wake_camera_index":6}')
            (root / "config.example.json").write_text("{}")
            (root / "README.md").write_text("instructions")
            (root / "installer").mkdir()
            defaults = {"lpha_enabled": False, "face_wake_enabled": False}
            (root / "installer/default-config.json").write_text(json.dumps(defaults))
            for source in ("chrome-extension", "codex-notifier"):
                (root / source / "__pycache__").mkdir(parents=True)
                (root / source / "asset.txt").write_text("asset")
                (root / source / "__pycache__/private.pyc").write_bytes(b"PRIVATE")
            distribution = SimpleNamespace(version="1.0", files=[], read_text=lambda name: "license metadata")
            payload = root / "payload"
            with patch("stage_release.importlib.metadata.distribution", return_value=distribution):
                stage(raw, payload, root)
            self.assertEqual(json.loads((payload / "config.json").read_text()), defaults)
            self.assertFalse(list(payload.rglob("*.log")))
            self.assertFalse(list(payload.rglob("*.pyc")))
            self.assertTrue((payload / "ImagePastor-Chime/asset.txt").exists())
            with self.assertRaises(FileExistsError):
                stage(raw, payload, root)

    def test_fresh_installer_defaults_disable_camera_and_personal_macro(self):
        defaults = json.loads((ROOT / "installer/default-config.json").read_text())
        self.assertFalse(defaults["lpha_enabled"])
        self.assertFalse(defaults["face_wake_enabled"])
        self.assertFalse(defaults["auto_sleep_enabled"])
        self.assertFalse(defaults["save_captures"])

    def test_disabled_macro_does_not_register_or_start_a_worker(self):
        import imagepastor as app
        with patch.object(app.user32, "RegisterHotKey") as register:
            self.assertFalse(app.register_lpha_hotkey({"lpha_enabled": False}))
            self.assertFalse(app.register_lpha_hotkey({"lpha_enabled": False}, inspect_only=True))
        register.assert_not_called()
        with patch.object(app, "active_config", {"lpha_enabled": False}), \
                patch.object(app, "notify_lpha"), patch.object(app.threading, "Thread") as thread:
            app.start_lpha_autofill()
        thread.assert_not_called()
