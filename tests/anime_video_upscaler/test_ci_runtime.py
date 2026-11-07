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
        'scripts/fel-bake.py',
        'vendor/vulkan/vulkan-1.dll',
        'vendor/vulkan-loader-LICENSE.txt',
        'vendor/ncnn-LICENSE.txt',
        'vendor/dovi-tool-LICENSE.txt',
        'vendor/hdr10plus-tool-LICENSE.txt',
        'vendor/mkvtoolnix-GPL-2.0.txt',
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
        'build/independent-deps/ffmpeg-shared/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-shared-7.1/bin/ffmpeg.exe',
        'build/independent-deps/ffmpeg-shared/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-shared-7.1/LICENSE.txt',
        'build/independent-deps/ffmpeg-cli/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe',
        'build/independent-deps/ffmpeg-cli/ffmpeg-9.0.2-essentials_build/bin/ffprobe.exe',
        'build/independent-deps/ffmpeg-cli/ffmpeg-9.0.2-essentials_build/LICENSE',
        'build/independent-deps/realcugan/realcugan-ncnn-vulkan-20220728-windows/realcugan-ncnn-vulkan.exe',
        'build/independent-deps/realcugan/realcugan-ncnn-vulkan-20220728-windows/vcomp140.dll',
        'build/independent-deps/realcugan/realcugan-ncnn-vulkan-20220728-windows/LICENSE',
        'build/independent-deps/boost_1_86_0/LICENSE_1_0.txt',
        'build/independent-deps/spdlog-1.14.1/LICENSE',
        'build/independent-deps/dovi-tool/dovi_tool.exe',
        'build/independent-deps/hdr10plus-tool/hdr10plus_tool.exe',
        'build/independent-deps/mkvtoolnix/mkvtoolnix/mkvmerge.exe',
        'build/independent-deps/felbaker/felbaker.dll',
        'build/independent-deps/ffms2/ffms2-5.0-msvc/x64/ffms2.dll',
        'build/independent-deps/ffms2/ffms2-5.0-msvc/COPYING.GPLv3',
        'build/independent-deps/fel-python/python.exe',
        'build/independent-deps/fel-python/python312._pth',
        'build/independent-deps/vapoursynth/vapoursynth-82.dist-info/licenses/COPYING.LESSER',
        'models/example.param',
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')

    script = tmp_path / 'scripts/prepare-ci-runtime.ps1'
    # Use tiny fixture executables while retaining the production hash checks.
    fixture_digest = hashlib.sha256(b'fixture').hexdigest().upper()
    script_text = script.read_text(encoding='utf-8')
    for digest in ('8CD1262B4EDAC2DF219448D3450FF8E810C775E8025CDBE5E031D8F5EFD55DB8',
                   '04E4478208949EF89DDD90B4E6A8B2616ADD79E2EBD0379448329613970FCA1A',
                   '0DE358F33EB943AAEE1770600B8E2E6E8740DF834844C3B6C642DCF3E0E22100',
                   '2AD5D7A80C502C370ED533C426D1EA9D642AE686B08771A130E0C01C0C0FCD4D',
                   '970D3F8D170FD13A2DD8A7E8A7F56B0CE5A1DC7EEA79D1774BBB2C2CAB47E3F0'):
        assert digest in script_text
        script_text = script_text.replace(digest, fixture_digest)
    script.write_text(script_text, encoding='utf-8')
    command = ['pwsh', '-NoProfile', '-NonInteractive', '-File', str(script)]
    result = subprocess.run(command, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    dll = tmp_path / 'runtime/engine/vulkan-1.dll'
    digest = hashlib.sha256(dll.read_bytes()).hexdigest()
    assert digest == '8644669401d2c080ea10cfea2ba8a7c63d76e10a4e7ae8246837df1dfd157ea8'
    manifest = json.loads((tmp_path / 'runtime/SHA256.json').read_text(encoding='utf-8-sig'))
    assert any(item['Path'] == 'engine/vulkan-1.dll' and item['Hash'].lower() == digest for item in manifest)
    assert any(item['Path'] == 'hdr-metadata/dovi_tool.exe' and item['Hash'] == fixture_digest for item in manifest)
    assert (tmp_path / 'runtime/licenses/dovi-tool.txt').is_file()

    (tmp_path / 'build/independent-deps/dovi-tool/dovi_tool.exe').write_bytes(b'corrupted')
    result = subprocess.run(command + ['-DestinationRoot', str(tmp_path / 'bad-hdr-runtime')], text=True, capture_output=True)
    assert result.returncode != 0
    assert 'HDR metadata tool missing or invalid' in result.stderr

    (tmp_path / 'vendor/vulkan/vulkan-1.dll').write_bytes(b'corrupted')
    result = subprocess.run(command + ['-DestinationRoot', str(tmp_path / 'bad-runtime')], text=True, capture_output=True)
    assert result.returncode != 0
    assert 'Vulkan loader SHA-256 mismatch' in result.stderr
