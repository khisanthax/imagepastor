param(
    [string]$OutputDirectory = "dist",
    [string]$WorkDirectory = "build\pyinstaller",
    [switch]$SkipDependencies
)
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    if (-not (Test-Path ".venv")) {
        python -m venv .venv
        if ($LASTEXITCODE) { throw "Virtual environment creation failed" }
    }
    if (-not $SkipDependencies) {
        & .\.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
        if ($LASTEXITCODE) { throw "Dependency installation failed" }
    }
    foreach ($name in @("ImagePastor", "ImagePastorDiagnostics")) {
        $source = if ($name -eq "ImagePastor") { "src\imagepastor.py" } else { "src\lifecycle_observer.py" }
        $extra = if ($name -eq "ImagePastor") { @("--collect-data", "cv2") } else { @() }
        & .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name $name `
            --distpath $OutputDirectory --workpath $WorkDirectory --specpath $WorkDirectory @extra $source
        if ($LASTEXITCODE) { throw "$name build failed" }
    }
    Copy-Item config.example.json (Join-Path $OutputDirectory "config.example.json") -Force
    foreach ($asset in @{ "chrome-extension" = "ImagePastor-Chime"; "codex-notifier" = "ImagePastor-Codex" }.GetEnumerator()) {
        $destination = Join-Path $OutputDirectory $asset.Value
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
        Get-ChildItem $asset.Key -Recurse -File | Where-Object { $_.Extension -ne ".pyc" } | ForEach-Object {
            $relative = [IO.Path]::GetRelativePath((Resolve-Path $asset.Key).Path, $_.FullName)
            $target = Join-Path $destination $relative
            New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
    Write-Host "Built ImagePastor and companions in $OutputDirectory"
} finally {
    Pop-Location
}
