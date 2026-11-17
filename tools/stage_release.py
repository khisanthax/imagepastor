"""Stage only distributable files, never a developer's live dist directory."""

import argparse
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def stage(raw, payload, root=ROOT):
    payload.mkdir(parents=True, exist_ok=False)
    for name in ("ImagePastor.exe", "ImagePastorDiagnostics.exe"):
        shutil.copy2(raw / name, payload / name)
    shutil.copy2(root / "config.example.json", payload / "config.example.json")
    shutil.copy2(root / "installer/default-config.json", payload / "config.json")
    shutil.copy2(root / "README.md", payload / "README.md")
    for source, destination in (("chrome-extension", "ImagePastor-Chime"), ("codex-notifier", "ImagePastor-Codex")):
        # Copy repository assets, not live browser profiles or developer caches.
        shutil.copytree(root / source, payload / destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log"))
    notices = payload / "ThirdPartyNotices"
    notices.mkdir()
    packages = ("Pillow", "pystray", "pywinauto", "opencv-python-headless", "numpy", "comtypes", "pywin32", "six", "PyInstaller")
    versions = {}
    for name in packages:
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        destination = notices / name
        destination.mkdir()
        metadata = distribution.read_text("METADATA") or ""
        (destination / "METADATA.txt").write_text(metadata, encoding="utf-8")
        for file in distribution.files or ():
            if any(part.lower().startswith(("license", "copying", "notice")) for part in file.parts):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    target = destination / "files" / Path(*(part for part in file.parts if part not in (".", "..")))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, notices / "Python-LICENSE.txt")
    (notices / "versions.json").write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    args = parser.parse_args()
    stage(args.raw, args.payload)
