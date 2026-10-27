# Build one-folder exe: dist\airmouse\airmouse.exe (works from any cwd).
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI.
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found: $py" }

$out = Join-Path $root "dist\airmouse"
$keep = Join-Path $root "build\_keep"

Push-Location $root
try {
    # Back up user data (calibrated config, backups, logs) that a clean build would wipe
    if (Test-Path $keep) { Remove-Item $keep -Recurse -Force }
    New-Item -ItemType Directory -Force $keep | Out-Null
    if (Test-Path $out) {
        Get-ChildItem $out -File -Filter "config.toml*" | Copy-Item -Destination $keep -Force
        $oldLogs = Join-Path $out "logs"
        if (Test-Path $oldLogs) { Copy-Item $oldLogs (Join-Path $keep "logs") -Recurse -Force }
    }

    # PyInstaller logs to stderr; in PS 5.1 that would abort under "Stop"
    $ErrorActionPreference = "Continue"
    & $py -m PyInstaller --noconfirm --clean (Join-Path $root "airmouse.spec")
    $code = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($code -ne 0) { throw "PyInstaller failed (exit $code)" }

    # Restore user data
    $restored = $false
    Get-ChildItem $keep -File -Filter "config.toml*" | ForEach-Object {
        Copy-Item $_.FullName $out -Force
        if ($_.Name -eq "config.toml") { $restored = $true }
    }
    New-Item -ItemType Directory -Force (Join-Path $out "logs") | Out-Null
    $keptLogs = Join-Path $keep "logs"
    if (Test-Path $keptLogs) { Copy-Item (Join-Path $keptLogs "*") (Join-Path $out "logs") -Recurse -Force }

    # Default config must be neutral (not the developer's personal one); comments are kept
    if (-not $restored) {
        $env:PYTHONPATH = Join-Path $root "src"
        # Helper goes to a temp .py file: PS 5.1 mangles quotes in multi-line "python -c"
        $helper = Join-Path $keep "make_default_config.py"
        $lines = @(
            "import sys",
            "from pathlib import Path",
            "from airmouse.config import update_toml_text",
            "src, dst = Path(sys.argv[1]), Path(sys.argv[2])",
            "text = src.read_bytes().decode('utf-8')",
            "text = update_toml_text(text, {('camera', 'hand'): 'right', ('gesture', 'normal_facing'): 0, ('meta', 'calibrated'): ''})",
            "dst.write_bytes(text.encode('utf-8'))"
        )
        Set-Content -Path $helper -Value $lines -Encoding ASCII
        & $py $helper (Join-Path $root "config.toml") (Join-Path $out "config.toml")
        if ($LASTEXITCODE -ne 0) { throw "failed to generate neutral config.toml" }
        Write-Host "config.toml: neutral default generated"
    } else {
        Write-Host "config.toml: kept the existing one from the previous dist"
    }

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
