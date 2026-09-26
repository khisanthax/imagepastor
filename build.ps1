$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
& .\.venv\Scripts\python.exe -m PyInstaller `
    --onefile `
    --windowed `
    --name ImagePastor `
    .\src\imagepastor.py

Copy-Item .\config.example.json .\dist\config.example.json -Force
New-Item -ItemType Directory -Path .\dist\ImagePastor-Chime -Force | Out-Null
Copy-Item .\chrome-extension\* .\dist\ImagePastor-Chime -Recurse -Force
New-Item -ItemType Directory -Path .\dist\ImagePastor-Codex -Force | Out-Null
Copy-Item .\codex-notifier\* .\dist\ImagePastor-Codex -Recurse -Force
Write-Host "Built .\dist\ImagePastor.exe, .\dist\ImagePastor-Chime, and .\dist\ImagePastor-Codex"
