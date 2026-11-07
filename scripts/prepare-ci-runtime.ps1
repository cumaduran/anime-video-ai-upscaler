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
foreach ($file in @((Join-Path $native 'avue-engine.exe'), (Join-Path $native 'libvideo2x.dll'), (Join-Path $ncnn 'bin\ncnn.dll'), (Join-Path $ffmpegCli 'bin\ffmpeg.exe'), (Join-Path $ffmpeg 'bin\ffmpeg.exe'), (Join-Path $cugan 'realcugan-ncnn-vulkan.exe'), $vulkan)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required build input missing: $file" }
}
if ((Get-FileHash -LiteralPath $vulkan -Algorithm SHA256).Hash -ne '8644669401D2C080EA10CFEA2BA8A7C63D76E10A4E7AE8246837DF1DFD157EA8') { throw 'Bundled Vulkan loader SHA-256 mismatch.' }
$engineDir = Join-Path $destination 'engine'
$ffmpegDir = Join-Path $destination 'ffmpeg'
$licenseDir = Join-Path $destination 'licenses'
$metadataDir = Join-Path $destination 'hdr-metadata'
$doviFFmpegDir = Join-Path $destination 'dovi-ffmpeg'
$felDir = Join-Path $destination 'fel-baker'
$felPython = Join-Path $felDir 'python'
New-Item -ItemType Directory -Force -Path $engineDir,$ffmpegDir,$licenseDir,$metadataDir,$doviFFmpegDir,$felDir,$felPython | Out-Null
foreach ($item in @(@('dovi-tool\dovi_tool.exe','dovi_tool.exe','8CD1262B4EDAC2DF219448D3450FF8E810C775E8025CDBE5E031D8F5EFD55DB8'),
                     @('hdr10plus-tool\hdr10plus_tool.exe','hdr10plus_tool.exe','04E4478208949EF89DDD90B4E6A8B2616ADD79E2EBD0379448329613970FCA1A'),
                     @('mkvtoolnix\mkvtoolnix\mkvmerge.exe','mkvmerge.exe','0DE358F33EB943AAEE1770600B8E2E6E8740DF834844C3B6C642DCF3E0E22100'))) {
    $source = Join-Path $deps $item[0]
    if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $item[2]) { throw "HDR metadata tool missing or invalid: $source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $metadataDir $item[1])
}
Copy-Item -LiteralPath (Join-Path $repoRoot 'vendor\dovi-tool-LICENSE.txt') -Destination (Join-Path $licenseDir 'dovi-tool.txt')
Copy-Item -LiteralPath (Join-Path $repoRoot 'vendor\hdr10plus-tool-LICENSE.txt') -Destination (Join-Path $licenseDir 'hdr10plus-tool.txt')
Copy-Item -LiteralPath (Join-Path $repoRoot 'vendor\mkvtoolnix-GPL-2.0.txt') -Destination (Join-Path $licenseDir 'mkvtoolnix-GPL-2.0.txt')
foreach ($item in @(@('felbaker\felbaker.dll','felbaker.dll','2AD5D7A80C502C370ED533C426D1EA9D642AE686B08771A130E0C01C0C0FCD4D'),
                     @('ffms2\ffms2-5.0-msvc\x64\ffms2.dll','ffms2.dll','970D3F8D170FD13A2DD8A7E8A7F56B0CE5A1DC7EEA79D1774BBB2C2CAB47E3F0'))) {
    $source = Join-Path $deps $item[0]
    if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $item[2]) { throw "FEL tool missing or invalid: $source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $felDir $item[1])
}
Copy-Item -Path (Join-Path $deps 'fel-python\*') -Destination $felPython -Recurse
$sitePackages = Join-Path $felPython 'Lib\site-packages'
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
Copy-Item -Path (Join-Path $deps 'vapoursynth\*') -Destination $sitePackages -Recurse
Add-Content -LiteralPath (Join-Path $felPython 'python312._pth') -Value 'Lib/site-packages' -Encoding ascii
Copy-Item -LiteralPath (Join-Path $repoRoot 'scripts\fel-bake.py') -Destination $felDir
Copy-Item -LiteralPath (Join-Path $deps 'ffms2\ffms2-5.0-msvc\COPYING.GPLv3') -Destination (Join-Path $licenseDir 'ffms2-and-felbaker-GPLv3.txt')
Copy-Item -LiteralPath (Join-Path $deps 'vapoursynth\vapoursynth-82.dist-info\licenses\COPYING.LESSER') -Destination (Join-Path $licenseDir 'vapoursynth-LGPL.txt')
Get-ChildItem -LiteralPath (Join-Path $ffmpeg 'bin') -Filter '*.dll' -File | Copy-Item -Destination $engineDir
Get-ChildItem -LiteralPath (Join-Path $ffmpeg 'bin') -Filter '*.dll' -File | Copy-Item -Destination $doviFFmpegDir
Copy-Item -LiteralPath (Join-Path $ffmpeg 'bin\ffmpeg.exe') -Destination (Join-Path $doviFFmpegDir 'ffmpeg.exe')
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
HDR10+ metadata tool 1.7.2: https://github.com/quietvoid/hdr10plus_tool/tree/1.7.2
Dolby Vision metadata tool 2.3.4: https://github.com/quietvoid/dovi_tool/tree/2.3.4
MKVToolNix 101.0: https://mkvtoolnix.download/sources/mkvtoolnix-101.0.tar.xz
FelBaker 1.0.0: https://github.com/bbeny123/felbaker/tree/1.0.0
FFMS2 5.0: https://github.com/FFMS/ffms2/tree/5.0
VapourSynth R82: https://github.com/vapoursynth/vapoursynth/tree/R82
Python 3.12.10: https://www.python.org/downloads/release/python-31210/
See LICENSE, NOTICE and runtime/licenses for license information.
'@ | Set-Content -LiteralPath (Join-Path $destination 'PROVENANCE.txt') -Encoding utf8
Get-ChildItem -LiteralPath $destination -Recurse -File | Where-Object { $_.Extension -in '.exe','.dll' } |
    Get-FileHash -Algorithm SHA256 | Select-Object Hash,@{Name='Path';Expression={ $_.Path.Substring($destination.Length + 1).Replace('\','/') }} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'SHA256.json') -Encoding utf8
Write-Output "Runtime prepared: $destination"
