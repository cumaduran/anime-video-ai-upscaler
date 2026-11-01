"""Release runtime staging must work without a Vulkan download."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='The release runtime is built on Windows')
def test_runtime_bundles_verified_vulkan_loader(tmp_path):
    source = Path(__file__).resolve().parents[2]
    for relative in (
        'scripts/prepare-ci-runtime.ps1',
        'scripts/prepare-independent-deps.py',
        'vendor/vulkan/vulkan-1.dll',
        'vendor/vulkan-loader-LICENSE.txt',
        'vendor/ncnn-LICENSE.txt',
        'vendor/librealesrgan_ncnn_vulkan/LICENSE',
        'vendor/librealcugan_ncnn_vulkan/LICENSE',
        'vendor/librife_ncnn_vulkan/LICENSE',
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)

    for relative in (
        'build/native-independent/avue-engine.exe',
        'build/native-independent/libvideo2x.dll',
        'build/native-independent/librealesrgan-ncnn-vulkan.dll',
        'build/native-independent/librealcugan-ncnn-vulkan.dll',
        'build/native-independent/librife-ncnn-vulkan.dll',
        'build/independent-deps/ncnn/ncnn-20240820-windows-vs2022-shared/x64/bin/ncnn.dll',
        'build/independent-deps/ffmpeg-shared/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-shared-7.1/bin/avcodec-61.dll',
        'build/independent-deps/ffmpeg-shared/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-shared-7.1/LICENSE.txt',
        'build/independent-deps/ffmpeg-cli/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe',
        'build/independent-deps/ffmpeg-cli/ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe',
        'build/independent-deps/ffmpeg-cli/ffmpeg-9.0.2-essentials_build/LICENSE',
        'build/independent-deps/realcugan/realcugan-ncnn-vulkan-20220728-windows/realcugan-ncnn-vulkan.exe',
        'build/independent-deps/realcugan/realcugan-ncnn-vulkan-20220728-windows/vcomp140.dll',
        'build/independent-deps/realcugan/realcugan-ncnn-vulkan-20220728-windows/LICENSE',
        'build/independent-deps/boost_1_86_0/LICENSE_1_0.txt',
        'build/independent-deps/spdlog-1.14.1/LICENSE',
        'models/example.param',
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')

    script = tmp_path / 'scripts/prepare-ci-runtime.ps1'
    command = ['pwsh', '-NoProfile', '-NonInteractive', '-File', str(script)]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    dll = tmp_path / 'runtime/engine/vulkan-1.dll'
    digest = hashlib.sha256(dll.read_bytes()).hexdigest()
    assert digest == '8644669401d2c080ea10cfea2ba8a7c63d76e10a4e7ae8246837df1dfd157ea8'
    manifest = json.loads((tmp_path / 'runtime/SHA256.json').read_text(encoding='utf-8-sig'))
    assert any(item['Path'] == 'engine/vulkan-1.dll' and item['Hash'].lower() == digest for item in manifest)

    (tmp_path / 'vendor/vulkan/vulkan-1.dll').write_bytes(b'corrupted')
    result = subprocess.run(command + ['-DestinationRoot', str(tmp_path / 'bad-runtime')], text=True, capture_output=True)
    assert result.returncode != 0
    assert 'Vulkan loader SHA-256 mismatch' in result.stderr
