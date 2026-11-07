"""Checks for actual presentation timestamps through the AI pipeline."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from anime_video_upscaler.config import Settings
from anime_video_upscaler.runner import QueueRunner
from test_integration import wait_runner

pytestmark = pytest.mark.integration


def _run(args):
    return subprocess.run([str(x) for x in args], check=True, capture_output=True,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout


def _timestamps(ffprobe, path):
    data = json.loads(_run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                            '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', path]))
    return [float(frame['best_effort_timestamp_time']) for frame in data['frames']]


@pytest.mark.parametrize('engine,hdr', [('realcugan', False), ('realesrgan', False),
                                        ('libplacebo', False), ('realcugan', True)])
@pytest.mark.gpu
def test_vfr_ai_preserves_frame_timestamps(app, tmp_path, engine, hdr):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    if not all(path.is_file() for path in (ffmpeg, ffprobe, native)):
        pytest.skip('Real media tools and GPU engine are required')
    source = tmp_path / 'variable.mkv'
    vf = 'setpts=PTS+floor(N/2)*0.2/TB'
    encoder = ['-c:v', 'libx264', '-preset', 'ultrafast']
    if hdr:
        vf += ',setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv'
        encoder = ['-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le', '-x265-params',
                   'log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:'
                   'master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400']
    _run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=128x96:r=5:d=1.2',
          '-itsoffset', '0.15', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-vf', vf,
          '-fps_mode', 'passthrough', *encoder, '-c:a', 'aac', source])
    original = _timestamps(ffprobe, source)
    assert len({round(b - a, 3) for a, b in zip(original, original[1:])}) > 1
    settings = replace(Settings(), engine=engine, cugan_model='models-nose', noise=0, scale=2, high_precision=hdr,
                       codec='hevc_nvenc' if hdr else 'libx264', pixel_format='p010le' if hdr else 'yuv420p',
                       preset='p4' if hdr else 'ultrafast', target='native', container='mkv',
                       output_dir=str(tmp_path), copy_subtitles=False, audio='copy')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state in ('done', 'done_warning'), job.error + '\n' + '\n'.join(logs[-15:])
    assert 'Presentation timestamps verified for every output frame.' in logs
    actual = _timestamps(ffprobe, job.output)
    assert len(actual) == len(original)
    assert all(abs((a - actual[0]) - (b - original[0])) < .002 for a, b in zip(actual, original))


@pytest.mark.parametrize('output_frames,audio_start,expected_error', [
    ('0.0\n0.3\n0.6\n', '0.15', 'timestamps changed'),
    ('0.0\n0.2\n0.6\n', '0.20', 'audio or subtitle timing changed'),
    ('0.0\n0.2\n', '0.15', 'frame count changed')])
def test_vfr_verifier_refuses_timing_or_frame_loss(app, tmp_path, output_frames, audio_start, expected_error):
    runner = QueueRunner(); runner.work = tmp_path
    runner.current = type('Job', (), {'settings': replace(Settings(), audio='copy', copy_subtitles=False)})()
    from anime_video_upscaler.pipeline import Media
    runner.media = Media(128, 96, 1, 5)
    runner.probe_data = {'streams': [{'index': 0, 'codec_type': 'video', 'start_time': '0'},
                                     {'index': 1, 'codec_type': 'audio', 'start_time': '0.15'}]}
    runner._vfr_output_probe = {'streams': [{'index': 0, 'codec_type': 'video', 'start_time': '0'},
                                            {'index': 1, 'codec_type': 'audio', 'start_time': audio_start}]}
    (tmp_path / 'vfr-source.csv').write_text('0.0\n0.2\n0.6\n', encoding='utf-8')
    (tmp_path / 'vfr-output.csv').write_text(output_frames, encoding='utf-8')
    with pytest.raises(ValueError, match=expected_error):
        runner._verify_vfr_timestamps()


def test_rife_checks_real_timestamps_even_if_container_reports_constant_rate(app, tmp_path):
    from anime_video_upscaler.pipeline import Media
    runner = QueueRunner(); runner.work = tmp_path; runner.media = Media(128, 96, 1, 5)
    runner.current = type('Job', (), {'settings': replace(Settings(), engine='rife')})()
    (tmp_path / 'rife-source.csv').write_text('0.0\n0.2\n0.6\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Variable-frame-rate RIFE'):
        runner._verify_rife_source_cfr()


def test_vfr_verifier_rejects_changed_final_frame_duration(app, tmp_path):
    from anime_video_upscaler.pipeline import Media
    runner = QueueRunner(); runner.work = tmp_path; runner.media = Media(128, 96, 1, 5)
    runner.current = type('Job', (), {'settings': replace(Settings(), audio='off', copy_subtitles=False)})()
    (tmp_path / 'vfr-source.csv').write_text('0.0,0.2\n0.2,0.4\n', encoding='utf-8')
    (tmp_path / 'vfr-output.csv').write_text('0.0,0.2\n0.2,0.2\n', encoding='utf-8')
    with pytest.raises(ValueError, match='final frame duration changed'):
        runner._verify_vfr_timestamps()
