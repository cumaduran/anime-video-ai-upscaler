param([string]$AppDirectory = '', [string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$appDir = if ($AppDirectory) { [IO.Path]::GetFullPath($AppDirectory) } else { Join-Path $repoRoot 'build\anime-video-upscaler-dev\dist\Anime Video Upscaler and Encoder' }
$exe = Join-Path $appDir 'Anime Video Upscaler and Encoder.exe'
foreach ($path in @($exe, (Join-Path $appDir 'runtime\engine\avue-engine.exe'), (Join-Path $appDir 'runtime\engine\avue-realcugan.exe'), (Join-Path $appDir 'runtime\engine\vcomp140.dll'), (Join-Path $appDir 'runtime\engine\RealCUGAN-LICENSE.txt'), (Join-Path $appDir 'runtime\ffmpeg\ffmpeg.exe'), (Join-Path $appDir 'runtime\ffmpeg\ffprobe.exe'), (Join-Path $appDir 'runtime\hdr-metadata\dovi_tool.exe'), (Join-Path $appDir 'runtime\hdr-metadata\hdr10plus_tool.exe'), (Join-Path $appDir 'runtime\licenses\dovi-tool.txt'), (Join-Path $appDir 'runtime\licenses\hdr10plus-tool.txt'), (Join-Path $appDir 'LICENSE'), (Join-Path $appDir 'NOTICE'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Package file missing: $path" }
}
$manifest = Get-Content -LiteralPath (Join-Path $appDir 'runtime\SHA256.json') -Raw | ConvertFrom-Json
foreach ($entry in $manifest) {
    $path = Join-Path (Join-Path $appDir 'runtime') $entry.Path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.Hash) { throw "Bundled binary verification failed: $($entry.Path)" }
}
& (Join-Path $appDir 'runtime\engine\avue-engine.exe') --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Bundled AI engine failed to start.' }
& (Join-Path $appDir 'runtime\ffmpeg\ffmpeg.exe') -version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Bundled FFmpeg failed to start.' }
& (Join-Path $appDir 'runtime\ffmpeg\ffprobe.exe') -version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Bundled FFprobe failed to start.' }
& (Join-Path $appDir 'runtime\dovi-ffmpeg\ffmpeg.exe') -version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Bundled Dolby Vision color converter failed to start.' }
& (Join-Path $appDir 'runtime\fel-baker\python\python.exe') -c 'import vapoursynth; print(vapoursynth.core.version_number())' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Bundled Dolby Vision FEL runtime failed to start.' }
foreach ($name in @('dovi_tool.exe', 'hdr10plus_tool.exe', 'mkvmerge.exe')) {
    & (Join-Path $appDir "runtime\hdr-metadata\$name") --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Bundled HDR metadata tool failed to start: $name" }
}
$oldPath = $env:PATH
$oldProfile = $env:ANIME_UPSCALER_HOME
$oldQt = $env:QT_QPA_PLATFORM
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:ANIME_UPSCALER_HOME = Join-Path $repoRoot 'build\ci-smoke-profile'
    $env:QT_QPA_PLATFORM = 'offscreen'
    $process = Start-Process -FilePath $exe -WorkingDirectory $appDir -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 10
    $process.Refresh()
    if ($process.HasExited) { throw "Application exited during startup: $($process.ExitCode)" }
    Stop-Process -Id $process.Id
} finally {
    $env:PATH = $oldPath
    $env:ANIME_UPSCALER_HOME = $oldProfile
    $env:QT_QPA_PLATFORM = $oldQt
}
$output = if ($OutputDirectory) { [IO.Path]::GetFullPath($OutputDirectory) } else { Join-Path $repoRoot 'build\ci-package' }
if (-not $output.StartsWith($repoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Package destination must be inside the project.' }
if (Test-Path -LiteralPath $output) { throw 'CI package destination already exists.' }
New-Item -ItemType Directory -Path $output | Out-Null
$zip = Join-Path $output 'Anime-Video-Upscaler-Windows.zip'
$sevenZip = Join-Path $env:ProgramFiles '7-Zip\7z.exe'
if (-not (Test-Path -LiteralPath $sevenZip -PathType Leaf)) { throw '7-Zip is required for CI packaging.' }
& $sevenZip a -tzip -mx=3 $zip (Join-Path $appDir '*') | Out-Host
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $zip -PathType Leaf)) { throw 'Archive creation failed.' }
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
"$hash  Anime-Video-Upscaler-Windows.zip" | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding ascii
