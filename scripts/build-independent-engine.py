"""Build the Windows engine and its small AI wrappers from source.

Inputs are staged in build/independent-deps by the dependency preparation step.
The compiler/SDK layout is prepared by prepare-native-toolchain.py.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
deps = Path(os.environ.get('AVUE_DEPS_DIR', root / 'build' / 'independent-deps')).resolve()
kit = Path(os.environ.get('AVUE_TOOLCHAIN_DIR', root / 'build' / 'native-devtools')).resolve()
vc = kit / 'msvc/VC/Tools/MSVC/14.44.35207'
bin_dir = vc / 'bin/Hostx64/x64'
sdk = kit / 'sdk/c'
include = sdk / 'Include/10.0.26100.0'
out = root / 'build' / 'native-independent'
out.mkdir(parents=True, exist_ok=True)
ncnn = deps / 'ncnn/ncnn-20240820-windows-vs2022-shared/x64'
ffmpeg = deps / 'ffmpeg-shared/ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-shared-7.1'
spdlog = deps / 'spdlog-1.14.1/include'
boost = deps / 'boost_1_86_0'
vulkan = kit / 'vulkan'
glslang = deps / 'glslang/bin/glslang.exe'
env = os.environ.copy()
env['PATH'] = str(bin_dir) + ';' + env.get('PATH', '')
env['INCLUDE'] = ';'.join(map(str, [vc/'include', include/'ucrt', include/'shared', include/'um', include/'winrt']))
env['LIB'] = ';'.join(map(str, [vc/'lib/x64', vc/'lib/onecore/x64', sdk/'ucrt/x64', sdk/'um/x64', ncnn/'lib', ffmpeg/'lib', out]))

def run(args):
    print('Building:', Path(str(args[0])).name, flush=True)
    subprocess.run([str(x) for x in args], cwd=out, env=env, check=True)

compiler = bin_dir/'cl.exe'
common = [compiler, '/nologo', '/std:c++17', '/O2', '/MD', '/EHsc', '/utf-8', '/DNOMINMAX', '/DWIN32']
common += ['/I'+str(p) for p in [ncnn/'include/ncnn', vulkan]]

def compile_wrapper(name, macro, files):
    src = root/'vendor'/name/'src'
    generated = out/name
    generated.mkdir(exist_ok=True)
    for shader in src.glob('*.comp'):
        if name == 'librealesrgan_ncnn_vulkan':
            for suffix, flags in [('', []), ('_fp16s', ['-DNCNN_fp16_storage=1']), ('_int8s', ['-DNCNN_fp16_storage=1', '-DNCNN_int8_storage=1'])]:
                run([glslang, *flags, '-V', '-s', '-x', '-o', generated/(shader.stem+suffix+'.spv.hex.h'), shader])
        else:
            content = shader.read_text(encoding='utf-8')
            content = content[content.index('#version'):]
            content = re.sub(r'\n +', '\n', content)
            data = ','.join(f'0x{x:02x}' for x in content.encode())
            (generated/(shader.name+'.hex.h')).write_text(f'static const char {shader.stem}_comp_data[] = {{{data}}};\n')
    dll = name.replace('_', '-')+'.dll'
    run(common + ['/LD', '/D'+macro, '/I'+str(src), '/I'+str(generated)] + [src/f for f in files] + ['/Fe:'+str(out/dll), '/link', 'ncnn.lib', '/IMPLIB:'+str(out/(Path(dll).stem+'.lib'))])

compile_wrapper('librealesrgan_ncnn_vulkan', 'LIBREALESRGAN_EXPORTS', ['realesrgan.cpp'])
compile_wrapper('librealcugan_ncnn_vulkan', 'LIBREALCUGAN_EXPORTS', ['realcugan.cpp'])
compile_wrapper('librife_ncnn_vulkan', 'LIBRIFE_EXPORTS', ['rife.cpp', 'warp.cpp'])

includes = [root/'engine/include', root/'engine/include/libvideo2x', ffmpeg/'include', spdlog]
includes += [root/'vendor'/name/'src' for name in ('librealesrgan_ncnn_vulkan', 'librealcugan_ncnn_vulkan', 'librife_ncnn_vulkan')]
run(common + ['/LD', '/DLIBVIDEO2X_EXPORTS'] + ['/I'+str(p) for p in includes] + list((root/'engine/core').glob('*.cpp')) + ['/Fe:'+str(out/'libvideo2x.dll'), '/link', 'ncnn.lib', 'avcodec.lib', 'avformat.lib', 'avutil.lib', 'avfilter.lib', 'swscale.lib', 'librealesrgan-ncnn-vulkan.lib', 'librealcugan-ncnn-vulkan.lib', 'librife-ncnn-vulkan.lib', 'kernel32.lib', 'advapi32.lib', 'shell32.lib', 'ole32.lib', '/IMPLIB:'+str(out/'libvideo2x.lib')])

definition = out/'vulkan-1.def'
definition.write_text('LIBRARY vulkan-1.dll\nEXPORTS\nvkCreateInstance\nvkDestroyInstance\nvkEnumeratePhysicalDevices\nvkGetPhysicalDeviceProperties\n')
run([bin_dir/'lib.exe', '/nologo', '/def:'+str(definition), '/machine:x64', '/out:'+str(out/'vulkan-1.lib')])
version = out/'generated/libvideo2x/version.h'
version.parent.mkdir(parents=True, exist_ok=True)
version.write_text('#define LIBVIDEO2X_VERSION_STRING "6.4.0+avue-stream.1"\n')
cli_includes = [root/'engine/include', root/'engine/cli/include', ffmpeg/'include', spdlog, boost, out/'generated']
cli_sources = list((root/'engine/cli/src').glob('*.cpp'))
boost_sources = list((boost/'libs/program_options/src').glob('*.cpp'))
run(common + ['/DBOOST_ALL_NO_LIB', '/DBOOST_PROGRAM_OPTIONS_SOURCE'] + ['/I'+str(p) for p in cli_includes] + cli_sources + boost_sources + ['/Fe:'+str(out/'avue-engine.exe'), '/link', 'libvideo2x.lib', 'ncnn.lib', 'avcodec.lib', 'avformat.lib', 'avutil.lib', 'vulkan-1.lib', 'kernel32.lib', 'advapi32.lib'])
print('Independent native engine built:', out, flush=True)
