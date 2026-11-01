param([string]$OutputRoot = '', [string]$RuntimeRoot = '')
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
if (-not $OutputRoot) { $OutputRoot = Join-Path $repoRoot 'build\anime-video-upscaler-dev' }
$output = [IO.Path]::GetFullPath($OutputRoot)
if (-not $output.StartsWith($repoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Output must be inside the project.' }
if (Test-Path -LiteralPath $output) { throw "Build output already exists: $output" }
$runtime = if ($RuntimeRoot) { [IO.Path]::GetFullPath($RuntimeRoot) } else { Join-Path $repoRoot 'runtime' }
if (-not $runtime.StartsWith($repoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime must be inside the project.' }
$nativeBuild = Join-Path $repoRoot 'build\native-independent'
foreach ($name in @('avue-engine.exe','libvideo2x.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $nativeBuild $name) -PathType Leaf)) { throw "Build the fixed native engine first: $name" }
}

foreach ($path in @('ffmpeg\ffmpeg.exe','ffmpeg\ffprobe.exe','PROVENANCE.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $runtime $path) -PathType Leaf)) { throw "Prepare runtime first: $path" }
}
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'engine\avue-engine.exe') -PathType Leaf) -and -not (Test-Path -LiteralPath (Join-Path $runtime 'engine\video2x.exe') -PathType Leaf)) { throw 'Prepare runtime first: AI engine missing.' }
foreach ($path in @('engine\avue-realcugan.exe','engine\vcomp140.dll','engine\RealCUGAN-LICENSE.txt','engine\libvideo2x.dll','engine\ncnn.dll','engine\models\realesrgan','engine\models\realcugan','engine\models\libplacebo','engine\models\rife')) { if (-not (Test-Path -LiteralPath (Join-Path $runtime $path))) { throw "Prepare runtime first: $path" } }
$python = Join-Path $repoRoot '.venv-anime\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Project Python environment not found.' }
New-Item -ItemType Directory -Path $output | Out-Null
$icon = Join-Path $output 'app.ico'
& $python (Join-Path $PSScriptRoot 'generate-app-icon.py') $icon
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
$entry = Join-Path $repoRoot 'app\anime_video_upscaler\standalone.py'
$assets = (Join-Path $repoRoot 'app\anime_video_upscaler\assets') + ';anime_video_upscaler/assets'
$locales = (Join-Path $repoRoot 'app\anime_video_upscaler\locales') + ';anime_video_upscaler/locales'
$previousPath = $env:PATH
try {
    # Do not let unrelated development runtimes inject incompatible ICU DLLs.
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    & $python -m PyInstaller --noconfirm --onedir --windowed --name 'Anime Video Upscaler and Encoder' `
        --icon $icon --paths (Join-Path $repoRoot 'app') --add-data $assets --add-data $locales `
        --distpath (Join-Path $output 'dist') --workpath (Join-Path $output 'work') --specpath $output $entry
    if ($LASTEXITCODE -ne 0) { throw 'Development executable build failed.' }
} finally {
    $env:PATH = $previousPath
}
$appDir = Join-Path $output 'dist\Anime Video Upscaler and Encoder'
$sourceFiles = Get-ChildItem -LiteralPath (Join-Path $repoRoot 'app\anime_video_upscaler') -Recurse -File |
    Where-Object { $_.Extension -in '.py','.json','.svg' } | Sort-Object FullName
$sourceManifest = ($sourceFiles | ForEach-Object {
    $_.FullName.Substring($repoRoot.Length + 1).Replace('\','/') + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}) -join "`n"
$hashAlgorithm = [System.Security.Cryptography.SHA256]::Create()
try { $sourceHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($sourceManifest))).Replace('-', '').ToLowerInvariant() }
finally { $hashAlgorithm.Dispose() }
@{ built_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'; source_sha256 = $sourceHash } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $appDir 'BUILD.json') -Encoding utf8
$qtRoot = Join-Path $repoRoot '.venv-anime\Lib\site-packages\PySide6'
foreach ($file in @('vcruntime140.dll','vcruntime140_1.dll')) {
    $source = Join-Path $qtRoot $file
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Qt runtime missing: $file" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $appDir '_internal')
}
$bundledRuntime = Join-Path $appDir 'runtime'
New-Item -ItemType Directory -Path $bundledRuntime | Out-Null
Get-ChildItem -LiteralPath $runtime -Force | Where-Object { $_.Name -ne 'native-engine' } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $bundledRuntime -Recurse
}
# The application carries its own source-built native engine. Preserve all
# upstream dependency DLLs/models and record hashes for the actual packaged files.
$bundledEngine = Join-Path $appDir 'runtime\engine'
foreach ($name in @('video2x.exe','avue-engine-test.exe','avue-realesrgan.exe')) {
    $staged = Join-Path $bundledEngine $name
    if (Test-Path -LiteralPath $staged -PathType Leaf) { Remove-Item -LiteralPath $staged }
}
Copy-Item -LiteralPath (Join-Path $nativeBuild 'avue-engine.exe') -Destination (Join-Path $bundledEngine 'avue-engine.exe') -Force
Copy-Item -LiteralPath (Join-Path $nativeBuild 'libvideo2x.dll') -Destination (Join-Path $bundledEngine 'libvideo2x.dll') -Force
# The native process is standalone and cannot rely on another application's VC runtime.
foreach ($name in @('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll')) {
    $source = Join-Path $qtRoot $name
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Native C++ runtime missing: $name" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $bundledEngine $name) -Force
}

$bundledRuntime = Join-Path $appDir 'runtime'
@'
Anime Video Upscaler & Encoder development runtime.
avue-engine.exe and libvideo2x.dll are built from this checkout's modified Video2X-based source.
AI wrapper libraries are built from the source included in this repository.
Review LICENSE, NOTICE and the dependency source/build information before redistribution.
'@ | Add-Content -LiteralPath (Join-Path $bundledRuntime 'PROVENANCE.txt') -Encoding utf8
Get-ChildItem -LiteralPath $bundledRuntime -Recurse -File | Where-Object { $_.Extension -in '.exe','.dll' } |
    Get-FileHash -Algorithm SHA256 | Select-Object Hash,@{Name='Path';Expression={ $_.Path.Substring($bundledRuntime.Length + 1).Replace('\','/') }} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $bundledRuntime 'SHA256.json') -Encoding utf8
foreach ($file in @('LICENSE','NOTICE','README.md','README.tr.md','CHANGELOG.md')) { Copy-Item -LiteralPath (Join-Path $repoRoot $file) -Destination $appDir }
New-Item -ItemType Directory -Path (Join-Path $appDir 'docs') | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\anime-upscaler.md') -Destination (Join-Path $appDir 'docs')
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\project-structure.tr.md') -Destination (Join-Path $appDir 'docs')
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\cmd-dataset-guide.md') -Destination (Join-Path $appDir 'docs')
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\ai-development-roadmap.md') -Destination (Join-Path $appDir 'docs')
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\application-review.md') -Destination (Join-Path $appDir 'docs')
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\quality-samples.tr.md') -Destination (Join-Path $appDir 'docs')
Copy-Item -LiteralPath (Join-Path $repoRoot 'docs\images') -Destination (Join-Path $appDir 'docs\images') -Recurse
New-Item -ItemType Directory -Path (Join-Path $appDir 'scripts') | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot 'scripts\create-quality-samples.py') -Destination (Join-Path $appDir 'scripts')
Write-Output "Local development executable: $(Join-Path $appDir 'Anime Video Upscaler and Encoder.exe')"
