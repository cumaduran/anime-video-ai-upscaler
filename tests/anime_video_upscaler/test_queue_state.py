from dataclasses import replace
import json

import pytest

from anime_video_upscaler.config import Settings, config_dir
from anime_video_upscaler.pipeline import Media, lossless_working_bytes
from anime_video_upscaler.queue_state import load_queue, save_queue
from anime_video_upscaler.runner import Job


def test_queue_roundtrip_does_not_resume_interrupted_or_completed_work(tmp_path):
    source = tmp_path / 'anime.mkv'
    original = Job(source, replace(Settings(), quality=17), state='ai', percent=65,
                   output=tmp_path/'output.mkv', settings_locked=True, output_name='my-output')
    done = Job(tmp_path / 'done.mkv', Settings(), state='done', percent=100, output=tmp_path/'done-out.mkv')
    path = tmp_path / 'queue.json'
    save_queue(path, [original, done])
    restored = load_queue(path)
    assert restored[0].id == original.id
    assert restored[0].state == 'interrupted' and restored[0].percent == 0
    assert restored[0].output is None and restored[0].settings.quality == 17
    assert restored[0].settings_locked and restored[0].output_name == 'my-output'
    assert restored[1].state == 'done' and restored[1].output == done.output


def test_queue_restores_source_timing_verification_as_interrupted(tmp_path):
    path = tmp_path / 'queue.json'
    save_queue(path, [Job(tmp_path / 'source.mkv', Settings(), state='verify_source_timing'),
                      Job(tmp_path / 'waiting.mkv', Settings())])
    restored = load_queue(path)
    assert [job.state for job in restored] == ['interrupted', 'queued']


def test_queue_rejects_invalid_snapshot_without_altering_original(tmp_path):
    path = tmp_path / 'queue.json'
    path.write_text(json.dumps({'schema':1,'jobs':[{'source':'relative.mkv','state':'queued'}]}), encoding='utf-8')
    original = path.read_bytes()
    with pytest.raises(ValueError):
        load_queue(path)
    assert path.read_bytes() == original


def test_window_restores_waiting_and_interrupted_jobs_without_starting(app, tmp_path, monkeypatch):
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: {'ffmpeg':'', 'ffprobe':'', 'video2x':''})
    source = tmp_path / 'one.mkv'; source.touch()
    window = MainWindow()
    window.add_paths([str(source)])
    job = window.runner.jobs[0]
    job.state = 'ai'
    job.settings = replace(job.settings, quality=17)
    job.settings_locked = True
    window.close()
    reopened = MainWindow()
    assert len(reopened.runner.jobs) == 1
    restored = reopened.runner.jobs[0]
    assert restored.state == 'interrupted' and restored.settings.quality == 17
    # Restoring still does not start processing. The explicit Start action is now
    # available for the selected interrupted video, just like Retry selected.
    assert not reopened.runner.busy and reopened.start_button.isEnabled()
    assert restored.state == 'interrupted' and reopened.runner.process is None
    reopened.table.selectRow(0)
    reopened.retry_selected()
    assert restored.state == 'queued' and reopened.start_button.isEnabled()
    reopened.close()


def test_window_retains_unreadable_queue(app, tmp_path, monkeypatch):
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: {'ffmpeg':'', 'ffprobe':'', 'video2x':''})
    path = config_dir()/'queue.json'
    path.parent.mkdir(parents=True)
    path.write_text('{bad', encoding='utf-8')
    window = MainWindow()
    assert window._queue_error
    window.close()
    assert path.read_text(encoding='utf-8') == '{bad'


def test_lossless_reference_covers_scaling_and_unknown_media():
    media = Media(640,480,10,24)
    assert lossless_working_bytes(replace(Settings(),scale=2),media) == 4 * 240 * 640 * 480 * 5
    assert lossless_working_bytes(replace(Settings(),engine='realcugan',scale=2),media) == 4 * 240 * 640 * 480 * 9
    assert lossless_working_bytes(replace(Settings(),engine='rife',fps_multiplier=2),media) == 4 * 240 * 640 * 480 * 3
    assert lossless_working_bytes(replace(Settings(),engine='none'),media) is None
    assert lossless_working_bytes(Settings(),replace(media,duration=0)) is None


def test_queue_rejects_job_id_path_traversal(tmp_path):
    source = tmp_path / 'one.mkv'
    path = tmp_path / 'queue.json'
    save_queue(path, [Job(source, Settings())])
    data = json.loads(path.read_text(encoding='utf-8'))
    data['jobs'][0]['id'] = '../../outside'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='job ID'):
        load_queue(path)


def test_temporarily_invalid_video_settings_do_not_overwrite_saved_queue(app,tmp_path,monkeypatch):
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: {'ffmpeg':'', 'ffprobe':'', 'video2x':''})
    source=tmp_path/'video.mkv'; source.touch()
    window=MainWindow(); window.add_paths([str(source)])
    window._save_queue()
    path=config_dir()/'queue.json'
    previous=path.read_bytes()
    window.sync_checkbox.setChecked(False)
    window.store.update_many(engine='realcugan',cugan_model='models-pro',scale=4)
    window._save_queue()
    assert path.read_bytes()==previous
    window.close()
    assert path.read_bytes()==previous


def test_retry_during_active_queue_preserves_settings_and_explains_state(app, tmp_path, monkeypatch):
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: {'ffmpeg':'', 'ffprobe':'', 'video2x':''})
    paths = [tmp_path / 'a.mkv', tmp_path / 'b.mkv']
    for path in paths: path.touch()
    window = MainWindow()
    window.add_paths([str(path) for path in paths])
    running, failed = window.runner.jobs
    running.state = 'stream'
    failed.state = 'failed'
    failed.percent = 90
    failed.settings = replace(failed.settings, quality=23)
    window.runner.busy = True
    window.runner.current = running
    window.table.selectRow(1)
    window.retry_selected()
    assert failed.state == 'queued' and failed.percent == 0 and failed.settings.quality == 23
    assert 'queued again' in window.status_label.text()
    window.runner.current = None
    window.runner.busy = False
    window.close()


def test_resume_checkpoint_changes_when_model_or_core_changes(app, tmp_path, monkeypatch):
    from anime_video_upscaler.runner import QueueRunner
    engine_dir = tmp_path / "engine"
    model_dir = engine_dir / "models" / "realesrgan"
    model_dir.mkdir(parents=True)
    exe = engine_dir / "avue-engine.exe"
    exe.write_bytes(b"engine")
    core = engine_dir / "libvideo2x.dll"
    core.write_bytes(b"first core")
    model = model_dir / "model.bin"
    model.write_bytes(b"first model")
    source = tmp_path / "input.mkv"
    source.write_bytes(b"source")
    runner = QueueRunner()
    runner.tools = {"video2x": str(exe)}
    runner.current = Job(source, Settings(), output=tmp_path / "out.mkv")
    runner.media = Media(96, 64, 1, 12)
    runner._resume_total_frames = 12
    monkeypatch.setattr(runner, "_resume_next", lambda: None)
    runner._prepare_resume()
    first = runner._resume_dir
    model.write_bytes(b"second model")
    runner._prepare_resume()
    second = runner._resume_dir
    core.write_bytes(b"second core")
    runner._prepare_resume()
    assert len({first, second, runner._resume_dir}) == 3
