from pathlib import Path
import concurrent.futures
import hashlib
import json
import tarfile
import urllib.request
import zipfile

root=Path(__file__).resolve().parents[1] / 'build/native-devtools'
# The exact Microsoft package catalog and digests are recorded with the source.
catalog=json.loads((Path(__file__).with_name('native-toolchain-packages.json')).read_text(encoding='utf-8'))
ids=[f'Microsoft.VC.14.44.17.14.{s}' for s in ('CRT.Headers.base','CRT.x64.Desktop.base','CRT.x64.OneCore.Desktop.base','Tools.HostX64.TargetX64.base','Tools.HostX64.TargetX64.Res.base')]
tasks=[]
for ident in ids:
    package=next(p for p in catalog['packages'] if p['id']==ident and p.get('language','en-US')=='en-US')
    for payload in package['payloads']:
        tasks.append((payload['url'],root/payload['fileName'],payload.get('sha256'),root/'msvc',True))
for name in ('microsoft.windows.sdk.cpp','microsoft.windows.sdk.cpp.x64'):
    filename=f'{name}.10.0.26100.4204.nupkg'
    tasks.append((f'https://api.nuget.org/v3-flatcontainer/{name}/10.0.26100.4204/{filename}',root/filename,{'microsoft.windows.sdk.cpp':'0653aa3dab7fc269fa8f3da0493d6bbddbfd79ab83ab94837c2f209a584596eb','microsoft.windows.sdk.cpp.x64':'ea4947d9a8ec715b78be40fb9bf3dcee91518098b7d7dd24b293fe7c021576c7'}[name],root/'sdk',False))

def prepare(task):
    url,path,digest,destination,strip=task
    if not path.exists():urllib.request.urlretrieve(url,path)
    actual=hashlib.file_digest(path.open('rb'),'sha256').hexdigest()
    if digest:
        assert actual.lower()==digest.lower(),(path,actual,digest)
    with zipfile.ZipFile(path) as archive:
        count=0
        for member in archive.infolist():
            name=member.filename
            if strip:
                if not name.startswith('Contents/'):continue
                name=name[len('Contents/'):]
            target=destination/name
            if member.is_dir():continue
            assert target.resolve().is_relative_to(destination.resolve())
            target.parent.mkdir(parents=True,exist_ok=True)
            data=archive.read(member)
            if not target.exists() or target.read_bytes()!=data:
                target.write_bytes(data)
            count+=1
    print(path.name,count,'files',actual,flush=True)

for task in tasks:
    prepare(task)

archives=[('https://ffmpeg.org/releases/ffmpeg-7.1.2.tar.xz',root/'ffmpeg-7.1.2.tar.xz','ffmpeg-7.1.2/',root/'ffmpeg','089bc60fb59d6aecc5d994ff530fd0dcb3ee39aa55867849a2bbc4e555f9c304'),
          ('https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/v1.3.296.tar.gz',root/'vulkan-headers.tar.gz','Vulkan-Headers-1.3.296/include/',root/'vulkan','e204e0b3c19f622d197df945737f5db913d6621830999b8578d34e80a7c90585')]
for url,path,prefix,destination,digest in archives:
    if not path.exists():urllib.request.urlretrieve(url,path)
    assert hashlib.file_digest(path.open('rb'),'sha256').hexdigest()==digest, path
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith(prefix) or not member.name.endswith('.h'):continue
            name=member.name[len(prefix):]
            target=destination/name
            assert target.resolve().is_relative_to(destination.resolve())
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    print(path.name,'headers prepared',flush=True)
(root/'ffmpeg/libavutil/avconfig.h').write_text('#define AV_HAVE_BIGENDIAN 0\n#define AV_HAVE_FAST_UNALIGNED 1\n')
print('Project-local compiler, SDK and headers ready.',flush=True)
