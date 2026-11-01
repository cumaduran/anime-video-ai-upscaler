from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
import os
import subprocess
import sys

import pytest
from PySide6.QtCore import QProcess
from anime_video_upscaler.config import Settings
from anime_video_upscaler.diagnostics import failure_summary
from anime_video_upscaler.disk_policy import GIB, RESERVE_BYTES, contains_disk_error, disk_error_text, preflight_threshold
from anime_video_upscaler.i18n import Translator
from anime_video_upscaler.pipeline import Media, lossless_working_bytes
from anime_video_upscaler.runner import Job, QueueRunner


def test_long_ai_job_fails_preflight_with_user_observed_space():
    media = Media(640,480,1488.656,24000/1001)
    reference = lossless_working_bytes(Settings(),media)
    threshold = preflight_threshold(reference)
    assert reference / GIB == pytest.approx(204.2,abs=0.2)
    assert 4.5 * GIB < threshold < 20 * GIB
    assert 180 * GIB > threshold
    message = disk_error_text(int(4.5*GIB),threshold,Path('D:/output'))
    assert 'Insufficient disk space' in message and 'D:' in message
    assert 'not a guarantee' in message


def test_disk_error_takes_priority_over_unrelated_crash_advice():
    log = 'Error encoding/writing frame: No space left on device\nVideo processing failed with error code -28'
    message = failure_summary('ai exited with code -1073741819. See the job log.',Translator('tr'),log)
    assert 'yeterli boş alan yok' in message
    assert 'kurtarma' not in message.lower()
    assert contains_disk_error('No space left on device')
    assert not contains_disk_error('Disk space warning: monitor free space')


def test_unexpected_engine_crash_is_not_assumed_to_be_successful_shutdown():
    message = failure_summary('ai exited with code -1073741819',Translator('tr'),'Video processing failed')
    assert 'beklenmedik' in message.lower() and 'kurtarma' not in message.lower()
    success = failure_summary('ai exited with code -1073741819',Translator('tr'),
                              'Video processed successfully\nTotal frames processed: 10')
    assert 'kurtarma' in success.lower()


def test_disk_watch_stops_running_child_before_reserve_exhausted(app,tmp_path,monkeypatch):
    runner=QueueRunner()
    runner.work=tmp_path
    runner._temp=object()
    runner.current=Job(tmp_path/'input.mkv',Settings(),output=tmp_path/'out.mkv')
    class FakeProcess:
        killed=False
        def state(self): return QProcess.ProcessState.Running
        def kill(self): self.killed=True
    process=FakeProcess(); runner.process=process
    monkeypatch.setattr('anime_video_upscaler.runner.shutil.disk_usage',lambda _: SimpleNamespace(free=RESERVE_BYTES-1))
    runner._check_disk_space()
    assert process.killed and 'Insufficient disk space' in runner._timeout_error
    # This test supplies a fake temp directory and process; prevent QObject teardown from touching it.
    runner._temp=None; runner.process=None; runner.current=None


@pytest.mark.skipif(sys.platform != 'win32',reason='Windows child process error mode')
def test_windows_child_crash_returns_without_blocking_dialog():
    code = """import ctypes,subprocess,sys
from anime_video_upscaler.windows_errors import configure_windows_error_mode
assert configure_windows_error_mode()
p=subprocess.run([sys.executable,'-c','import ctypes; ctypes.string_at(0)'],capture_output=True,timeout=15)
assert p.returncode != 0
"""
    env = dict(os.environ)
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]/'app')
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=25,env=env)
    assert result.returncode == 0, result.stderr


def test_disk_failure_never_triggers_shutdown_recovery(app, tmp_path, monkeypatch):
    runner=QueueRunner()
    settings=replace(Settings(),recover_shutdown=True)
    runner.current=Job(tmp_path/'input.mkv',settings,output=tmp_path/'output.mkv')
    runner.work=tmp_path
    runner.stage='ai'
    runner._normalized_frames=runner._ai_reported_frames=10
    runner._ai_success=True
    runner._disk_error_seen=True
    runner.process=SimpleNamespace(deleteLater=lambda:None)
    runner._read=lambda:None
    runner._consume=lambda line:None
    finished=[]
    runner._finish=lambda state,error='':finished.append((state,error))
    runner._verify_ai=lambda:pytest.fail('Recovery must never follow a disk error')
    monkeypatch.setattr('anime_video_upscaler.runner.shutil.disk_usage',lambda _: SimpleNamespace(free=0))
    runner._exited(-1073741819,QProcess.ExitStatus.CrashExit)
    assert finished and finished[0][0]=='failed' and 'Insufficient disk space' in finished[0][1]
    runner.current=None
