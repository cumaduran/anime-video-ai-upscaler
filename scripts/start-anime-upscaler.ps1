param([string]$Python = "")
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $localPython = Join-Path $repoRoot '.venv-anime\Scripts\pythonw.exe'
    if (Test-Path -LiteralPath $localPython) { $Python = $localPython }
    else { $Python = 'pythonw.exe' }
}
Push-Location -LiteralPath (Join-Path $repoRoot 'app')
try { & $Python -m anime_video_upscaler }
finally { Pop-Location }
