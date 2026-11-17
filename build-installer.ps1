param(
    [string]$Version = "1.1.0",
    [string]$Compiler = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    [switch]$SkipDependencies
)
$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must be major.minor.patch" }
if (-not (Test-Path -LiteralPath $Compiler)) { throw "Install Inno Setup 6 or provide -Compiler" }
Push-Location $PSScriptRoot
try {
    $root = Join-Path $PSScriptRoot "build\installer-$Version"
    $raw = Join-Path $root "raw"
    $payload = Join-Path $root "payload"
    if (Test-Path $root) { throw "Build directory already exists; choose a new version or archive it first: $root" }
    & .\build.ps1 -OutputDirectory $raw -WorkDirectory (Join-Path $root "work") -SkipDependencies:$SkipDependencies
    & .\.venv\Scripts\python.exe tools\stage_release.py --raw $raw --payload $payload
    if ($LASTEXITCODE) { throw "Release staging failed" }
    $release = Join-Path $PSScriptRoot "releases"
    & $Compiler "/DAppVersion=$Version" "/DPayloadDir=$payload" "/DReleaseDir=$release" installer\ImagePastor.iss
    if ($LASTEXITCODE) { throw "Installer compiler failed" }
    $file = Join-Path $release "ImagePastor-Setup-$Version-windows-x64.exe"
    $hash = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLowerInvariant()
    "$hash  $([IO.Path]::GetFileName($file))" | Set-Content (Join-Path $release "SHA256SUMS.txt") -Encoding ascii
    Write-Host "Installer: $file"
} finally {
    Pop-Location
}
