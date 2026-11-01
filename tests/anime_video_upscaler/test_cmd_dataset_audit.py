"""Offline dataset audit checks with small, generated videos; no copyrighted media."""
import csv
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from cmd_dataset_audit import audit, main


@pytest.fixture
def dataset(tmp_path):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip('Set ANIME_TEST_FFMPEG and ANIME_TEST_FFPROBE')
    root = tmp_path/'dataset'
    entries = [('train', 'series_a', 'red'), ('val', 'series_b', 'blue'), ('test', 'series_c', 'green')]
    for split, series, color in entries:
        folder=root/split/series
        folder.mkdir(parents=True)
        subprocess.run([str(ffmpeg),'-v','error','-f','lavfi','-i',f'color={color}:s=96x64:r=2:d=1',
                        '-c:v','ffv1',str(folder/'clip.mkv')],check=True,capture_output=True,timeout=30,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    with (root/'provenance.csv').open('w',encoding='utf-8',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(['series','source','rights','training_permitted'])
        writer.writerows([(series,'self-created synthetic test','test fixture','yes') for _,series,_ in entries])
    return root,ffprobe


def test_dataset_audit_detects_leakage_and_never_overwrites(dataset,tmp_path):
    root,ffprobe=dataset
    result=audit(root,ffprobe,hashing=True)
    assert len(result['files'])==3 and len({r['split'] for r in result['files']})==3
    assert all(len(r['sha256'])==64 for r in result['files'])
    assert any('below 1080p' in w for w in result['warnings'])
    out=tmp_path/'audit.json'
    assert main(['--root',str(root),'--ffprobe',str(ffprobe),'--output',str(out),'--hash'])==0
    original=out.read_bytes()
    assert main(['--root',str(root),'--ffprobe',str(ffprobe),'--output',str(out)])==1
    assert out.read_bytes()==original
    assert main(['--root',str(root),'--ffprobe',str(ffprobe),'--output',str(root/'unsafe.json')])==1
    shutil.copyfile(root/'train'/'series_a'/'clip.mkv',root/'val'/'series_b'/'clip.mkv')
    with pytest.raises(ValueError,match='Identical files'):
        audit(root,ffprobe,hashing=True)


def test_dataset_audit_requires_declared_permission(dataset):
    root,ffprobe=dataset
    path=root/'provenance.csv'
    text=path.read_text(encoding='utf-8')
    path.write_text(text.replace('series_b,self-created synthetic test,test fixture,yes',
                                'series_b,self-created synthetic test,test fixture,no'),encoding='utf-8')
    with pytest.raises(ValueError,match='training permission'):
        audit(root,ffprobe)
