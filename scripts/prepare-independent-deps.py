"""Fetch and verify pinned inputs for the independent Windows engine build."""
from pathlib import Path
import hashlib
import shutil
import subprocess
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
    'boost.tar.gz': ('https://archives.boost.io/release/1.86.0/source/boost_1_86_0.tar.gz', '2575e74ffc3ef1cd0babac2c1ee8bdb5782a0ee672b1912da40e5b4b591ca01f'),
    'spdlog.tar.gz': ('https://github.com/gabime/spdlog/archive/refs/tags/v1.14.1.tar.gz', '1586508029a7d0670dfcb2d97575dcdc242d3868a259742b69f100801ab4e16b'),
    'realcugan.zip': ('https://github.com/nihui/realcugan-ncnn-vulkan/releases/download/20220728/realcugan-ncnn-vulkan-20220728-windows.zip', 'c6e08d46c11704b1e3a1ada9ddd591cb5005f52f132136c8633ba25def400e01'),
    'dovi-tool.zip': ('https://github.com/quietvoid/dovi_tool/releases/download/2.3.4/dovi_tool-2.3.4-x86_64-pc-windows-msvc.zip', '0d9733d311dfe49f9dca0c350f7e6d978182673d333c6f07f7365daf9e6c81eb'),
    'hdr10plus-tool.zip': ('https://github.com/quietvoid/hdr10plus_tool/releases/download/1.7.2/hdr10plus_tool-1.7.2-x86_64-pc-windows-msvc.zip', '82b2d560073941b14c6511a431f429e33e134e5caefb60d7e8f6f6e6da8e16ba'),
    'mkvtoolnix.zip': ('https://mkvtoolnix.download/windows/releases/101.0/mkvtoolnix-64-bit-101.0.zip', '59a807b45b34ac524dfdef61edcf0642ee9bd6b08c130cfc078532c362569abc'),
    'felbaker.zip': ('https://github.com/bbeny123/felbaker/releases/download/1.0.0/felbaker-win-x64-avx2.zip', 'd98be96bc8f5e68785c0089fc6eab9e8a678f2bff3bb1cd25684d37c2f1adb79'),
    'ffms2.7z': ('https://github.com/FFMS/ffms2/releases/download/5.0/ffms2-5.0-msvc.7z', 'e867a3df7262865107df40f230f5b8e1455905eba9b8852e6f35b1227537caeb'),
    'fel-python.zip': ('https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip', '4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3'),
    'vapoursynth.zip': ('https://files.pythonhosted.org/packages/09/d7/f67e68a7bf35dc24bdff34b30d14baee5b726790c0bed8cba449790dbc33/vapoursynth-82-cp312-abi3-win_amd64.whl', 'dbde9930d4e85de4eb220e05a0314edb7469b9016c5c58a279844b90da4a32b1'),
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
    elif name.endswith('.7z'):
        seven_zip = shutil.which('7z') or str(Path(r'C:\Program Files\7-Zip\7z.exe'))
        if not Path(seven_zip).is_file():
            raise RuntimeError('7-Zip is required to extract the verified FFMS2 release')
        subprocess.run([seven_zip, 'x', '-y', f'-o{root / "ffms2"}', str(path)], check=True)
    else:
        with tarfile.open(path, 'r:gz') as archive:
            members = archive.getmembers()
            if name == 'boost.tar.gz':
                members = [m for m in members if m.name.startswith(('boost_1_86_0/boost/', 'boost_1_86_0/libs/program_options/src/')) or m.name == 'boost_1_86_0/LICENSE_1_0.txt']
            archive.extractall(root, members=members, filter='data')
