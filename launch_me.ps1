param(
    # Where the agent should work. Defaults to wherever you run this from.
    [string]$TargetPath
)

if (-not $TargetPath) {
    $TargetPath = (Get-Location).Path
    # "Run as administrator" shells start in C:\WINDOWS\System32 — never a
    # place the agent should write its .harness folder. Fall back to home.
    if ($TargetPath -like "$env:WINDIR*") {
        Write-Host "You're in a system folder ($TargetPath); starting in $HOME instead."
        $TargetPath = $HOME
    }
}

if (-not (Test-Path $TargetPath -PathType Container)) {
    Write-Host "Error: Directory '$TargetPath' not found."
    exit 1
}

# The harness (main.py, .venv, .env) lives where this script lives.
$Harness = $PSScriptRoot
$Python = Join-Path $Harness ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Warning "venv python not found at $Python; falling back to 'python' on PATH."
    $Python = "python"
}

# UTF-8 so the CLI's emoji output never trips Windows' legacy code page.
$env:PYTHONUTF8 = "1"

Write-Host "Launching AI in directory: $TargetPath"
Push-Location $TargetPath
try {
    & $Python (Join-Path $Harness "main.py")
}
finally {
    Pop-Location
}
