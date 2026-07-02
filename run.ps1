# MrFoX-MeM - dev convenience launcher for Windows (PowerShell).
#
# Ensures the venv exists, starts the core API bound to 127.0.0.1, waits for
# /health, then opens the UI in the default browser via Python's webbrowser
# module. This is a thin wrapper over cli.py; on macOS/Linux use ./run.sh.
#
# Usage (from this folder):
#     powershell -ExecutionPolicy Bypass -File .\run.ps1
# If you hit an execution-policy error, the line above runs it without changing
# any machine-wide policy. Nothing here alters your execution policy.

$ErrorActionPreference = "Stop"

# Resolve the project root (this script's directory), regardless of cwd.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$Host_ = "127.0.0.1"
$Port  = if ($env:MRFOX_PORT) { $env:MRFOX_PORT } else { "8077" }
$Base  = "http://${Host_}:${Port}"
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"

function Log($msg) { Write-Host "[mrfox] $msg" -ForegroundColor Cyan }
function Err($msg) { Write-Host "[mrfox] $msg" -ForegroundColor Red }

# --- prerequisites: ensure the venv exists ----------------------------------
if (-not (Test-Path $VenvPython)) {
    Log "no venv found - running 'python cli.py setup' first..."
    python cli.py setup
    if ($LASTEXITCODE -ne 0) { Err "setup failed."; exit 1 }
}

# --- start core API in the background ---------------------------------------
Log "starting core API on $Base (127.0.0.1 only)..."
$server = Start-Process -FilePath $VenvPython `
    -ArgumentList @("-m", "uvicorn", "core.api:app", "--host", $Host_, "--port", $Port) `
    -WorkingDirectory $ScriptDir -PassThru -NoNewWindow

# --- wait for /health -------------------------------------------------------
Log "waiting for $Base/health ..."
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    if ($server.HasExited) { Err "core API process exited early."; exit 1 }
    try {
        $resp = Invoke-WebRequest -Uri "$Base/health" -TimeoutSec 2 -UseBasicParsing
        if ($resp.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
}

if (-not $ready) {
    Err "core API did not become healthy in time."
    try { $server.Kill() } catch {}
    exit 1
}
Log "core API healthy."

# --- open the UI (cross-OS, via Python's webbrowser module) -----------------
Log "opening UI: $Base/"
& $VenvPython -m webbrowser "$Base/" 2>$null

Log "core API running (pid $($server.Id)). Press Ctrl-C or close this window to stop."
try {
    Wait-Process -Id $server.Id
} finally {
    if (-not $server.HasExited) {
        Log "stopping core API..."
        try { $server.Kill() } catch {}
    }
}
