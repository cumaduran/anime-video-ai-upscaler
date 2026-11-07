"""Real local tools, synthetic media only. Opt in with ANIME_TEST_FFMPEG/FFPROBE."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage

from anime_video_upscaler.config import FACTORY_PRESETS, Settings
from anime_video_upscaler.runner import QueueRunner
from anime_video_upscaler.pipeline import Media

pytestmark=pytest.mark.integration


@pytest.mark.parametrize("output_duration,accepted", [(1.0, True), (0.5, False)])
def test_packet_timing_verification_accepts_matching_video_and_rejects_truncation(app, monkeypatch, output_duration, accepted):
    runner = QueueRunner()
    runner.current = type("Job", (), {"settings": Settings()})()
    runner.media = Media(96, 64, 1.5, 10)
    runner.stage = "verify_source_timing"
    runner._timing_start, runner._timing_end = float("inf"), float("-inf")
    runner._verified_output_duration = output_duration
    published = []
    monkeypatch.setattr(runner, "_publish_verified", lambda: published.append(True))
    # Packet order can differ from presentation order when B-frames are present.
    for line in ("0.200000,0.100000", "0.000000,0.100000", "0.900000,0.100000"):
        runner._consume(line)
    if accepted:
        runner._advance()
        assert published == [True]
    else:
        with pytest.raises(ValueError, match="duration differs"):
            runner._advance()
        assert not published


def test_hdr_static_metadata_warns_when_encoder_cannot_preserve_it(app, monkeypatch):
    runner = QueueRunner()
    runner.current = type('Job', (), {'settings': replace(Settings(), engine='rife', high_precision=True,
                                                         codec='hevc_amf', pixel_format='p010le', preset='balanced')})()
    runner.media = Media(96, 64, 1, 2, hdr=True)
    runner.probe_data = {'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'av1'}]}
    runner._recovery_warning = ''
    runner.stage = 'hdr_metadata_probe'
    runner._capture = json.dumps({'frames': [{'side_data_list': [
        {'side_data_type': 'Content light level metadata', 'max_content': 1000, 'max_average': 400}]}]})
    advanced = []
    monkeypatch.setattr(runner, '_prepare_subtitle_checks', lambda: advanced.append(True))
    runner._advance()
    assert advanced and 'select libx265' in runner._recovery_warning


@pytest.fixture
def tools():
    ffmpeg=os.environ.get("ANIME_TEST_FFMPEG","")
    ffprobe=os.environ.get("ANIME_TEST_FFPROBE","")
    if not Path(ffmpeg).is_file() or not Path(ffprobe).is_file():
        pytest.skip("Set ANIME_TEST_FFMPEG and ANIME_TEST_FFPROBE to run media integration tests")
    return {"ffmpeg":ffmpeg,"ffprobe":ffprobe,"video2x":os.environ.get("ANIME_TEST_VIDEO2X","")}


def run(command):
    return subprocess.run(command,check=True,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=30,
                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)).stdout


def test_queue_media_preflight_reports_invalid_files(app, tools, media_files, tmp_path):
    from anime_video_upscaler.preflight import check_media_queue
    from anime_video_upscaler.runner import Job
    source, _, _ = media_files
    invalid = tmp_path / "broken.mp4"
    invalid.write_bytes(b"not a video")
    jobs = [Job(source, replace(Settings(), engine="none")), Job(invalid, replace(Settings(), engine="none"))]
    errors = check_media_queue(None, jobs, tools["ffprobe"], lambda text: text)
    assert len(errors) == 1 and invalid.name in errors[0]


def test_readiness_report_shows_media_encoder_and_free_space(app, tools, media_files, monkeypatch):
    from PySide6.QtWidgets import QDialog, QPlainTextEdit
    from anime_video_upscaler.estimates import add_observation
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: tools)
    window = MainWindow()
    window.add_paths([str(media_files[0])])
    probe = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(media_files[0])]))
    media = Media.from_probe(probe)
    add_observation(window.runner.completed_observations, window.runner.jobs[0].settings, media, 30, 1024 * 1024)
    reports = []
    def capture(dialog):
        reports.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', capture)
    window.queue_report()
    assert len(reports) == 1
    assert media_files[0].name in reports[0]
    assert '96 × 64' in reports[0] and window.store.value.codec in reports[0] and 'GiB' in reports[0]
    assert 'Estimate from comparable completed jobs in this session' in reports[0]
    window.close()


def test_readiness_report_warns_about_resume_disk_requirements(app, tools, media_files, monkeypatch):
    from PySide6.QtWidgets import QDialog, QPlainTextEdit
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: tools)
    window = MainWindow()
    window.add_paths([str(media_files[0])])
    job = window.runner.jobs[0]
    job.settings = replace(job.settings, resume_segments=True)
    checkpoint = media_files[0].parent / ('.anime-resume-' + job.id + '-' + 'a' * 16)
    checkpoint.mkdir()
    (checkpoint / 'manifest.json').write_text(json.dumps({'version': 3, 'source': str(media_files[0])}), encoding='utf-8')
    (checkpoint / 'part-000000.mkv').write_bytes(b'example')
    reports = []
    def capture(dialog):
        reports.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', capture)
    window.queue_report()
    assert 'Resume mode stores lossless AI segments' in reports[0]
    assert 'Resume mode safety threshold, not a disk-space prediction' in reports[0]
    assert 'Possible saved checkpoints for this job: 1' in reports[0]
    assert 'Saved segments: 1' in reports[0]
    window.close()


@pytest.mark.parametrize('watermark,subtitles', [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize('transfer', ['bt709', 'smpte2084', 'arib-std-b67'])
def test_high_precision_finishing_preserves_untouched_ten_bit_luma(tools, media_files, tmp_path, watermark, subtitles, transfer):
    import array
    from anime_video_upscaler.pipeline import video_filter_graph
    subtitle = tmp_path / 'subtitle.srt'
    subtitle.write_text('1\n00:00:00,000 --> 00:00:00,900\nTest\n', encoding='utf-8')
    hdr = transfer != 'bt709'
    media = Media(256, 32, 1, 1, hdr=hdr, color_primaries='bt2020' if hdr else 'bt709', color_transfer=transfer,
                  color_space='bt2020nc' if hdr else 'bt709', color_range='tv')
    settings = replace(Settings(), high_precision=True, pixel_format='yuv420p10le', target='native', aspect='fit',
                       watermark_path=str(media_files[2]) if watermark else '', watermark_width=32, watermark_margin=4,
                       subtitle_mode='burn' if subtitles else 'off', subtitle_path=str(subtitle) if subtitles else '')
    settings.validate()
    def frame(with_finishing):
        logo = watermark and with_finishing
        sub = subtitles and with_finishing
        graph = video_filter_graph(settings, media, '0:v:0', 1 if logo else None, 'subtitle.srt' if sub else '')
        cmd = [tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
               "nullsrc=s=512x64:r=1:d=1,format=gbrp16le,geq=r='X*128':g='X*128':b='X*128'"]
        if logo:
            cmd += ['-i', str(media_files[2])]
        cmd += ['-filter_complex', graph, '-map', '[outv]', '-frames:v', '1', '-pix_fmt', 'yuv420p10le', '-f', 'rawvideo', '-']
        process = subprocess.run(cmd, cwd=tmp_path, capture_output=True, timeout=30)
        assert process.returncode == 0, process.stderr.decode('utf-8', 'replace')
        result = process.stdout
        values = array.array('H'); values.frombytes(result)
        return values
    baseline, finished = frame(False), frame(True)
    assert len(set(finished[:512 * 8])) > 256
    assert max(abs(a - b) for a, b in zip(baseline[:512 * 8], finished[:512 * 8])) <= 2
    assert sum(abs(a - b) for a, b in zip(baseline, finished)) > 100


@pytest.mark.parametrize('transfer,expected', [('smpte2084', 506), ('arib-std-b67', 614)])
def test_hdr_white_overlay_uses_reference_brightness(tools, transfer, expected):
    import array
    from anime_video_upscaler.pipeline import hdr_overlay_graph
    graph = '[1:v]format=rgba[wm]' + hdr_overlay_graph('0:v', 'wm', 'out', 'test', transfer, '0', '0')
    result = subprocess.run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
                             'color=black:s=64x64:r=1:d=1,format=yuv420p10le', '-f', 'lavfi', '-i',
                             'color=white:s=32x32:r=1:d=1,format=rgba', '-filter_complex', graph,
                             '-map', '[out]', '-frames:v', '1', '-pix_fmt', 'yuv420p10le', '-f', 'rawvideo', '-'],
                            check=True, capture_output=True, timeout=30)
    values = array.array('H'); values.frombytes(result.stdout)
    assert values[10 * 64 + 10] == pytest.approx(expected, abs=2)
    assert values[50 * 64 + 50] == 64


def test_hdr_subtitle_layer_is_visible_and_bounded(tools, tmp_path):
    import array
    from anime_video_upscaler.pipeline import video_filter_graph
    (tmp_path / 'subtitle.srt').write_text('1\n00:00:00,000 --> 00:00:00,900\nTEST SUBTITLE\n', encoding='utf-8')
    media = Media(160, 90, 1, 1, hdr=True, color_primaries='bt2020', color_transfer='smpte2084',
                  color_space='bt2020nc', color_range='tv')
    settings = replace(Settings(), high_precision=True, pixel_format='yuv420p10le', target='native', aspect='fit',
                       subtitle_mode='burn', subtitle_path='subtitle.srt')
    graph = video_filter_graph(settings, media, '0:v:0', None, 'subtitle.srt')
    result = subprocess.run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
                             'color=black:s=320x180:r=1:d=1,format=gbrp16le', '-filter_complex', graph,
                             '-map', '[outv]', '-frames:v', '1', '-pix_fmt', 'yuv420p10le', '-f', 'rawvideo', '-'],
                            cwd=tmp_path, check=True, capture_output=True, timeout=30)
    values = array.array('H'); values.frombytes(result.stdout)
    luminance = values[:320 * 180]
    assert 250 < max(luminance) < 550
    assert luminance[0] == 64


@pytest.mark.parametrize('filter_graph', ['hqdn3d=1.5:1.5:3:3', 'deflicker=size=3:mode=am'])
def test_restoration_filters_retain_more_than_eight_bit_levels(tools, filter_graph):
    import array
    source = "nullsrc=s=512x32:r=2:d=1,format=yuv420p10le,geq=lum='64+X':cb=512:cr=512"
    result = subprocess.run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', source, '-vf', filter_graph,
                             '-frames:v', '1', '-pix_fmt', 'yuv420p10le', '-f', 'rawvideo', '-'],
                            check=True, capture_output=True, timeout=30)
    values = array.array('H'); values.frombytes(result.stdout)
    assert len(set(values[:512 * 32])) > 256


def test_comparison_reports_measured_video_only_estimate(app, tools, media_files):
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.gui import SettingsStore
    source = media_files[0]
    info = json.loads(run([tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)]))
    settings = replace(Settings(), engine="none", target="native", preset="ultrafast")
    dialog = CompareDialog(source, Media.from_probe(info), 0, settings, tools, lambda text: text, SettingsStore)
    dialog.length.setValue(.5)
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    dialog.runner.busy_changed.connect(lambda busy: loop.quit() if not busy else None)
    timer.start(30000)
    dialog.start()
    loop.exec()
    timer.stop()
    assert dialog.job and dialog.job.state == "done", dialog.job.error if dialog.job else "sample did not start"
    assert "Estimated full video" in dialog.results.toPlainText()
    assert "sample startup can affect the time estimate" in dialog.results.toPlainText()
    dialog.reject()


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_source_is_oriented_and_output_has_no_rotation(app, tools, media_files, tmp_path, rotation):
    source = tmp_path / f"rotated-{rotation}.mp4"
    run([tools["ffmpeg"], "-v", "error", "-display_rotation:v:0", str(rotation), "-i", str(media_files[0]),
         "-c", "copy", str(source)])
    runner = QueueRunner()
    settings = replace(Settings(), engine="none", target="native", preset="ultrafast", output_dir=str(tmp_path))
    job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools)
    assert job.state == "done", job.error
    probe = json.loads(run([tools["ffprobe"], "-v", "error", "-show_streams", "-of", "json", str(job.output)]))
    output = Media.from_probe(probe)
    assert (output.width, output.height) == ((64, 96) if rotation % 180 else (96, 64))
    assert output.rotation % 360 == 0


@pytest.mark.parametrize("transfer", ["smpte2084", "arib-std-b67"])
@pytest.mark.parametrize("pixel_format", ["yuv420p", "yuv420p10le"])
def test_hdr_source_is_tone_mapped_to_sdr(app, tools, tmp_path, transfer, pixel_format):
    source = tmp_path / f"hdr-{transfer}.mkv"
    run([tools["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=12:duration=1",
         "-vf", f"setparams=color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc",
         "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", f"log-level=error:colorprim=bt2020:transfer={transfer}:colormatrix=bt2020nc", "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", transfer,
         "-colorspace", "bt2020nc", str(source)])
    runner = QueueRunner()
    settings = replace(Settings(), engine="none", target="native", codec="libx265", pixel_format=pixel_format,
                       preset="ultrafast", output_dir=str(tmp_path), copy_subtitles=False)
    job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools)
    assert job.state == "done", job.error
    probe = json.loads(run([tools["ffprobe"], "-v", "error", "-show_streams", "-of", "json", str(job.output)]))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert video["color_transfer"] == "bt709"
    assert video["color_primaries"] == "bt709"
    assert video["pix_fmt"] == pixel_format
    assert not Media.from_probe(probe).hdr


@pytest.mark.gpu
@pytest.mark.parametrize('processor', ['realesrgan', 'realcugan'])
def test_real_high_precision_engine_preserves_sub_eight_bit_levels(app, tools, tmp_path, processor):
    engine = Path(tools['video2x'])
    if not engine.is_file():
        pytest.skip('Set ANIME_TEST_VIDEO2X to opt into the Vulkan GPU test')
    capability = 'rgb16-realcugan-tiled-v1' if processor == 'realcugan' else 'rgb16-realesrgan-v1'
    if capability not in run([str(engine), '--help']):
        pytest.skip('High-precision engine capability is required')
    source, output = tmp_path / 'gradient.mkv', tmp_path / 'high.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
         "nullsrc=s=64x64:r=1:d=1,format=gbrp16le,geq=r='X*950':g='X*950':b='X*950'",
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    model_args = ['--realcugan-model', 'models-nose', '--realcugan-syncgap', '0'] if processor == 'realcugan' else ['--realesrgan-model', 'realesr-animevideov3']
    subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', processor, '-d', '0',
                    '-c', 'ffv1', '--pix-fmt', 'gbrp16le', '--no-copy-audio-streams',
                    '--no-copy-subtitle-streams', '-s', '2', *model_args, '-n', '0'],
                   cwd=engine.parent, check=True, capture_output=True, timeout=120,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(output), '-frames:v', '1',
                          '-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-'], check=True, capture_output=True, timeout=30).stdout
    import array
    samples = array.array('H'); samples.frombytes(raw)
    assert len(set(samples)) > 256
    assert any(value % 257 for value in samples)


@pytest.mark.gpu
@pytest.mark.parametrize('transfer', ['bt709', 'smpte2084', 'arib-std-b67'])
def test_high_precision_with_watermark_and_burned_subtitles(app, tools, media_files, tmp_path, transfer):
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realesrgan-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision engine capability is required')
    source = tmp_path / 'sdr.mkv'
    primaries, matrix = ('bt709', 'bt709') if transfer == 'bt709' else ('bt2020', 'bt2020nc')
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12:duration=1',
         '-vf', f'setparams=color_primaries={primaries}:color_trc={transfer}:colorspace={matrix}:range=tv',
         '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', f'log-level=error:colorprim={primaries}:transfer={transfer}:colormatrix={matrix}',
         '-pix_fmt', 'yuv420p10le', '-color_primaries', primaries, '-color_trc', transfer,
         '-colorspace', matrix, '-color_range', 'tv', str(source)])
    settings = replace(Settings(), high_precision=True, codec='libx265', pixel_format='yuv420p10le',
                       target='native', aspect='fit', preset='ultrafast', output_dir=str(tmp_path), copy_subtitles=False,
                       restoration='mild', flicker='mild',
                       subtitle_mode='burn', subtitle_path=str(media_files[1]), watermark_path=str(media_files[2]),
                       watermark_width=32, watermark_margin=4)
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools, timeout=25_000)
    assert job.state == 'done', job.error
    data = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(job.output)]))
    video = next(st for st in data['streams'] if st['codec_type'] == 'video')
    assert video['pix_fmt'] == 'yuv420p10le' and video['color_transfer'] == transfer


@pytest.mark.gpu
@pytest.mark.parametrize('processor', ['realesrgan', 'realcugan', 'rife', 'libplacebo'])
def test_high_precision_hdr_resume_reuses_verified_16bit_segments(app, tools, tmp_path, monkeypatch, processor):
    engine = Path(tools['video2x'])
    capability = {'realcugan': 'rgb16-realcugan-tiled-v1', 'rife': 'rgb16-rife-v4-v1', 'libplacebo': 'rgb16-libplacebo-v1'}.get(processor, 'rgb16-realesrgan-v1')
    if not engine.is_file() or capability not in run([str(engine), '--help']):
        pytest.skip('High-precision engine capability is required')
    monkeypatch.setattr('anime_video_upscaler.runner.RESUME_SEGMENT_SECONDS', 0.25)
    source = tmp_path / 'hdr-source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12:duration=1',
         '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-vf',
         'setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv',
         '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params',
         'log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc',
         '-pix_fmt', 'yuv420p10le', '-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
         '-colorspace', 'bt2020nc', '-color_range', 'tv', '-c:a', 'aac', str(source)])
    settings = replace(Settings(), high_precision=True, engine=processor, cugan_model='models-nose', resume_segments=True, codec='libx265',
                       pixel_format='yuv420p10le', target='native', aspect='fit', preset='ultrafast',
                       quality=0, output_dir=str(tmp_path), copy_subtitles=False)
    runner = QueueRunner(); messages = []; runner.log.connect(messages.append)
    job = runner.enqueue([source], settings)[0]
    def interrupt(line):
        if 'Verified segment 1/' in line and not runner.cancelled:
            QTimer.singleShot(0, runner.cancel)
    runner.log.connect(interrupt)
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'cancelled', '\n'.join(messages)
    runner.log.disconnect(interrupt)
    segment = next(tmp_path.glob('.anime-resume-*/part-000000.mkv'))
    metadata = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(segment)]))
    assert metadata['streams'][0]['pix_fmt'] == 'gbrp16le'
    if processor == 'realesrgan':
        import shutil
        saved = tmp_path / 'saved-16bit.mkv'
        invalid = tmp_path / 'invalid-8bit.mkv'
        shutil.copyfile(segment, saved)
        run([tools['ffmpeg'], '-v', 'error', '-i', str(segment), '-c:v', 'ffv1', '-pix_fmt', 'bgr0', str(invalid)])
        os.replace(invalid, segment)
        job.state, job.percent, job.error = 'queued', 0, ''
        wait_runner(app, runner, tools, timeout=120_000)
        assert job.state == 'failed' and 'not 16-bit RGB' in job.error
        shutil.copyfile(saved, segment)
    job.state, job.percent, job.error = 'queued', 0, ''
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'done', '\n'.join(messages)
    assert sum('Verified segment 1/' in line for line in messages) == 2
    assert not list(tmp_path.glob('.anime-resume-*'))
    output = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(job.output)]))
    video = next(st for st in output['streams'] if st['codec_type'] == 'video')
    assert video['pix_fmt'] == 'yuv420p10le' and video['color_transfer'] == 'smpte2084'
    assert any(st['codec_type'] == 'audio' for st in output['streams'])


@pytest.mark.gpu
@pytest.mark.parametrize('shader', ['anime4k-v4-a', 'anime4k-v4-a+a', 'anime4k-v4-b', 'anime4k-v4-b+b',
                                    'anime4k-v4-c', 'anime4k-v4-c+a', 'anime4k-v4.1-gan'])
def test_high_precision_anime4k_distinguishes_sub_eight_bit_inputs(app, tools, tmp_path, shader):
    import array
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-libplacebo-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision Anime4K capability is required')
    if not (engine.parent / 'models' / 'libplacebo' / (shader + '.glsl')).is_file():
        pytest.skip('Shader file is not available')
    source, output = tmp_path / 'source.mkv', tmp_path / 'output.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i',
         "nullsrc=s=64x64:r=2:d=1,format=gbrp16le,geq=r='32768+N*48':g='32768+N*48':b='32768+N*48'",
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'libplacebo', '-d', '0', '-c', 'ffv1',
                    '--pix-fmt', 'gbrp16le', '--no-copy-audio-streams', '--no-copy-subtitle-streams', '-w', '128', '-h', '128',
                    '--libplacebo-shader', shader, '--no-progress'], cwd=engine.parent,
                   check=True, capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(output), '-f', 'rawvideo', '-pix_fmt', 'gbrp16le', '-'],
                         check=True, capture_output=True, timeout=30).stdout
    pixels = array.array('H'); pixels.frombytes(raw); frame_size = 128 * 128 * 3
    assert len(pixels) == frame_size * 2
    assert sum(abs(pixels[i + frame_size] - pixels[i]) for i in range(frame_size)) / frame_size > 1


@pytest.mark.gpu
@pytest.mark.parametrize('model,scale', [('models-se', 3), ('models-se', 4), ('models-pro', 2), ('models-pro', 3)])
def test_high_precision_cugan_multitile_matches_reference(app, tools, tmp_path, model, scale):
    import re
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realcugan-tiled-v1' not in run([str(engine), '--help']):
        pytest.skip('Expanded high-precision Real-CUGAN capability is required')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=420x416:r=1:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    outputs = []
    for fmt in ('gbrp16le', 'bgr0'):
        output = tmp_path / (fmt + '.mkv')
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'realcugan', '-d', '0',
                        '-c', 'ffv1', '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams',
                        '-s', str(scale), '--realcugan-model', model, '--realcugan-syncgap', '0', '-n', '0', '--no-progress'],
                       cwd=engine.parent, check=True, capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs.append(output)
    result = subprocess.run([tools['ffmpeg'], '-hide_banner', '-i', str(outputs[0]), '-i', str(outputs[1]),
                             '-lavfi', '[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];[a][b]psnr',
                             '-f', 'null', '-'], check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r'average:([0-9.]+)', result.stderr)
    assert match and float(match.group(1)) > (30 if model == 'models-pro' else 40)


@pytest.mark.gpu
@pytest.mark.parametrize('finishing', [False, True])
def test_hevc_nvenc_preserves_hdr10_static_metadata(app, tools, media_files, tmp_path, finishing):
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realcugan-tiled-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision engine is required')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=256x256:rate=2:duration=1',
         '-vf', 'setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv',
         '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params',
         'log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400',
         '-pix_fmt', 'yuv420p10le', '-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
         '-colorspace', 'bt2020nc', '-color_range', 'tv', str(source)])
    settings = replace(Settings(), engine='realcugan', cugan_model='models-nose', cugan_syncgap=0, scale=2, noise=0,
                       high_precision=True, codec='hevc_nvenc', pixel_format='p010le', preset='p4', target='native',
                       aspect='fit', output_dir=str(tmp_path), copy_subtitles=False,
                       subtitle_mode='burn' if finishing else 'off', subtitle_path=str(media_files[1]) if finishing else '',
                       watermark_path=str(media_files[2]) if finishing else '', watermark_width=32, watermark_margin=4)
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'done', job.error
    def metadata(path):
        result = json.loads(run([tools['ffprobe'], '-v', 'error', '-read_intervals', '%+#10', '-select_streams', 'v:0',
                                 '-show_frames', '-show_entries', 'frame=side_data_list', '-of', 'json', str(path)]))
        return {item['side_data_type']: item for item in result['frames'][0].get('side_data_list', [])
                if item.get('side_data_type') in ('Mastering display metadata', 'Content light level metadata')}
    expected = metadata(source)
    assert set(expected) == {'Mastering display metadata', 'Content light level metadata'}
    assert metadata(job.output) == expected


@pytest.mark.gpu
@pytest.mark.parametrize('model,scale', [('models-se', 4), ('models-pro', 2)])
def test_high_precision_cugan_hdr_with_watermark_and_subtitle(app, tools, media_files, tmp_path, model, scale):
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realcugan-tiled-v1' not in run([str(engine), '--help']):
        pytest.skip('Expanded high-precision Real-CUGAN capability is required')
    source = tmp_path / 'hdr.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12:duration=1',
         '-vf', 'setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv',
         '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params',
         'log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400',
         '-pix_fmt', 'yuv420p10le', '-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
         '-colorspace', 'bt2020nc', '-color_range', 'tv', str(source)])
    settings = replace(Settings(), engine='realcugan', cugan_model=model, cugan_syncgap=0, scale=scale, noise=0,
                       high_precision=True, codec='libx265', pixel_format='yuv420p10le', target='native', aspect='fit',
                       preset='ultrafast', output_dir=str(tmp_path), copy_subtitles=False, subtitle_mode='burn',
                       subtitle_path=str(media_files[1]), watermark_path=str(media_files[2]), watermark_width=32, watermark_margin=4)
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools, timeout=60_000)
    assert job.state == 'done', job.error
    data = json.loads(run([tools['ffprobe'], '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', str(job.output)]))
    video = next(st for st in data['streams'] if st['codec_type'] == 'video')
    assert video['pix_fmt'] == 'yuv420p10le' and video['color_transfer'] == 'smpte2084' and int(video['nb_read_frames']) == 12
    import array
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(job.output), '-frames:v', '1',
                          '-pix_fmt', 'yuv420p10le', '-f', 'rawvideo', '-'], check=True, capture_output=True, timeout=30).stdout
    pixels = array.array('H'); pixels.frombytes(raw)
    assert len(set(pixels)) > 256


@pytest.mark.gpu
@pytest.mark.parametrize('model,scale,noise', [
    ('realesr-animevideov3', 2, 0), ('realesr-animevideov3', 3, 0),
    ('realesr-animevideov3', 4, 0), ('realesrgan-plus-anime', 4, 0),
    ('realesrgan-plus', 4, 0), ('realesr-generalv3', 4, 0), ('realesr-generalv3', 4, 1),
])
def test_high_precision_realesrgan_models_match_standard_path(app, tools, tmp_path, model, scale, noise):
    import array
    import re
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realesrgan-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision Real-ESRGAN capability is required')
    suffix = '-wdn' if model == 'realesr-generalv3' and noise > 0 else ''
    if not (engine.parent / 'models' / 'realesrgan' / f'{model}{suffix}-x{scale}.bin').is_file():
        pytest.skip('Model files are not available')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=1:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    outputs = []
    for fmt in ('gbrp16le', 'bgr0'):
        output = tmp_path / (fmt + '.mkv')
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'realesrgan', '-d', '0',
                        '-c', 'ffv1', '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams',
                        '-s', str(scale), '--realesrgan-model', model, '-n', str(noise), '--no-progress'],
                       cwd=engine.parent, check=True, capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs.append(output)
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(outputs[0]), '-frames:v', '1',
                          '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'], check=True, capture_output=True, timeout=30).stdout
    pixels = array.array('H'); pixels.frombytes(raw)
    assert len(set(pixels)) > 256
    result = subprocess.run([tools['ffmpeg'], '-hide_banner', '-i', str(outputs[0]), '-i', str(outputs[1]),
                             '-lavfi', '[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];[a][b]psnr',
                             '-f', 'null', '-'], check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r'average:([0-9.]+)', result.stderr)
    assert match and float(match.group(1)) > 40


@pytest.mark.gpu
def test_cugan_pro_2x_completes_without_black_frame_warning(app, tools, media_files):
    engine = Path(tools['video2x'])
    if not engine.is_file():
        pytest.skip('Vulkan GPU test requires an engine')
    settings = replace(Settings(), engine='realcugan', cugan_model='models-pro', scale=2, noise=0,
                       target='native', aspect='fit', preset='ultrafast', copy_subtitles=False)
    runner = QueueRunner(); job = runner.enqueue([media_files[0]], settings)[0]
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'done' and 'black frames' not in job.error


@pytest.mark.gpu
@pytest.mark.parametrize('model,scale,noise', [
    ('models-nose', 2, 0), ('models-se', 2, 0), ('models-se', 2, 3),
    ('models-se', 3, 0), ('models-se', 4, 0), ('models-se', 4, 3),
    ('models-pro', 2, 0), ('models-pro', 2, -1), ('models-pro', 2, 3),
    ('models-pro', 3, 0), ('models-pro', 3, -1), ('models-pro', 3, 3),
])
def test_high_precision_cugan_models_match_standard_path(app, tools, tmp_path, model, scale, noise):
    import array
    import re
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realcugan-tiled-v1' not in run([str(engine), '--help']):
        pytest.skip('Expanded high-precision Real-CUGAN capability is required')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=1:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    outputs = []
    for fmt in ('gbrp16le', 'bgr0'):
        output = tmp_path / (fmt + '.mkv')
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'realcugan', '-d', '0',
                        '-c', 'ffv1', '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams',
                        '-s', str(scale), '--realcugan-model', model, '--realcugan-syncgap', '0', '-n', str(noise), '--no-progress'],
                       cwd=engine.parent, check=True, capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs.append(output)
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(outputs[0]), '-frames:v', '1',
                          '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'], check=True, capture_output=True, timeout=30).stdout
    pixels = array.array('H'); pixels.frombytes(raw)
    assert len(set(pixels)) > 256
    result = subprocess.run([tools['ffmpeg'], '-hide_banner', '-i', str(outputs[0]), '-i', str(outputs[1]),
                             '-lavfi', '[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];[a][b]psnr',
                             '-f', 'null', '-'], check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r'average:([0-9.]+)', result.stderr)
    assert match and float(match.group(1)) > (30 if model == 'models-pro' else 40)


@pytest.mark.gpu
@pytest.mark.parametrize('engine,model,syncgap,uhd', [('rife', 'rife-v2', 0, False), ('rife', 'rife-v2', 0, True),
                                                     ('rife', 'rife-v4.26', 0, True), ('realcugan', 'models-pro', 3, False)])
def test_hdr_legacy_rife_and_synchronized_cugan_end_to_end(app, tools, media_files, tmp_path, engine, model, syncgap, uhd):
    native = Path(tools['video2x'])
    capability = ('rgb16-rife-uhd-v1' if uhd else 'rgb16-rife-legacy-v1') if engine == 'rife' else 'rgb16-realcugan-sync-v1'
    if not native.is_file() or capability not in run([str(native), '--help']):
        pytest.skip('Updated high-precision engine is required')
    source = tmp_path / 'hdr-source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=2:duration=1',
         '-vf', 'setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv',
         '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params',
         'log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400',
         '-pix_fmt', 'yuv420p10le', '-color_primaries', 'bt2020', '-color_trc', 'smpte2084',
         '-colorspace', 'bt2020nc', '-color_range', 'tv', str(source)])
    settings = replace(Settings(), engine=engine, rife_model=model if engine == 'rife' else 'rife-v4.26',
                       cugan_model=model if engine == 'realcugan' else 'models-se', cugan_syncgap=syncgap,
                       scale=2, noise=0, rife_uhd=uhd, high_precision=True, codec='libx265', pixel_format='yuv420p10le',
                       target='native', aspect='fit', preset='ultrafast', output_dir=str(tmp_path), copy_subtitles=False,
                       subtitle_mode='burn', subtitle_path=str(media_files[1]), watermark_path=str(media_files[2]),
                       watermark_width=32, watermark_margin=4)
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'done', job.error
    info = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(job.output)]))
    video = next(stream for stream in info['streams'] if stream['codec_type'] == 'video')
    assert video['pix_fmt'] == 'yuv420p10le' and video['color_transfer'] == 'smpte2084'
    frames = json.loads(run([tools['ffprobe'], '-v', 'error', '-read_intervals', '%+#1', '-select_streams', 'v:0',
                             '-show_frames', '-show_entries', 'frame=side_data_list', '-of', 'json', str(job.output)]))
    kinds = {item['side_data_type'] for item in frames['frames'][0].get('side_data_list', [])}
    assert {'Mastering display metadata', 'Content light level metadata'} <= kinds


@pytest.mark.gpu
@pytest.mark.parametrize('model', ['rife', 'rife-anime', 'rife-HD', 'rife-UHD', 'rife-v2', 'rife-v2.3',
                                   'rife-v2.4', 'rife-v3.0', 'rife-v3.1', 'rife-v4', 'rife-v4.6',
                                   'rife-v4.25', 'rife-v4.25-lite', 'rife-v4.26'])
def test_high_precision_rife_uhd_is_repeatable(app, tools, tmp_path, model):
    import array
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-rife-uhd-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision RIFE UHD capability is required')
    if not (engine.parent / 'models' / 'rife' / model).is_dir():
        pytest.skip('Model files are not available')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=2:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    frames = []
    for attempt in range(2):
        output = tmp_path / f'{attempt}.mkv'
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'rife', '-d', '0', '-c', 'ffv1',
                        '--pix-fmt', 'gbrp16le', '--no-copy-audio-streams', '--no-copy-subtitle-streams', '-m', '2',
                        '-t', '100', '--rife-model', model, '--rife-uhd', '--no-progress'], cwd=engine.parent,
                       check=True, capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(output), '-vf', r'select=eq(n\,1)',
                              '-frames:v', '1', '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'],
                             check=True, capture_output=True, timeout=30).stdout
        pixels = array.array('H'); pixels.frombytes(raw); frames.append(pixels)
    assert len(frames[0]) == 96 * 64 * 3 and len(set(frames[0])) > 256
    assert any(value % 257 for value in frames[0])
    assert frames[0] == frames[1]


@pytest.mark.gpu
@pytest.mark.parametrize('model', ['rife', 'rife-anime', 'rife-HD', 'rife-UHD', 'rife-v2', 'rife-v2.3',
                                   'rife-v2.4', 'rife-v3.0', 'rife-v3.1'])
def test_high_precision_legacy_rife_matches_standard_and_keeps_detail(app, tools, tmp_path, model):
    import array
    import re
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-rife-legacy-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision legacy RIFE capability is required')
    if not (engine.parent / 'models' / 'rife' / model).is_dir():
        pytest.skip('Model files are not available')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=2:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    outputs = []
    for fmt in ('gbrp16le', 'bgr0'):
        output = tmp_path / (fmt + '.mkv')
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'rife', '-d', '0', '-c', 'ffv1',
                        '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams', '-m', '2',
                        '-t', '100', '--rife-model', model, '--no-progress'], cwd=engine.parent,
                       check=True, capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs.append(output)
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(outputs[0]), '-vf', r'select=eq(n\,1)',
                          '-frames:v', '1', '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'],
                         check=True, capture_output=True, timeout=30).stdout
    values = array.array('H'); values.frombytes(raw)
    assert len(set(values)) > 256 and any(value % 257 for value in values)
    result = subprocess.run([tools['ffmpeg'], '-hide_banner', '-i', str(outputs[0]), '-i', str(outputs[1]),
                             '-lavfi', '[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];[a][b]psnr',
                             '-f', 'null', '-'], check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r'average:([0-9.]+)', result.stderr)
    assert match and float(match.group(1)) > 30


@pytest.mark.gpu
@pytest.mark.parametrize('uhd', [False, True])
@pytest.mark.parametrize('multiplier', [3, 4])
def test_high_precision_legacy_rife_multiple_intermediate_times(app, tools, tmp_path, multiplier, uhd):
    import array
    engine = Path(tools['video2x'])
    capability = 'rgb16-rife-uhd-v1' if uhd else 'rgb16-rife-legacy-v1'
    if not engine.is_file() or capability not in run([str(engine), '--help']):
        pytest.skip('High-precision legacy RIFE capability is required')
    source, output = tmp_path / 'source.mkv', tmp_path / 'output.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=2:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'rife', '-d', '0', '-c', 'ffv1',
                    '--pix-fmt', 'gbrp16le', '--no-copy-audio-streams', '--no-copy-subtitle-streams', '-m', str(multiplier),
                    '-t', '100', '--rife-model', 'rife-v2', '--no-progress'] + (['--rife-uhd'] if uhd else []), cwd=engine.parent,
                   check=True, capture_output=True, timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(output), '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'],
                         check=True, capture_output=True, timeout=30).stdout
    values = array.array('H'); values.frombytes(raw); size = 96 * 64 * 3
    assert len(values) == (multiplier + 1) * size
    assert sum(abs(values[size + i] - values[2 * size + i]) for i in range(size)) > size


@pytest.mark.gpu
@pytest.mark.parametrize('uhd', [False, True])
def test_high_precision_legacy_rife_distinguishes_sub_eight_bit_inputs(app, tools, tmp_path, uhd):
    import array
    engine = Path(tools['video2x'])
    capability = 'rgb16-rife-uhd-v1' if uhd else 'rgb16-rife-legacy-v1'
    if not engine.is_file() or capability not in run([str(engine), '--help']):
        pytest.skip('High-precision legacy RIFE capability is required')
    frames = []
    for offset in (0, 48):
        source, output = tmp_path / f'source{offset}.mkv', tmp_path / f'output{offset}.mkv'
        expression = f"nullsrc=s=64x64:r=2:d=1,format=gbrp16le,geq=r='32768+{offset}':g='32768+{offset}':b='32768+{offset}'"
        run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', expression, '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'rife', '-d', '0', '-c', 'ffv1',
                        '--pix-fmt', 'gbrp16le', '--no-copy-audio-streams', '--no-copy-subtitle-streams', '-m', '2',
                        '-t', '100', '--rife-model', 'rife-v2', '--no-progress'] + (['--rife-uhd'] if uhd else []), cwd=engine.parent,
                       check=True, capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(output), '-vf', r'select=eq(n\,1)',
                              '-frames:v', '1', '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'],
                             check=True, capture_output=True, timeout=30).stdout
        values = array.array('H'); values.frombytes(raw); frames.append(values)
    assert len(frames[0]) == len(frames[1]) == 64 * 64 * 3
    assert sum(abs(a - b) for a, b in zip(*frames)) / len(frames[0]) > 1


@pytest.mark.gpu
@pytest.mark.parametrize('syncgap', [1, 2, 3])
@pytest.mark.parametrize('model,scale', [('models-se', 2), ('models-se', 4), ('models-pro', 2), ('models-pro', 3)])
def test_high_precision_cugan_sync_modes_match_standard(app, tools, tmp_path, syncgap, model, scale):
    import array
    import re
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realcugan-sync-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision synchronized Real-CUGAN is required')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=420x416:r=1:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    outputs = []
    for fmt in ('gbrp16le', 'bgr0'):
        output = tmp_path / (fmt + '.mkv')
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'realcugan', '-d', '0',
                        '-c', 'ffv1', '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams',
                        '-s', str(scale), '--realcugan-model', model, '--realcugan-syncgap', str(syncgap),
                        '-n', '0', '--no-progress'], cwd=engine.parent, check=True, capture_output=True, timeout=180,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs.append(output)
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(outputs[0]), '-frames:v', '1',
                          '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'], check=True, capture_output=True, timeout=30).stdout
    values = array.array('H'); values.frombytes(raw)
    assert len(set(values)) > 256 and any(value % 257 for value in values)
    result = subprocess.run([tools['ffmpeg'], '-hide_banner', '-i', str(outputs[0]), '-i', str(outputs[1]),
                             '-lavfi', '[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];[a][b]psnr',
                             '-f', 'null', '-'], check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r'average:([0-9.]+)', result.stderr)
    assert match and float(match.group(1)) > (30 if model == 'models-pro' else 35)


@pytest.mark.gpu
@pytest.mark.parametrize('width,height', [(420, 64), (64, 420)])
@pytest.mark.parametrize('fmt', ['bgr0', 'gbrp16le'])
def test_cugan_narrow_tiled_frames_use_stable_rough_mode(app, tools, tmp_path, width, height, fmt):
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-realcugan-sync-v1' not in run([str(engine), '--help']):
        pytest.skip('Updated synchronized Real-CUGAN engine is required')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=s={width}x{height}:r=1:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    frames = []
    for mode in (2, 3):
        output = tmp_path / f'{mode}.mkv'
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'realcugan', '-d', '0',
                        '-c', 'ffv1', '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams',
                        '-s', '2', '--realcugan-model', 'models-se', '--realcugan-syncgap', str(mode),
                        '-n', '0', '--no-progress'], cwd=engine.parent, check=True, capture_output=True, timeout=180,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        frames.append(subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(output), '-frames:v', '1',
                                      '-pix_fmt', fmt, '-f', 'rawvideo', '-'], check=True, capture_output=True, timeout=30).stdout)
    assert frames[0] == frames[1] and frames[0]


@pytest.mark.gpu
@pytest.mark.parametrize('fmt', ['bgr0', 'gbrp16le'])
def test_cugan_pro_very_rough_uses_stable_rough_mode(app, tools, tmp_path, fmt):
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'realcugan-pro-range-v1' not in run([str(engine), '--help']):
        pytest.skip('Updated Real-CUGAN Pro engine is required')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=420x416:r=1:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    frames = []
    for mode in (2, 3):
        output = tmp_path / f'{mode}.mkv'
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'realcugan', '-d', '0',
                        '-c', 'ffv1', '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams',
                        '-s', '2', '--realcugan-model', 'models-pro', '--realcugan-syncgap', str(mode),
                        '-n', '0', '--no-progress'], cwd=engine.parent, check=True, capture_output=True, timeout=180,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        frames.append(subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(output), '-frames:v', '1',
                                      '-pix_fmt', fmt, '-f', 'rawvideo', '-'], check=True, capture_output=True, timeout=30).stdout)
    assert frames[0] == frames[1] and frames[0]


@pytest.mark.gpu
@pytest.mark.parametrize('model', ['rife-v4', 'rife-v4.6', 'rife-v4.25', 'rife-v4.25-lite', 'rife-v4.26'])
def test_high_precision_rife_v4_model_variants(app, tools, tmp_path, model):
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-rife-v4-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision RIFE v4 capability is required')
    if not (engine.parent / 'models' / 'rife' / model).is_dir():
        pytest.skip('Model files are not available')
    source, output = tmp_path / 'source.mkv', tmp_path / 'output.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=2:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'rife', '-d', '0', '-c', 'ffv1',
                    '--pix-fmt', 'gbrp16le', '--no-copy-audio-streams', '--no-copy-subtitle-streams', '-m', '2',
                    '-t', '100', '--rife-model', model, '--no-progress'], cwd=engine.parent,
                   check=True, capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    info = json.loads(run([tools['ffprobe'], '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', str(output)]))
    video = info['streams'][0]
    assert video['pix_fmt'] == 'gbrp16le' and int(video['nb_read_frames']) == 3


@pytest.mark.gpu
def test_high_precision_rife_produces_interpolated_sixteen_bit_frames(app, tools, tmp_path):
    import array
    import re
    engine = Path(tools['video2x'])
    if not engine.is_file() or 'rgb16-rife-v4-v1' not in run([str(engine), '--help']):
        pytest.skip('High-precision RIFE v4 capability is required')
    source = tmp_path / 'source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=2:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    outputs = []
    for fmt in ('gbrp16le', 'bgr0'):
        output = tmp_path / (fmt + '.mkv')
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', 'rife', '-d', '0', '-c', 'ffv1',
                        '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams', '-m', '2', '-t', '100',
                        '--rife-model', 'rife-v4.26', '--no-progress'], cwd=engine.parent, check=True,
                       capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs.append(output)
    raw = subprocess.run([tools['ffmpeg'], '-v', 'error', '-i', str(outputs[0]), '-vf', 'select=eq(n\\,1)',
                          '-frames:v', '1', '-pix_fmt', 'gbrp16le', '-f', 'rawvideo', '-'],
                         check=True, capture_output=True, timeout=30).stdout
    values = array.array('H'); values.frombytes(raw)
    assert len(set(values)) > 256 and any(value % 257 for value in values)
    result = subprocess.run([tools['ffmpeg'], '-hide_banner', '-i', str(outputs[0]), '-i', str(outputs[1]),
                             '-lavfi', '[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];[a][b]psnr',
                             '-f', 'null', '-'], check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r'average:([0-9.]+)', result.stderr)
    assert match and float(match.group(1)) > 40


@pytest.mark.gpu
@pytest.mark.parametrize('processor', ['realesrgan', 'realcugan'])
def test_high_precision_tiles_are_consistent_with_standard_model(app, tools, tmp_path, processor):
    import re
    engine = Path(tools['video2x'])
    capability = 'rgb16-realcugan-tiled-v1' if processor == 'realcugan' else 'rgb16-realesrgan-v1'
    if not engine.is_file() or capability not in run([str(engine), '--help']):
        pytest.skip('High-precision engine capability is required')
    source = tmp_path / 'tile-input.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=420x416:r=1:d=1,format=gbrp16le',
         '-c:v', 'ffv1', '-pix_fmt', 'gbrp16le', str(source)])
    outputs = []
    for fmt in ('gbrp16le', 'bgr0'):
        output = tmp_path / (fmt + '.mkv')
        model_args = ['--realcugan-model', 'models-nose', '--realcugan-syncgap', '0'] if processor == 'realcugan' else ['--realesrgan-model', 'realesr-animevideov3']
        subprocess.run([str(engine), '-i', str(source), '-o', str(output), '-p', processor, '-d', '0',
                        '-c', 'ffv1', '--pix-fmt', fmt, '--no-copy-audio-streams', '--no-copy-subtitle-streams',
                        '-s', '2', *model_args, '-n', '0', '--no-progress'],
                       cwd=engine.parent, check=True, capture_output=True, timeout=120,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        outputs.append(output)
    result = subprocess.run([tools['ffmpeg'], '-hide_banner', '-i', str(outputs[0]), '-i', str(outputs[1]),
                             '-lavfi', '[0:v]format=gbrp16le[a];[1:v]format=gbrp16le[b];[a][b]psnr',
                             '-f', 'null', '-'], check=True, capture_output=True, text=True, timeout=30)
    match = re.search(r'average:([0-9.]+)', result.stderr)
    assert match and float(match.group(1)) > 40


@pytest.mark.gpu
@pytest.mark.parametrize('processor', ['realesrgan', 'realcugan', 'rife', 'libplacebo'])
@pytest.mark.parametrize('transfer', ['bt709', 'smpte2084', 'arib-std-b67'])
def test_high_precision_ai_retains_tagged_color_and_ten_bit_output(app, tools, tmp_path, transfer, processor):
    engine = Path(tools['video2x'])
    capability = {'realcugan': 'rgb16-realcugan-tiled-v1', 'rife': 'rgb16-rife-v4-v1', 'libplacebo': 'rgb16-libplacebo-v1'}.get(processor, 'rgb16-realesrgan-v1')
    if not engine.is_file() or capability not in run([str(engine), '--help']):
        pytest.skip('High-precision engine capability is required')
    hdr = transfer != 'bt709'
    primaries, matrix = ('bt2020', 'bt2020nc') if hdr else ('bt709', 'bt709')
    source = tmp_path / 'source.mkv'
    x265_options = f'log-level=error:colorprim={primaries}:transfer={transfer}:colormatrix={matrix}'
    if hdr:
        x265_options += ':master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12:duration=0.5',
         '-vf', f'setparams=color_primaries={primaries}:color_trc={transfer}:colorspace={matrix}:range=tv',
         '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', x265_options,
         '-pix_fmt', 'yuv420p10le', '-color_primaries', primaries, '-color_trc', transfer,
         '-colorspace', matrix, '-color_range', 'tv', str(source)])
    settings = replace(Settings(), high_precision=True, engine=processor, cugan_model='models-nose', codec='libx265', pixel_format='yuv420p10le',
                       target='native', aspect='fit', preset='ultrafast', output_dir=str(tmp_path), copy_subtitles=False,
                       restoration='mild' if transfer == 'smpte2084' else 'off',
                       flicker='mild' if transfer == 'smpte2084' else 'off')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'done', job.error
    output = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(job.output)]))
    video = next(item for item in output['streams'] if item['codec_type'] == 'video')
    assert (video['pix_fmt'], video['color_primaries'], video['color_transfer'], video['color_space']) == ('yuv420p10le', primaries, transfer, matrix)
    if hdr:
        def first_frame(path):
            data = json.loads(run([tools['ffprobe'], '-v', 'error', '-read_intervals', '%+#1', '-select_streams', 'v:0',
                                   '-show_frames', '-show_entries', 'frame=side_data_list', '-of', 'json', str(path)]))
            return data['frames'][0].get('side_data_list', [])
        source_metadata = {item['side_data_type']: item for item in first_frame(source)
                           if item.get('side_data_type') in ('Mastering display metadata', 'Content light level metadata')}
        output_metadata = {item['side_data_type']: item for item in first_frame(job.output)
                           if item.get('side_data_type') in ('Mastering display metadata', 'Content light level metadata')}
        assert set(source_metadata) == {'Mastering display metadata', 'Content light level metadata'}
        assert output_metadata == source_metadata
    if transfer == 'smpte2084':
        from anime_video_upscaler.comparison import CompareDialog
        from anime_video_upscaler.gui import SettingsStore
        probe = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(source)]))
        dialog = CompareDialog(source, Media.from_probe(probe), 0, settings, tools, lambda text: text, SettingsStore)
        dialog.length.setValue(.5)
        loop = QEventLoop(); timer = QTimer(); timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        dialog.runner.busy_changed.connect(lambda busy: loop.quit() if not busy else None)
        timer.start(120_000); dialog.start(); loop.exec(); timer.stop()
        assert dialog.job and dialog.job.state == 'done', dialog.job.error if dialog.job else 'sample did not start'
        result = json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(dialog.job.output)]))
        assert next(st for st in result['streams'] if st['codec_type'] == 'video')['color_transfer'] == 'smpte2084'
        dialog.reject()


@pytest.mark.gpu
@pytest.mark.parametrize("kind", ["rotation", "hdr"])
@pytest.mark.parametrize("resume", [False, True])
def test_ai_processes_normalized_source(app, tools, media_files, tmp_path, kind, resume):
    if not Path(tools["video2x"]).is_file():
        pytest.skip("Set ANIME_TEST_VIDEO2X to opt into the Vulkan GPU test")
    source = tmp_path / (kind + ".mkv")
    if kind == "rotation":
        run([tools["ffmpeg"], "-v", "error", "-display_rotation:v:0", "90", "-i", str(media_files[0]), "-c", "copy", str(source)])
    else:
        run([tools["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=12:duration=1",
             "-vf", "setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc", "-c:v", "libx265",
             "-preset", "ultrafast", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le", str(source)])
    settings = replace(Settings(), target="native", preset="ultrafast", output_dir=str(tmp_path), copy_subtitles=False, resume_segments=resume)
    runner = QueueRunner()
    job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == "done", job.error
    probe = json.loads(run([tools["ffprobe"], "-v", "error", "-show_streams", "-of", "json", str(job.output)]))
    result = Media.from_probe(probe)
    assert (result.width, result.height) == ((128, 192) if kind == "rotation" else (192, 128))
    assert not result.hdr and not result.rotation


@pytest.fixture
def media_files(tools,tmp_path):
    source=tmp_path/"Türkçe [test], 'video'.mp4"
    run([tools["ffmpeg"],"-v","error","-f","lavfi","-i","testsrc2=size=96x64:rate=12:duration=1",
         "-f","lavfi","-i","sine=frequency=440:duration=1","-c:v","libx264","-bf","2","-c:a","aac",str(source)])
    subtitle=tmp_path/"Türkçe 'altyazı'.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:00,800\nAnime test\n",encoding="utf-8")
    watermark=tmp_path/"Türkçe logo.png"
    image=QImage(24,16,QImage.Format.Format_ARGB32)
    image.fill(QColor(200,80,200,180))
    image.save(str(watermark))
    return source,subtitle,watermark


def wait_runner(app,runner,tools,timeout=60_000,cancel_after=None):
    loop=QEventLoop()
    timer=QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    def on_busy(busy):
        if not busy:
            loop.quit()
    runner.busy_changed.connect(on_busy)
    runner.start(tools)
    if cancel_after is not None:
        QTimer.singleShot(cancel_after,runner.cancel)
    timer.start(timeout)
    loop.exec()
    if runner.busy:
        runner.cancel()
        cleanup=QEventLoop()
        runner.busy_changed.connect(lambda busy: cleanup.quit() if not busy else None)
        QTimer.singleShot(3000,cleanup.quit)
        cleanup.exec()
        pytest.fail("Job did not complete within timeout")
    timer.stop()
    runner.busy_changed.disconnect(on_busy)


def test_mkv_container_longer_than_video_uses_video_timestamps(app, tools, tmp_path):
    subtitle = tmp_path / "long.srt"
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,500\nTest\n", encoding="utf-8")
    source = tmp_path / "source.mkv"
    run([tools["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=10:duration=1",
         "-i", str(subtitle), "-map", "0:v", "-map", "1:s", "-c:v", "libx264", "-c:s", "srt", str(source)])
    runner = QueueRunner()
    messages = []
    runner.log.connect(messages.append)
    job = runner.enqueue([source], replace(Settings(), engine="none", target="native", preset="ultrafast", container="mp4"))[0]
    wait_runner(app, runner, tools)
    assert job.state == "done", "\n".join(messages)
    assert job.output.exists()
    assert any("checking source video packet timestamps" in message for message in messages)


@pytest.mark.parametrize("encoding", ["cp1254", "utf-16", "mixed"])
@pytest.mark.parametrize("mode", ["soft", "burn"])
def test_legacy_turkish_subtitles_complete_without_decode_errors(app, tools, media_files, mode, encoding):
    source, subtitle, _ = media_files
    text = "Türkçe: ı İ ş Ş ğ Ğ ç ö ü"
    content = "1\n00:00:00,000 --> 00:00:00,800\n" + text + "\n"
    original = (content.encode("cp1254") + "2\n00:00:00,800 --> 00:00:01,000\nСантен Кесщин!\n".encode("utf-8")
                if encoding == "mixed" else content.encode(encoding))
    subtitle.write_bytes(original)
    runner = QueueRunner()
    messages = []
    runner.log.connect(messages.append)
    settings = replace(Settings(), engine="none", target="native", preset="ultrafast", container="mp4",
                       subtitle_mode=mode, subtitle_path=str(subtitle))
    job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools)
    assert job.state == "done", "\n".join(messages)
    assert subtitle.read_bytes() == original
    assert not any("Invalid UTF-8" in message or "Error decoding subtitles" in message for message in messages)
    if mode == "soft":
        decoded = run([tools["ffmpeg"], "-v", "error", "-i", str(job.output), "-map", "0:s:0", "-f", "srt", "-"])
        assert text in decoded
        if encoding == "mixed":
            assert "Сантен Кесщин!" in decoded


def test_invalid_external_subtitle_fails_before_video_processing(app, tools, media_files):
    source, subtitle, _ = media_files
    subtitle.write_text("1\n00:00:02,000 --> 00:00:01,000\nText\n", encoding="utf-8")
    runner = QueueRunner()
    states = []
    runner.changed.connect(lambda job: states.append(job.state))
    job = runner.enqueue([source], replace(Settings(), engine="none", target="native", preset="ultrafast",
                                          subtitle_mode="soft", subtitle_path=str(subtitle)))[0]
    wait_runner(app, runner, tools)
    assert job.state == "failed" and "timing" in job.error
    assert "encode" not in states and not job.output.exists()


@pytest.mark.parametrize("mode,container,flicker",[("burn","mkv","off"),("soft","mp4","off"),("burn","mkv","mild")])
def test_real_combined_workflow(app,tools,media_files,mode,container,flicker):
    source,subtitle,watermark=media_files
    settings=replace(Settings(),engine="none",target="custom",width=192,height=128,preset="ultrafast",
        subtitle_mode=mode,subtitle_path=str(subtitle),watermark_path=str(watermark),
        watermark_width=32,watermark_margin=4,container=container,flicker=flicker)
    runner=QueueRunner()
    messages=[]
    runner.log.connect(messages.append)
    job=runner.enqueue([source],settings)[0]
    wait_runner(app,runner,tools)
    assert job.state == "done", "\n".join(messages)
    assert job.percent == 100 and job.output.exists()
    probe=json.loads(run([tools["ffprobe"],"-v","error","-count_frames","-show_streams","-of","json",str(job.output)]))
    video=next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"],video["height"],int(video["nb_read_frames"])) == (192,128,12)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert any(s["codec_type"] == "subtitle" for s in probe["streams"]) == (mode == "soft")
    assert not list(source.parent.glob(".anime-job-*"))


def test_bad_file_does_not_stop_next_job(app,tools,media_files,tmp_path):
    bad=tmp_path/"broken.mp4"
    bad.write_text("not a video")
    runner=QueueRunner()
    jobs=runner.enqueue([bad,media_files[0]],replace(Settings(),engine="none",target="native",preset="ultrafast"))
    wait_runner(app,runner,tools)
    assert [j.state for j in jobs] == ["failed","done"]


@pytest.mark.stress
def test_long_synthetic_queue_preserves_video_and_audio_duration(app, tools, tmp_path):
    if os.environ.get("ANIME_TEST_STRESS") != "1":
        pytest.skip("Set ANIME_TEST_STRESS=1 to run the long synthetic media test")
    duration = 600
    source = tmp_path / "long-source.mkv"
    run([tools["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=64x48:rate=4:duration={duration}",
         "-f", "lavfi", "-i", f"sine=sample_rate=8000:duration={duration}", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(source)])
    runner = QueueRunner()
    job = runner.enqueue([source], replace(Settings(), engine="none", target="native", preset="ultrafast",
                                           output_dir=str(tmp_path), copy_subtitles=False))[0]
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == "done", job.error
    data = json.loads(run([tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(job.output)]))
    assert abs(float(data["format"]["duration"]) - duration) < 1
    assert {stream["codec_type"] for stream in data["streams"]} >= {"video", "audio"}
    def bounds(path, stream):
        packets = json.loads(run([tools["ffprobe"], "-v", "error", "-select_streams", stream,
                                  "-show_entries", "packet=pts_time,duration_time", "-of", "json", str(path)]))["packets"]
        starts = [float(packet["pts_time"]) for packet in packets if "pts_time" in packet]
        return min(starts), max(starts)
    for stream in ("v:0", "a:0"):
        before, after = bounds(source, stream), bounds(job.output, stream)
        assert all(abs(a - b) < .15 for a, b in zip(before, after))


def test_cancel_kills_process_cleans_partial_and_leaves_waiting_jobs(app,tools,media_files):
    runner=QueueRunner()
    source=media_files[0]
    job=runner.enqueue([source],replace(Settings(),engine="none",target="4k",preset="veryslow"))[0]
    def cancel_at_encode(changed):
        if changed.state == "encode":
            QTimer.singleShot(50,runner.cancel)
    runner.changed.connect(cancel_at_encode)
    wait_runner(app,runner,tools)
    assert job.state == "cancelled" and not job.output.exists()
    assert not list(source.parent.glob(".anime-job-*"))


@pytest.mark.gpu
@pytest.mark.parametrize("fractional_fps", [False, True])
@pytest.mark.parametrize("engine", ["realesrgan", "realcugan", "libplacebo", "rife"])
def test_resume_reuses_verified_segments_and_matches_uninterrupted_video(app, tools, media_files, monkeypatch, fractional_fps, engine):
    if not Path(tools["video2x"]).is_file():
        pytest.skip("Set ANIME_TEST_VIDEO2X to opt into the Vulkan GPU test")
    monkeypatch.setattr('anime_video_upscaler.runner.RESUME_SEGMENT_SECONDS', 0.25)
    source = media_files[0]
    if fractional_fps:
        source = source.parent / 'fractional.mkv'
        run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=24000/1001:duration=1',
             '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264', '-bf', '2', '-c:a', 'aac', str(source)])
    settings = replace(Settings(), engine=engine, scale=2, target="native", aspect="fit",
                       preset="ultrafast", quality=0, audio="copy", copy_subtitles=False,
                       output_dir=str(source.parent), resume_segments=True)
    runner = QueueRunner()
    messages = []
    runner.log.connect(messages.append)
    job = runner.enqueue([source], settings)[0]
    def interrupt_after_segment(line):
        if 'Verified segment 1/' in line and not runner.cancelled:
            QTimer.singleShot(0, runner.cancel)
    runner.log.connect(interrupt_after_segment)
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'cancelled', '\n'.join(messages)
    runner.log.disconnect(interrupt_after_segment)
    assert list(source.parent.glob('.anime-resume-*/part-000000.mkv'))
    job.state, job.percent, job.error = 'queued', 0, ''
    wait_runner(app, runner, tools, timeout=120_000)
    assert job.state == 'done', '\n'.join(messages)
    assert sum('Verified segment 1/' in line for line in messages) == 2
    assert not list(source.parent.glob('.anime-resume-*'))
    resumed_output = job.output
    reference = QueueRunner()
    reference_job = reference.enqueue([source], replace(settings, resume_segments=False))[0]
    wait_runner(app, reference, tools, timeout=120_000)
    assert reference_job.state == 'done', reference_job.error
    def frame_hashes(path):
        result = run([tools['ffmpeg'], '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'framemd5', '-'])
        return [line.rsplit(',', 1)[-1].strip() for line in result.splitlines() if line and not line.startswith('#')]
    assert frame_hashes(resumed_output) == frame_hashes(reference_job.output)
    def audio_hash(path):
        return run([tools['ffmpeg'], '-v', 'error', '-i', str(path), '-map', '0:a:0', '-f', 'hash', '-hash', 'sha256', '-']).strip()
    assert audio_hash(resumed_output) == audio_hash(reference_job.output)


@pytest.mark.gpu
@pytest.mark.parametrize("engine",["realesrgan","realcugan","libplacebo","rife"])
def test_real_ai_with_delayed_input_frames(app,tools,media_files,engine):
    if not Path(tools["video2x"]).is_file():
        pytest.skip("Set ANIME_TEST_VIDEO2X to opt into the Vulkan GPU test")
    source,subtitle,watermark=media_files
    runner=QueueRunner()
    logs=[]
    runner.log.connect(logs.append)
    job=runner.enqueue([source],replace(Settings(),engine=engine,recover_shutdown=True,target="custom",width=192,height=128,preset="ultrafast",
        subtitle_mode="burn",subtitle_path=str(subtitle),watermark_path=str(watermark),watermark_width=32,watermark_margin=4))[0]
    wait_runner(app,runner,tools,timeout=120_000)
    assert job.state in ("done","done_warning"), "\n".join(logs)
    probe=json.loads(run([tools["ffprobe"],"-v","error","-count_frames","-show_streams","-of","json",str(job.output)]))
    video=next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert int(video["nb_read_frames"]) == (24 if engine == "rife" else 12)


@pytest.mark.gpu
def test_real_4k_nvenc_output(app,tools,media_files):
    if not Path(tools["video2x"]).is_file():
        pytest.skip("GPU opt-in required")
    runner=QueueRunner()
    logs=[]
    runner.log.connect(logs.append)
    source,subtitle,watermark=media_files
    job=runner.enqueue([source],replace(Settings(),recover_shutdown=True,codec="hevc_nvenc",preset="p4",target="4k",
        subtitle_mode="burn",subtitle_path=str(subtitle),watermark_path=str(watermark)))[0]
    wait_runner(app,runner,tools)
    assert job.state in ("done","done_warning"), "\n".join(logs)
    probe=json.loads(run([tools["ffprobe"],"-v","error","-show_streams","-of","json",str(job.output)]))
    video=next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"],video["height"]) == (3840,2160)


def test_deflicker_reduces_synthetic_luminance_spikes_without_dropping_frames(app,tools,tmp_path):
    import statistics
    source=tmp_path/"flicker.mkv"
    run([tools["ffmpeg"],"-v","error","-f","lavfi","-i",
         "nullsrc=s=64x64:r=15:d=1,geq=lum='90+40*eq(mod(N,5),2)':cb=128:cr=128",
         "-c:v","ffv1",str(source)])
    variances=[]
    for mode in ("off","mild"):
        runner=QueueRunner()
        job=runner.enqueue([source],replace(Settings(),engine="none",target="native",flicker=mode,
            preset="ultrafast",quality=0,audio="off",copy_subtitles=False))[0]
        wait_runner(app,runner,tools)
        assert job.state == "done", job.error
        pixels=subprocess.run([tools["ffmpeg"],"-v","error","-i",str(job.output),"-f","rawvideo","-pix_fmt","gray","pipe:1"],
            check=True,capture_output=True,timeout=30,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)).stdout
        assert len(pixels) == 64*64*15
        means=[sum(pixels[i:i+4096])/4096 for i in range(0,len(pixels),4096)]
        variances.append(statistics.pvariance(means))
    assert variances[1] < variances[0]*0.5


def test_qhd_factory_preset_produces_expected_canvas(app,tools,media_files):
    runner=QueueRunner()
    settings=replace(FACTORY_PRESETS["Anime · QHD balanced CPU"],engine="none",preset="ultrafast")
    job=runner.enqueue([media_files[0]],settings)[0]
    wait_runner(app,runner,tools)
    assert job.state == "done", job.error
    probe=json.loads(run([tools["ffprobe"],"-v","error","-show_streams","-of","json",str(job.output)]))
    video=next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"],video["height"]) == (2560,1440)


def test_preview_refreshes_watermark_and_seek_without_running_ai(app,tools,media_files):
    from PySide6.QtCore import QEventLoop, QTimer
    from anime_video_upscaler.gui import SettingsStore
    from anime_video_upscaler.i18n import Translator
    from anime_video_upscaler.preview import PreviewPanel
    source,subtitle,watermark=media_files
    # This AI model/scale combination is invalid for processing, but must not
    # prevent source preview and overlay placement.
    settings=replace(Settings(),engine="realesrgan",model="realesrgan-plus",scale=2,
                     target="custom",width=192,height=128,
                     watermark_width=32,watermark_margin=4,watermark_opacity=1.0)
    store=SettingsStore(settings)
    panel=PreviewPanel(store,tools,Translator())
    panel.show()
    panel.set_source(source)
    def wait_for_frame():
        loop=QEventLoop()
        timer=QTimer()
        timer.setInterval(25)
        timer.timeout.connect(lambda: loop.quit() if not panel.canvas.frame.isNull() and panel.process is None and not panel.debounce.isActive() else None)
        timer.start()
        timeout=QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(loop.quit)
        timeout.start(15_000)
        loop.exec()
        timeout.stop()
        timer.stop()
        assert not panel.canvas.frame.isNull(), panel.status.text() + ' ' + panel.status.toolTip()
    wait_for_frame()
    original=panel.canvas.frame.copy()
    store.update("watermark_corner","top-left")
    store.update("watermark_path",str(watermark))
    panel.canvas.frame=QImage()
    wait_for_frame()
    changed=panel.canvas.frame
    assert changed.size() == original.size()
    point=(30,30)
    a,b=original.pixelColor(*point),changed.pixelColor(*point)
    assert abs(a.red()-b.red())+abs(a.green()-b.green())+abs(a.blue()-b.blue()) > 25
    panel.slider.setValue(500)
    panel.canvas.frame=QImage()
    wait_for_frame()
    assert panel.slider.value() == 500 and panel.media.duration > 0
    panel.shutdown()
    panel.close()


def test_preview_with_user_4k_fit_settings(app,tools,media_files):
    from anime_video_upscaler.gui import SettingsStore
    from anime_video_upscaler.i18n import Translator
    from anime_video_upscaler.preview import PreviewPanel
    settings = replace(Settings(), target="4k", aspect="fit", codec="h264_nvenc",
                       preset="p6", quality=24, container="mp4", flicker="mild")
    panel = PreviewPanel(SettingsStore(settings), tools, Translator("tr"))
    panel.show()
    panel.set_source(media_files[0])
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(50)
    poll.timeout.connect(lambda: loop.quit() if not panel.canvas.frame.isNull() else None)
    poll.start()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(25_000)
    loop.exec()
    timeout.stop()
    poll.stop()
    assert not panel.canvas.frame.isNull(), panel.canvas.text() + ' ' + panel.status.toolTip()
    panel.shutdown()
    panel.close()


def test_preview_burned_subtitle_uses_seek_timestamp(app,tools,tmp_path):
    from anime_video_upscaler.pipeline import Media, preview_command
    source=tmp_path/"black.mkv"
    run([tools["ffmpeg"],"-v","error","-f","lavfi","-i","color=c=black:s=192x128:r=10:d=1", "-c:v","ffv1",str(source)])
    (tmp_path/"subtitle.srt").write_text("1\n00:00:00,000 --> 00:00:00,500\nVISIBLE SUBTITLE\n",encoding="utf-8")
    media=Media(192,128,1,10)
    settings=replace(Settings(),engine="none",target="custom",width=192,height=128,subtitle_mode="burn")
    images=[]
    for time_point in (0.2,0.8):
        command=preview_command(settings,tools,source,media,time_point,"subtitle.srt")
        completed=subprocess.run(command,cwd=tmp_path,check=True,capture_output=True,timeout=30,
                                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        image=QImage.fromData(completed.stdout,"PNG")
        assert not image.isNull()
        # Subtitle is white on the otherwise black synthetic image.
        bright=sum(image.pixelColor(x,y).red()>70 for y in range(image.height()//2,image.height(),3)
                   for x in range(0,image.width(),3))
        images.append(bright)
    assert images[0] > 20 and images[1] == 0


def test_real_short_comparison_uses_queue_and_cleans_up(app, tools, media_files):
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.gui import SettingsStore
    from anime_video_upscaler.i18n import Translator
    from anime_video_upscaler.pipeline import Media
    source = media_files[0]
    settings = replace(Settings(), engine='none', target='custom', width=192, height=128,
                       preset='ultrafast', audio='off', copy_subtitles=False)
    dialog = CompareDialog(source, Media(96,64,1,12), 0, settings, tools, Translator(), SettingsStore)
    dialog.length.setValue(0.5)
    dialog.show()
    dialog.start()
    loop=QEventLoop()
    timer=QTimer()
    timer.setInterval(25)
    timer.timeout.connect(lambda: loop.quit() if dialog.job and not dialog.runner.busy else None)
    timer.start()
    timeout=QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(30_000)
    loop.exec()
    timeout.stop()
    timer.stop()
    assert dialog.job and dialog.job.state == 'done', dialog.status.text()
    assert dialog.job.output.is_file() and dialog.clip.is_file()
    # The comparison is useful only when both actual decoded pictures appear.
    images=QEventLoop()
    image_poll=QTimer()
    image_poll.setInterval(25)
    image_poll.timeout.connect(lambda: images.quit() if not dialog.before.canvas.frame.isNull() and not dialog.after.canvas.frame.isNull() else None)
    image_poll.start()
    image_timeout=QTimer()
    image_timeout.setSingleShot(True)
    image_timeout.timeout.connect(images.quit)
    image_timeout.start(15_000)
    images.exec()
    image_poll.stop()
    image_timeout.stop()
    assert not dialog.before.canvas.frame.isNull(), dialog.before.canvas.text()
    assert not dialog.after.canvas.frame.isNull(), dialog.after.canvas.text()
    dialog.before.slider.setValue(250)
    assert dialog.after.slider.value() == 250
    directory=Path(dialog.temp.name)
    dialog.reject()
    assert not directory.exists()


def test_real_free_watermark_position_in_preview_and_output(app, tools, media_files, tmp_path):
    from anime_video_upscaler.pipeline import Media, preview_command
    import shutil
    source, _, watermark = media_files
    staged = tmp_path/'watermark.png'
    shutil.copyfile(watermark, staged)
    settings = replace(Settings(), engine='none', target='custom', width=192, height=128,
                       preset='ultrafast', audio='off', copy_subtitles=False, watermark_path=str(watermark),
                       watermark_corner='free', watermark_x=0.25, watermark_y=0.75,
                       watermark_width=32, watermark_opacity=1.0)
    media=Media(96,64,1,12)
    rendered=[]
    for watermark_name in ('', 'watermark.png'):
        command=preview_command(settings, tools, source, media, 0.25, watermark_name=watermark_name)
        completed=subprocess.run(command,cwd=tmp_path,check=True,capture_output=True,timeout=30,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        image=QImage.fromData(completed.stdout,'PNG')
        assert not image.isNull()
        rendered.append(image)
    # Overlay center: (40+16, 80+11) in the 192x128 output, scaled to preview.
    a,b=(img.pixelColor(round(img.width()*56/192),round(img.height()*91/128)) for img in rendered)
    assert abs(a.red()-b.red())+abs(a.green()-b.green())+abs(a.blue()-b.blue()) > 20
    runner=QueueRunner()
    job=runner.enqueue([source],settings)[0]
    wait_runner(app,runner,tools)
    assert job.state == 'done', job.error
    output_probe=json.loads(run([tools['ffprobe'],'-v','error','-show_streams','-of','json',str(job.output)]))
    video=next(s for s in output_probe['streams'] if s['codec_type']=='video')
    assert (video['width'],video['height']) == (192,128)
    decoded=subprocess.run([tools['ffmpeg'],'-v','error','-ss','0.25','-i',str(job.output),
                            '-frames:v','1','-f','image2pipe','-vcodec','png','pipe:1'],
                           check=True,capture_output=True,timeout=30,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    output_image=QImage.fromData(decoded.stdout,'PNG')
    assert not output_image.isNull()
    before=rendered[0].pixelColor(round(rendered[0].width()*56/192),round(rendered[0].height()*91/128))
    after=output_image.pixelColor(56,91)
    assert abs(before.red()-after.red())+abs(before.green()-after.green())+abs(before.blue()-after.blue()) > 20


def test_comparison_closing_while_preparing_cleans_up(app, tools, media_files):
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.gui import SettingsStore
    from anime_video_upscaler.i18n import Translator
    from anime_video_upscaler.pipeline import Media
    settings=replace(Settings(),engine='none',target='native',preset='ultrafast')
    dialog=CompareDialog(media_files[0],Media(96,64,1,12),0,settings,tools,Translator(),SettingsStore)
    dialog.length.setValue(0.5)
    dialog.show()
    dialog.start()
    assert dialog.process is not None
    directory=Path(dialog.temp.name)
    dialog.reject()
    loop=QEventLoop()
    timer=QTimer()
    timer.setInterval(25)
    timer.timeout.connect(lambda: loop.quit() if dialog.temp is None else None)
    timer.start()
    timeout=QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(5000)
    loop.exec()
    timeout.stop()
    timer.stop()
    assert dialog.temp is None and not directory.exists()


def test_audio_bitrate_and_mild_restoration_real_output(app,tools,media_files):
    source,_,_=media_files
    settings=replace(Settings(),engine='none',target='custom',width=192,height=128,preset='ultrafast',
                     audio='aac',audio_bitrate=320000,copy_subtitles=False,restoration='mild')
    runner=QueueRunner()
    lines=[];runner.log.connect(lines.append)
    job=runner.enqueue([source],settings)[0]
    wait_runner(app,runner,tools)
    assert job.state == 'done', '\n'.join(lines)
    assert any('hqdn3d=1.5:1.5:3:3' in line for line in lines)
    probe=json.loads(run([tools['ffprobe'],'-v','error','-show_streams','-of','json',str(job.output)]))
    audio=next(stream for stream in probe['streams'] if stream['codec_type']=='audio')
    assert audio['codec_name'] == 'aac' and int(audio.get('bit_rate',320000)) > 0


def test_reference_model_comparison_reuses_same_clip_and_cleans_reference(app,tools,media_files):
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.gui import SettingsStore
    from anime_video_upscaler.i18n import Translator
    from anime_video_upscaler.pipeline import Media
    source=media_files[0]
    settings=replace(Settings(),engine='none',target='custom',width=192,height=128,preset='ultrafast',
                     audio='off',copy_subtitles=False)
    dialog=CompareDialog(source,Media(96,64,1,12),0,settings,tools,Translator(),SettingsStore)
    dialog.show();dialog.length.setValue(0.5)
    def process():
        dialog.start()
        loop=QEventLoop();poll=QTimer();timeout=QTimer()
        poll.setInterval(25)
        poll.timeout.connect(lambda:loop.quit() if dialog.job and not dialog.runner.busy else None)
        timeout.setSingleShot(True);timeout.timeout.connect(loop.quit)
        poll.start();timeout.start(30000);loop.exec();poll.stop();timeout.stop()
        assert dialog.job and dialog.job.state == 'done',dialog.status.text()
    process()
    dialog.keep_reference()
    reference=dialog.reference
    assert reference.is_file() and not dialog.start_time.isEnabled() and not dialog.length.isEnabled()
    dialog.model_choice.setCurrentIndex(dialog.model_choice.findData('none'))
    process()
    assert dialog.before.source == reference and dialog.reference.is_file()
    refdir=Path(dialog.reference_temp.name)
    dialog.reject()
    assert not refdir.exists()


@pytest.mark.gpu
def test_general_video_ai_with_restoration(app,tools,media_files):
    if not Path(tools['video2x']).is_file():
        pytest.skip('Set ANIME_TEST_VIDEO2X to opt into the Vulkan GPU test')
    source,_,_=media_files
    settings=replace(Settings(),engine='realesrgan',model='realesrgan-plus',scale=4,
                     restoration='mild',recover_shutdown=True,target='custom',width=192,height=128,
                     preset='ultrafast',audio='off',copy_subtitles=False)
    runner=QueueRunner();lines=[];runner.log.connect(lines.append)
    job=runner.enqueue([source],settings)[0]
    wait_runner(app,runner,tools,timeout=120000)
    assert job.state in ('done','done_warning'),'\n'.join(lines)
    assert any('hqdn3d=1.5:1.5:3:3' in line for line in lines)
    result=json.loads(run([tools['ffprobe'],'-v','error','-count_frames','-show_streams','-of','json',str(job.output)]))
    video=next(stream for stream in result['streams'] if stream['codec_type']=='video')
    assert int(video['nb_read_frames']) == 12


@pytest.mark.gpu
def test_real_nvidia_quality_profile_with_preflight(app,tools,media_files):
    if not Path(tools['video2x']).is_file(): pytest.skip('GPU opt-in required')
    source,_,_=media_files
    settings=replace(FACTORY_PRESETS['Anime · 4K detail NVIDIA HEVC'],recover_shutdown=True,
                     target='custom',width=192,height=128,audio='off',copy_subtitles=False)
    runner=QueueRunner();states=[];lines=[]
    runner.changed.connect(lambda job:states.append(job.state));runner.log.connect(lines.append)
    job=runner.enqueue([source],settings)[0]
    wait_runner(app,runner,tools,timeout=120000)
    assert job.state in ('done','done_warning'), '\n'.join(lines)
    first_ai = 'stream' if 'stream' in states else 'normalize'
    assert 'encoder_check' in states and states.index('encoder_check') < states.index(first_ai)
    assert any('rc-lookahead' in line and 'spatial-aq' in line for line in lines)


@pytest.mark.gpu
def test_unavailable_amd_encoder_fails_before_ai(app,tools,media_files):
    from anime_video_upscaler.pipeline import encoder_probe_command
    if not Path(tools['video2x']).is_file(): pytest.skip('GPU opt-in required')
    settings=replace(FACTORY_PRESETS['Anime · 4K detail AMD HEVC'],target='custom',width=192,height=128,
                     audio='off',copy_subtitles=False)
    probe=subprocess.run(encoder_probe_command(settings,tools),capture_output=True,timeout=30,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if probe.returncode==0: pytest.skip('An AMD encoder is present on this machine')
    runner=QueueRunner();states=[]
    runner.changed.connect(lambda job:states.append(job.state))
    job=runner.enqueue([media_files[0]],settings)[0]
    wait_runner(app,runner,tools,timeout=60000)
    assert job.state=='failed' and 'GPU encoder initialization failed' in job.error
    assert 'encoder_check' in states and not {'normalize', 'ai', 'stream'}.intersection(states)


@pytest.mark.gpu
def test_unsupported_nvidia_av1_fails_before_ai(app,tools,media_files):
    from anime_video_upscaler.pipeline import encoder_probe_command
    if not Path(tools['video2x']).is_file(): pytest.skip('GPU opt-in required')
    settings=replace(FACTORY_PRESETS['Anime · 4K detail NVIDIA AV1'],target='custom',width=192,height=128,
                     audio='off',copy_subtitles=False)
    probe=subprocess.run(encoder_probe_command(settings,tools),capture_output=True,timeout=30,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if probe.returncode==0: pytest.skip('This NVIDIA GPU supports AV1 encoding')
    runner=QueueRunner();states=[];logs=[]
    runner.changed.connect(lambda job:states.append(job.state));runner.log.connect(logs.append)
    job=runner.enqueue([media_files[0]],settings)[0]
    wait_runner(app,runner,tools,timeout=60000)
    assert job.state=='failed' and 'GPU encoder initialization failed' in job.error
    assert 'encoder_check' in states and not {'normalize', 'ai', 'stream'}.intersection(states)
    assert any('No capable devices' in line or 'encoder' in line.lower() for line in logs)


@pytest.mark.gpu
def test_reference_cugan_applies_sync_modes_and_finishes_without_recovery(app, tools, tmp_path):
    engine = Path(tools['video2x'])
    if not engine.is_file() or not (engine.parent/'avue-realcugan.exe').is_file():
        pytest.skip('Bundled Real-CUGAN reference engine and GPU required')
    # Larger than the GPU tile so synchronization modes take different paths.
    source = tmp_path / 'Türkçe çizim.mp4'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=450x430:rate=12:duration=0.167',
         '-frames:v', '2', '-c:v', 'libx264', '-preset', 'ultrafast', str(source)])
    hashes = []
    for sync in (0, 1):
        runner = QueueRunner(); logs=[]; states=[]
        runner.log.connect(logs.append)
        runner.changed.connect(lambda job: states.append(job.state))
        settings = replace(Settings(), engine='realcugan', cugan_model='models-se', cugan_syncgap=sync,
                           scale=2, target='native', aspect='fit', preset='ultrafast', audio='off',
                           copy_subtitles=False, recover_shutdown=False)
        job = runner.enqueue([source], settings)[0]
        wait_runner(app, runner, tools, timeout=180000)
        assert job.state == 'done', '\n'.join(logs)
        if 'stream' in states:
            assert any('Stream verified: 2 source frames, 2 encoded frames' in line for line in logs)
        else:
            assert 'ai_frames' in states and 'assemble' in states and 'verify_ai' in states
            assert runner._processed_images == 2
        frames = run([tools['ffmpeg'], '-v', 'error', '-i', str(job.output), '-f', 'framemd5', '-'])
        hashes.append([line.split(',')[-1].strip() for line in frames.splitlines() if line and not line.startswith('#')])
    assert len(hashes[0]) == len(hashes[1]) == 2
    assert hashes[0] != hashes[1], 'Synchronization mode did not change the tiled output'


def test_reference_cugan_rejects_missing_output_frame(tmp_path):
    runner = QueueRunner()
    runner.frame_inputs = tmp_path/'input'; runner.frame_inputs.mkdir()
    runner.frame_outputs = tmp_path/'output'; runner.frame_outputs.mkdir()
    (runner.frame_inputs/'000000001.png').write_bytes(b'frame')
    runner._normalized_frames = 1
    with pytest.raises(ValueError, match='every expected frame'):
        runner._assemble_cugan_frames()


@pytest.mark.gpu
def test_reference_cugan_preserves_audio_video_start_offset(app, tools, tmp_path):
    engine = Path(tools['video2x'])
    if not engine.is_file() or not (engine.parent/'avue-realcugan.exe').is_file():
        pytest.skip('Bundled Real-CUGAN reference engine and GPU required')
    source = tmp_path/'offset source.mkv'
    run([tools['ffmpeg'], '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
         '-itsoffset', '0.25', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12:duration=1',
         '-map', '1:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-preset', 'ultrafast',
         '-c:a', 'aac', str(source)])
    def av_offset(path):
        result=json.loads(run([tools['ffprobe'], '-v', 'error', '-show_streams', '-of', 'json', str(path)]))
        video=next(s for s in result['streams'] if s['codec_type']=='video')
        audio=next(s for s in result['streams'] if s['codec_type']=='audio')
        return float(video['start_time'])-float(audio['start_time'])
    original_offset = av_offset(source)
    assert original_offset > 0.2
    settings=replace(Settings(), engine='realcugan', cugan_syncgap=1, scale=2, target='native', aspect='fit',
                     preset='ultrafast', audio='copy', copy_subtitles=False, recover_shutdown=False)
    runner=QueueRunner(); logs=[]; runner.log.connect(logs.append)
    job=runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools, timeout=180000)
    assert job.state=='done', '\n'.join(logs)
    assert av_offset(job.output) == pytest.approx(original_offset, abs=0.02)


def test_unsynced_videos_encode_with_their_own_resolutions(app, tools, media_files, tmp_path, monkeypatch):
    import shutil
    from anime_video_upscaler.gui import MainWindow
    from anime_video_upscaler.preview import PreviewPanel
    source,_,_=media_files
    second=tmp_path/'second video.mp4'
    shutil.copyfile(source,second)
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools',lambda:tools)
    monkeypatch.setattr(PreviewPanel,'set_source',lambda self, path: None)
    window=MainWindow()
    window.add_paths([str(source),str(second)])
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0)
    window.store.update_many(engine='none',target='custom',width=192,height=128,
                             aspect='fit',preset='ultrafast',audio='off',copy_subtitles=False)
    window.table.selectRow(1)
    window.store.update_many(engine='none',target='custom',width=288,height=192,
                             aspect='fit',preset='ultrafast',audio='off',copy_subtitles=False)
    first,other=window.runner.jobs
    assert (first.settings.width,other.settings.width)==(192,288)
    logs=[]; window.runner.log.connect(logs.append)
    wait_runner(app,window.runner,tools,timeout=90000)
    assert first.state==other.state=='done', '\n'.join(logs)
    dimensions=[]
    for job in (first,other):
        probe=json.loads(run([tools['ffprobe'],'-v','error','-show_streams','-of','json',str(job.output)]))
        video=next(stream for stream in probe['streams'] if stream['codec_type']=='video')
        dimensions.append((video['width'],video['height']))
    assert dimensions==[(192,128),(288,192)]
    window.close()


def test_lossless_sample_is_visible_in_real_qt_playback(app, tools, media_files, tmp_path):
    """The Qt decoder treated FFV1 bgr0's padding byte as alpha=0 on Windows."""
    import time
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
    clip=tmp_path/'playback-yuv.mkv'
    run([tools['ffmpeg'], '-v','error','-i',str(media_files[0]),'-t','1',
         '-an','-c:v','ffv1','-pix_fmt','yuv444p',str(clip)])
    player=QMediaPlayer(); sink=QVideoSink(); frames=[]
    player.setVideoSink(sink)
    sink.videoFrameChanged.connect(lambda frame: frames.append(frame.toImage()) if frame.isValid() else None)
    player.setSource(QUrl.fromLocalFile(str(clip)));player.play()
    deadline=time.monotonic()+8
    while not frames and time.monotonic()<deadline:
        app.processEvents();time.sleep(.02)
    player.stop();player.setSource(QUrl())
    assert frames, player.errorString()
    frame=next(image for image in frames if not image.isNull())
    assert frame.pixelColor(frame.width()//2,frame.height()//2).alpha()==255
    player.deleteLater();sink.deleteLater()


def test_gui_start_reprocesses_selected_video_and_preserves_previous_output(app,tools,media_files,tmp_path,monkeypatch):
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools',lambda:tools)
    window=MainWindow()
    window.add_paths([str(media_files[0])])
    window.sync_checkbox.setChecked(False)
    window.store.set(replace(Settings(),engine='none',target='custom',width=192,height=128,
                             preset='ultrafast',output_dir=str(tmp_path/'outputs')))
    job=window.runner.jobs[0]
    job.state='failed'
    window._busy_changed(False)
    assert window.start_button.isEnabled()
    outputs=[]
    for _ in range(2):
        loop=QEventLoop();timer=QTimer();timer.setSingleShot(True);timer.timeout.connect(loop.quit)
        callback=lambda busy:loop.quit() if not busy else None
        window.runner.busy_changed.connect(callback)
        timer.start(15000)
        window.start_button.click()
        loop.exec()
        timer.stop();window.runner.busy_changed.disconnect(callback)
        assert not window.runner.busy and job.state=='done',job.error
        assert job.output.is_file()
        outputs.append(job.output)
        assert window.start_button.isEnabled()
    assert outputs[0]!=outputs[1] and all(path.is_file() for path in outputs)
    window.close()
