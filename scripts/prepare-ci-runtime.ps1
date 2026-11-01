param([string]$DestinationRoot = '')
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$deps = Join-Path $repoRoot 'build\independent-deps'
$native = Join-Path $repoRoot 'build\native-independent'
$destination = if ($DestinationRoot) { [IO.Path]::GetFullPath($DestinationRoot) } else { Join-Path $repoRoot 'runtime' }
if (-not $destination.StartsWith($repoRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime must be inside the checkout.' }
if (Test-Path -LiteralPath $destination) { throw "Runtime already exists: $destination" }
$ffmpeg = Join-Path $deps 'ffmpeg-shared\ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-shared-7.1'
$ffmpegCli = Join-Path $deps 'ffmpeg-cli\ffmpeg-9.0.2-essentials_build'
$ncnn = Join-Path $deps 'ncnn\ncnn-20240820-windows-vs2022-shared\x64'
$cugan = Join-Path $deps 'realcugan\realcugan-ncnn-vulkan-20220728-windows'
$vulkan = Join-Path $repoRoot 'vendor\vulkan\vulkan-1.dll'
foreach ($file in @((Join-Path $native 'avue-engine.exe'), (Join-Path $native 'libvideo2x.dll'), (Join-Path $ncnn 'bin\ncnn.dll'), (Join-Path $ffmpegCli 'bin\ffmpeg.exe'), (Join-Path $cugan 'realcugan-ncnn-vulkan.exe'), $vulkan)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required build input missing: $file" }
}
if ((Get-FileHash -LiteralPath $vulkan -Algorithm SHA256).Hash -ne '8644669401D2C080EA10CFEA2BA8A7C63D76E10A4E7AE8246837DF1DFD157EA8') { throw 'Bundled Vulkan loader SHA-256 mismatch.' }
$engineDir = Join-Path $destination 'engine'
$ffmpegDir = Join-Path $destination 'ffmpeg'
$licenseDir = Join-Path $destination 'licenses'
New-Item -ItemType Directory -Force -Path $engineDir,$ffmpegDir,$licenseDir | Out-Null
Get-ChildItem -LiteralPath (Join-Path $ffmpeg 'bin') -Filter '*.dll' -File | Copy-Item -Destination $engineDir
foreach ($file in @('ffmpeg.exe','ffprobe.exe')) { Copy-Item -LiteralPath (Join-Path $ffmpegCli "bin\$file") -Destination $ffmpegDir }
Copy-Item -LiteralPath (Join-Path $ffmpegCli 'LICENSE') -Destination (Join-Path $ffmpegDir 'LICENSE')
Copy-Item -LiteralPath (Join-Path $ffmpeg 'LICENSE.txt') -Destination (Join-Path $licenseDir 'ffmpeg-shared.txt')
Copy-Item -LiteralPath (Join-Path $ncnn 'bin\ncnn.dll') -Destination $engineDir
Copy-Item -LiteralPath $vulkan -Destination $engineDir
foreach ($file in @('avue-engine.exe','libvideo2x.dll','librealesrgan-ncnn-vulkan.dll','librealcugan-ncnn-vulkan.dll','librife-ncnn-vulkan.dll')) { Copy-Item -LiteralPath (Join-Path $native $file) -Destination $engineDir }
Copy-Item -LiteralPath (Join-Path $cugan 'realcugan-ncnn-vulkan.exe') -Destination (Join-Path $engineDir 'avue-realcugan.exe')
Copy-Item -LiteralPath (Join-Path $cugan 'vcomp140.dll') -Destination $engineDir
Copy-Item -LiteralPath (Join-Path $cugan 'LICENSE') -Destination (Join-Path $engineDir 'RealCUGAN-LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $repoRoot 'models') -Destination (Join-Path $engineDir 'models') -Recurse
foreach ($name in @('librealesrgan_ncnn_vulkan','librealcugan_ncnn_vulkan','librife_ncnn_vulkan')) { Copy-Item -LiteralPath (Join-Path $repoRoot "vendor\$name\LICENSE") -Destination (Join-Path $licenseDir "$name.txt") }
Copy-Item -LiteralPath (Join-Path $deps 'boost_1_86_0\LICENSE_1_0.txt') -Destination (Join-Path $licenseDir 'boost.txt')
Copy-Item -LiteralPath (Join-Path $deps 'spdlog-1.14.1\LICENSE') -Destination (Join-Path $licenseDir 'spdlog.txt')
Copy-Item -LiteralPath (Join-Path $repoRoot 'vendor\ncnn-LICENSE.txt') -Destination (Join-Path $licenseDir 'ncnn.txt')
Copy-Item -LiteralPath (Join-Path $repoRoot 'vendor\vulkan-loader-LICENSE.txt') -Destination (Join-Path $licenseDir 'vulkan-loader.txt')
Copy-Item -LiteralPath (Join-Path $repoRoot 'scripts\prepare-independent-deps.py') -Destination (Join-Path $licenseDir 'dependency-inputs.py')
@'
The native engine and AI wrapper libraries are built from the included source.
Dependencies and SHA-256 hashes are listed in runtime/licenses/dependency-inputs.py.
FFmpeg is the GPL-enabled BtbN 7.1.5 shared build. FFmpeg source:
https://github.com/BtbN/FFmpeg-Builds/tree/autobuild-2026-07-31-14-10
FFmpeg 7.1 shared source: https://github.com/FFmpeg/FFmpeg/tree/1fdbca85aa
The ffmpeg and ffprobe executables are the Gyan.dev 9.0.2 essentials build.
FFmpeg 9.0.2 source: https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz
ncnn 20240820: https://github.com/Tencent/ncnn/tree/20240820
Vulkan Loader 1.3.296.0: https://github.com/KhronosGroup/Vulkan-Loader/tree/v1.3.296
Bundled vulkan-1.dll SHA-256: 8644669401d2c080ea10cfea2ba8a7c63d76e10a4e7ae8246837df1dfd157ea8
Real-CUGAN executable: https://github.com/nihui/realcugan-ncnn-vulkan/tree/20220728
See LICENSE, NOTICE and runtime/licenses for license information.
'@ | Set-Content -LiteralPath (Join-Path $destination 'PROVENANCE.txt') -Encoding utf8
Get-ChildItem -LiteralPath $destination -Recurse -File | Where-Object { $_.Extension -in '.exe','.dll' } |
    Get-FileHash -Algorithm SHA256 | Select-Object Hash,@{Name='Path';Expression={ $_.Path.Substring($destination.Length + 1).Replace('\','/') }} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'SHA256.json') -Encoding utf8
Write-Output "Runtime prepared: $destination"
