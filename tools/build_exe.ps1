# Build one-folder exe: dist\airmouse\airmouse.exe (works from any cwd).
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found: $py" }

Push-Location $root
try {
    # PyInstaller logs to stderr; in PS 5.1 that would abort under "Stop"
    $ErrorActionPreference = "Continue"
    & $py -m PyInstaller --noconfirm --clean (Join-Path $root "airmouse.spec")
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

    $out = Join-Path $root "dist\airmouse"
    # Files the app reads next to the exe (config.PROJECT_ROOT when frozen)
    Copy-Item (Join-Path $root "config.toml") $out -Force
    New-Item -ItemType Directory -Force (Join-Path $out "logs") | Out-Null
    $assets = Join-Path $root "assets"
    if (Test-Path $assets) { Copy-Item $assets $out -Recurse -Force }
    $model = Join-Path $root "models\hand_landmarker.task"
    if (Test-Path $model) {
        New-Item -ItemType Directory -Force (Join-Path $out "models") | Out-Null
        Copy-Item $model (Join-Path $out "models") -Force
    }

    $bytes = (Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum
    Write-Host ("Output: {0}" -f (Join-Path $out "airmouse.exe"))
    Write-Host ("Size:   {0:N1} MB" -f ($bytes / 1MB))
} finally {
    Pop-Location
}
