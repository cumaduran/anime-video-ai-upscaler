"""Fetch and verify pinned inputs for the independent Windows engine build."""
from pathlib import Path
import hashlib
import shutil
import tarfile
import urllib.request
import zipfile

root = Path(__file__).resolve().parents[1] / 'build' / 'independent-deps'
root.mkdir(parents=True, exist_ok=True)
inputs = {
    'ncnn.zip': ('https://github.com/Tencent/ncnn/releases/download/20240820/ncnn-20240820-windows-vs2022-shared.zip', 'af0b8730971181d6bfe1b1055d1c79ad6773e2c39d48187ea1a58c5ad3c48618'),
    'ffmpeg-shared.zip': ('https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-07-31-14-10/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-shared-7.1.zip', '3e61e96b44bce30f0fad9fc31955be7fe4d6690d6a5a2b65c62494e262f8369e'),
    'ffmpeg-cli.zip': ('https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip', '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'),
    'glslang.zip': ('https://github.com/KhronosGroup/glslang/releases/download/16.6.0/glslang-16.6.0-windows-x86_64-release.zip', '82bf434e69b9bb4829de7e2b4bc2c5e7a7861e53d66cf75e5cc70f5f694a8d9b'),
    'vulkan-runtime.zip': ('https://sdk.lunarg.com/sdk/download/1.3.296.0/windows/vulkan-runtime-components.zip', 'e0429f91cf356fe99e381f7d22e568ac81945669f1be1772d933a0d189230452'),
    'boost.tar.gz': ('https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.gz', '2575e74ffc3ef1cd0babac2c1ee8bdb5782a0ee672b1912da40e5b4b591ca01f'),
    'spdlog.tar.gz': ('https://github.com/gabime/spdlog/archive/refs/tags/v1.14.1.tar.gz', '1586508029a7d0670dfcb2d97575dcdc242d3868a259742b69f100801ab4e16b'),
    'realcugan.zip': ('https://github.com/nihui/realcugan-ncnn-vulkan/releases/download/20220728/realcugan-ncnn-vulkan-20220728-windows.zip', 'c6e08d46c11704b1e3a1ada9ddd591cb5005f52f132136c8633ba25def400e01'),
}
for name, (url, digest) in inputs.items():
    path = root/name
    if not path.exists():
        print('Downloading', name, flush=True)
        with urllib.request.urlopen(url, timeout=120) as source, path.open('wb') as dest:
            shutil.copyfileobj(source, dest)
    with path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise RuntimeError(f'SHA-256 mismatch: {name}')
    print('Verified', name, flush=True)
    if name.endswith('.zip'):
        destination = root/name.removesuffix('.zip')
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                if not (destination/item.filename).resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError('Unsafe ZIP path')
            archive.extractall(destination)
    else:
        with tarfile.open(path, 'r:gz') as archive:
            members = archive.getmembers()
            if name == 'boost.tar.gz':
                members = [m for m in members if m.name.startswith(('boost_1_86_0/boost/', 'boost_1_86_0/libs/program_options/src/')) or m.name == 'boost_1_86_0/LICENSE_1_0.txt']
            archive.extractall(root, members=members, filter='data')
