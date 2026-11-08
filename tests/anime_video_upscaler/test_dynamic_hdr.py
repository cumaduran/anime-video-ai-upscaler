"""Synthetic HDR10+/Dolby Vision round trips using the bundled tools."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

import pytest

from anime_video_upscaler.config import Settings
from anime_video_upscaler.dynamic_hdr import DynamicHDRWorker, _hdr10_signatures, _validate_dovi_display_matrix, adjust_dovi_active_area, duplicate_hdr10plus
from anime_video_upscaler.pipeline import Media, validate_media
from anime_video_upscaler.runner import QueueRunner
from test_integration import wait_runner

pytestmark = pytest.mark.integration


def test_interpolated_hdr10plus_metadata_preserves_scene_alignment():
    frames = [{'SceneId': 0, 'SceneFrameIndex': 0, 'SequenceFrameIndex': 0, 'value': 'a'},
              {'SceneId': 1, 'SceneFrameIndex': 0, 'SequenceFrameIndex': 1, 'value': 'b'}]
    output = duplicate_hdr10plus({'SceneInfo': frames}, 3)
    assert [frame['value'] for frame in output['SceneInfo']] == ['a', 'a', 'a', 'b', 'b', 'b']
    assert [frame['SequenceFrameIndex'] for frame in output['SceneInfo']] == list(range(6))
    assert output['SceneInfoSummary'] == {'SceneFirstFrameIndex': [0, 3], 'SceneFrameNumbers': [3, 3]}
    assert len(frames) == 2


def test_hdr10plus_verification_compares_all_image_metadata():
    frame = {'SceneId': 0, 'SceneFrameIndex': 0, 'SequenceFrameIndex': 0,
             'LuminanceParameters': {'AverageRGB': 42}, 'AdditionalImageMetadata': {'value': 1}}
    modified = dict(frame, SequenceFrameIndex=3, AdditionalImageMetadata={'value': 2})
    assert _hdr10_signatures({'SceneInfo': [frame]}) != _hdr10_signatures({'SceneInfo': [modified]})


@pytest.mark.parametrize('timestamps,valid', [('0.000\n0.200\n0.400\n', True),
                                               ('0.000\n0.300\n0.400\n', False)])
def test_dynamic_hdr_checks_actual_presentation_timestamps(app, tmp_path, monkeypatch, timestamps, valid):
    worker = DynamicHDRWorker('prepare', {'ffprobe': 'ffprobe'}, tmp_path / 'source.mkv', tmp_path,
                              Media(100, 100, 1, 5), Settings())
    monkeypatch.setattr(worker, '_call', lambda args: (tmp_path / 'dynamic-source-timestamps.csv').write_text(timestamps))
    if valid:
        assert worker._source_frame_count() == 3
    else:
        with pytest.raises(ValueError, match='constant frame rate'):
            worker._source_frame_count()


def test_profile7_fel_is_not_silently_discarded(app, tmp_path, monkeypatch):
    tool_dir = tmp_path / 'hdr-metadata'; tool_dir.mkdir()
    for name in ('dovi_tool.exe', 'hdr10plus_tool.exe'):
        (tool_dir / name).touch()
    worker = DynamicHDRWorker('prepare', {'ffmpeg': str(tmp_path / 'ffmpeg/ffmpeg.exe')}, tmp_path / 'source.mkv',
                              tmp_path, Media(100, 100, 1, 5, color_transfer='smpte2084'),
                              replace(Settings(), codec='libx265'))
    monkeypatch.setattr(worker, '_stream_extract', lambda executable, action, output, missing: action == 'dovi')
    monkeypatch.setattr(worker, '_source_frame_count', lambda: 1)
    monkeypatch.setattr(worker, '_dovi_count', lambda path, executable: 1)
    monkeypatch.setattr(worker, '_probe', lambda *args: {'streams': [{'side_data_list': [
        {'side_data_type': 'DOVI configuration record', 'dv_profile': 7, 'bl_present_flag': 1,
         'el_present_flag': 1, 'rpu_present_flag': 1}]}]})
    def fake_call(args):
        if args[1] == 'export':
            (tmp_path / 'dovi-profile7-source.json').write_text('[{"dovi_profile":7,"el_type":"FEL"}]')
        return 'Summary:\n  Frames: 1\n  Profile: 7 (FEL)\n'
    monkeypatch.setattr(worker, '_call', fake_call)
    with pytest.raises(ValueError, match='FEL processing tools are missing'):
        worker._prepare()


def test_real_profile7_fel_sample_separates_both_layers(app, tmp_path):
    source = Path(os.environ.get('ANIME_TEST_DOVI_FEL_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe)):
        pytest.skip('Set ANIME_TEST_DOVI_FEL_SAMPLE and FFmpeg tools for the real FEL sample')
    probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', source]).stdout)
    worker = DynamicHDRWorker('prepare', {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)}, source,
                              tmp_path, Media.from_probe(probe), replace(Settings(), codec='libx265'))
    result = worker._prepare()
    assert result.fel_bl.stat().st_size and result.fel_el.stat().st_size
    assert result.converted_profile7 and result.dovi.is_file()


def test_real_profile7_fel_comparison_preserves_both_layers(app, tmp_path):
    from anime_video_upscaler.profile7_sample import Profile7SampleWorker
    from anime_video_upscaler.pipeline import high_precision_output_filters
    source = Path(os.environ.get('ANIME_TEST_DOVI_FEL_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe)):
        pytest.skip('Set ANIME_TEST_DOVI_FEL_SAMPLE and FFmpeg tools')
    probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', source]).stdout)
    media = Media.from_probe(probe)
    settings = replace(Settings(), engine='libplacebo', high_precision=True, codec='libx265', pixel_format='yuv420p10le')
    worker = Profile7SampleWorker({'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)}, source, tmp_path, media, settings, 0, 1)
    worker.run()
    assert not worker.error, worker.error
    clip = tmp_path / 'sample.mkv'
    result = _run([ffmpeg, '-v', 'error', '-i', clip, '-frames:v', '1', '-pix_fmt', 'yuv420p10le', '-f', 'framemd5', '-']).stdout.splitlines()[-1].split(b',')[-1].strip()
    helper = ffmpeg.parent.parent / 'fel-baker'
    baker = subprocess.Popen([str(x) for x in (helper / 'python/python.exe', '-u', helper / 'fel-bake.py',
        tmp_path / 'fel-base.hevc', tmp_path / 'fel-enhancement.hevc', 1, media.width, media.height, helper, '', 0, 0, 1)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reference = subprocess.run([str(ffmpeg), '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'gbrp16le',
        '-video_size', f'{media.width}x{media.height}', '-framerate', str(media.fps), '-i', 'pipe:0',
        '-vf', ','.join(high_precision_output_filters(media)), '-frames:v', '1', '-pix_fmt', 'yuv420p10le',
        '-f', 'framemd5', '-'], stdin=baker.stdout, capture_output=True, check=True, timeout=120)
    baker.stdout.close()
    assert baker.wait(timeout=30) == 0, baker.stderr.read().decode('utf-8', 'replace')
    assert result == reference.stdout.splitlines()[-1].split(b',')[-1].strip()


def test_profile7_mel_sample_preparation(app, tmp_path, monkeypatch):
    from anime_video_upscaler.profile7_sample import Profile7SampleWorker
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    dovi = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    hdr = ffmpeg.parent.parent / 'hdr-metadata/hdr10plus_tool.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, dovi, hdr)):
        pytest.skip('Real media tools are required')
    source = _sample(tmp_path, ffmpeg, 'p7mel', hdr, dovi)
    probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', source]).stdout)
    media = Media.from_probe(probe)
    work = tmp_path / 'preview'; work.mkdir()
    settings = replace(Settings(), high_precision=True, codec='libx265', pixel_format='yuv420p10le')
    actions = []
    original = DynamicHDRWorker._stream_extract
    def record_action(self, executable, action, output, missing):
        actions.append(action)
        return original(self, executable, action, output, missing)
    monkeypatch.setattr(DynamicHDRWorker, '_stream_extract', record_action)
    worker = Profile7SampleWorker({'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)}, source, work, media, settings, .2, .6)
    worker.run()
    assert not worker.error, worker.error
    assert actions == ['dovi']
    frames = json.loads(_run([ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', work / 'sample.mkv']).stdout)['streams'][0]
    assert int(frames['nb_read_frames']) == 3


def test_profile7_comparison_rejects_unverified_variable_timing(app, tmp_path):
    from anime_video_upscaler.profile7_sample import Profile7SampleWorker
    media = Media(100, 100, 5, 24, variable_rate=True, dovi_profile=7)
    worker = Profile7SampleWorker({}, tmp_path / 'source.mkv', tmp_path, media, Settings(), 0, 1)
    worker.run()
    assert 'requires constant frame rate' in worker.error
    assert not (tmp_path / 'sample.mkv').exists()


@pytest.mark.gpu
def test_real_profile7_fel_comparison_dialog(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.gui import SettingsStore
    source = Path(os.environ.get('ANIME_TEST_DOVI_FEL_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe, native)):
        pytest.skip('Set ANIME_TEST_DOVI_FEL_SAMPLE and real GPU tools')
    monkeypatch.setenv('ANIME_UPSCALER_HOME', str(tmp_path / 'settings'))
    probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', source]).stdout)
    media = replace(Media.from_probe(probe), duration=1)
    settings = replace(Settings(), engine='libplacebo', high_precision=True, codec='libx265', pixel_format='yuv420p10le',
                       preset='ultrafast', target='custom', width=256, height=144)
    tools = {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}
    dialog = CompareDialog(source, media, 0, settings, tools, lambda text: text, SettingsStore)
    dialog.length.setValue(.5)
    loop = QEventLoop(); timer = QTimer(); timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    dialog.runner.busy_changed.connect(lambda busy: loop.quit() if not busy else None)
    timer.start(120000); dialog.start(); loop.exec(); timer.stop()
    assert dialog.job and dialog.job.state in ('done', 'done_warning'), dialog.status.text()
    assert dialog.clip.is_file() and dialog.job.output.is_file()
    dialog.reject()


@pytest.mark.parametrize('orientation,width,height,filter_name', [
    ('0,-65536,65536,0', 2160, 3840, 'transpose=2'),
    ('-65536,0,0,65536', 3840, 2160, 'hflip'),
])
def test_real_fel_orientation_matches_reference(app, tmp_path, orientation, width, height, filter_name):
    source = Path(os.environ.get('ANIME_TEST_DOVI_FEL_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe)):
        pytest.skip('Set ANIME_TEST_DOVI_FEL_SAMPLE and FFmpeg tools')
    probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', source]).stdout)
    worker = DynamicHDRWorker('prepare', {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)}, source,
                              tmp_path, Media.from_probe(probe), replace(Settings(), codec='libx265'))
    data = worker._prepare()
    helper = ffmpeg.parent.parent / 'fel-baker'
    def digest(matrix, w, h, filters):
        command = [helper / 'python/python.exe', '-u', helper / 'fel-bake.py', data.fel_bl, data.fel_el,
                   data.source_frames, w, h, helper, matrix, 0]
        process = subprocess.Popen([str(arg) for arg in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        result = subprocess.run([str(ffmpeg), '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'gbrp16le',
                                 '-s', f'{w}x{h}', '-i', 'pipe:0', *filters, '-f', 'framemd5', '-'],
                                stdin=process.stdout, capture_output=True, check=True, timeout=120)
        process.stdout.close()
        assert process.wait(timeout=30) == 0, process.stderr.read().decode('utf-8', 'replace')
        return result.stdout.decode().splitlines()[-1].split(',')[-1].strip()
    assert digest('', 3840, 2160, ['-vf', filter_name]) == digest(orientation, width, height, [])


@pytest.mark.gpu
def test_real_profile7_fel_full_pipeline(app, tmp_path):
    source = Path(os.environ.get('ANIME_TEST_DOVI_FEL_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe, native)):
        pytest.skip('Set ANIME_TEST_DOVI_FEL_SAMPLE and the real GPU tools')
    settings = replace(Settings(), engine='libplacebo', high_precision=True, codec='libx265',
                       pixel_format='yuv420p10le', preset='ultrafast', target='custom', width=256, height=144,
                       container='mkv', output_dir=str(tmp_path), copy_subtitles=False, audio='off')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=240_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-30:])
    assert 'profile 7 FEL was combined' in job.error
    probe = json.loads(_run([ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', job.output]).stdout)
    video = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
    assert int(video['nb_read_frames']) == 1
    assert any(side.get('dv_profile') == 8 for side in video.get('side_data_list', []))


@pytest.mark.gpu
def test_real_hdr10plus_source_survives_processing(app, tmp_path):
    source = Path(os.environ.get('ANIME_TEST_HDR10PLUS_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe, native)):
        pytest.skip('Set ANIME_TEST_HDR10PLUS_SAMPLE and the real GPU tools')
    wrapped = tmp_path / 'real-hdr10plus.mkv'
    _run([ffmpeg, '-v', 'error', '-r', '25', '-i', source, '-c:v', 'copy', wrapped])
    settings = replace(Settings(), engine='libplacebo', high_precision=True, codec='libx265',
                       pixel_format='yuv420p10le', preset='ultrafast', target='custom', width=256, height=144,
                       container='mkv', output_dir=str(tmp_path), copy_subtitles=False, audio='off')
    runner = QueueRunner(); job = runner.enqueue([wrapped], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-25:])
    assert 'Dynamic HDR frame count, metadata and output timing verified.' in logs


def test_real_dolby_profile84_is_rejected_with_clear_explanation(app, tmp_path):
    source = Path(os.environ.get('ANIME_TEST_DOVI_P84_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe)):
        pytest.skip('Set ANIME_TEST_DOVI_P84_SAMPLE and FFmpeg tools')
    probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', source]).stdout)
    media = Media.from_probe(probe)
    worker = DynamicHDRWorker('prepare', {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)}, source,
                              tmp_path, media, replace(Settings(), high_precision=True, codec='libx265', pixel_format='yuv420p10le'))
    with pytest.raises(ValueError, match='profile 8.4'):
        worker._prepare()


def test_profile5_requires_the_high_precision_conversion_path():
    probe = {'streams': [{'codec_type': 'video', 'index': 0, 'width': 100, 'height': 100,
                          'avg_frame_rate': '5/1', 'r_frame_rate': '5/1', 'side_data_list': [
                              {'side_data_type': 'DOVI configuration record', 'dv_profile': 5}]}]}
    with pytest.raises(ValueError, match='profile 5 requires high-precision AI'):
        validate_media(Settings(), Media.from_probe(probe))
    settings = replace(Settings(), high_precision=True, codec='libx265', pixel_format='yuv420p10le')
    validate_media(settings, Media.from_probe(probe))


def test_mirrored_dolby_display_matrix_is_accepted():
    stream = {'side_data_list': [{'side_data_type': 'Display Matrix', 'displaymatrix':
              '00000000: -65536 0 0\n00000001: 0 65536 0\n00000002: 0 0 1073741824\n'}]}
    _validate_dovi_display_matrix(stream)


def test_dolby_active_area_accounts_for_scaling_and_padding():
    data = {'active_area': {'crop': True, 'presets': [{'id': 0, 'left': 0, 'right': 0, 'top': 10, 'bottom': 10}],
                            'edits': {'0-4': 0}}}
    media = Media(100, 100, 1, 5)
    settings = replace(Settings(), target='custom', width=400, height=200, aspect='pad')
    result, changed = adjust_dovi_active_area(data, media, settings)
    assert changed and result['active_area']['presets'][0] == {'id': 0, 'left': 100, 'right': 100, 'top': 20, 'bottom': 20}
    assert data['active_area']['presets'][0]['top'] == 10


@pytest.mark.parametrize('rotation,expected', [
    (90, (24, 32, 16, 8)), (180, (16, 8, 32, 24)), (270, (32, 24, 8, 16))])
def test_dolby_active_area_rotates_with_the_video(rotation, expected):
    data = {'active_area': {'presets': [{'id': 0, 'left': 8, 'right': 16, 'top': 24, 'bottom': 32}]}}
    media = Media(192 if rotation % 180 else 256, 256 if rotation % 180 else 192, 1, 5, rotation=rotation)
    result, changed = adjust_dovi_active_area(data, media, replace(Settings(), engine='rife', target='native'))
    assert changed
    preset = result['active_area']['presets'][0]
    assert tuple(preset[key] for key in ('left', 'right', 'top', 'bottom')) == expected


def _run(args, cwd=None, timeout=120):
    return subprocess.run([str(arg) for arg in args], cwd=cwd, check=True, capture_output=True,
                          timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


@pytest.mark.gpu
@pytest.mark.parametrize('windows,engine,rotate,bframes,vfr,dovi,codec', [
    (2, 'rife', False, 0, False, False, 'libx265'), (3, 'libplacebo', True, 0, False, False, 'libx265'),
    (2, 'libplacebo', False, 2, False, False, 'libx265'), (3, 'rife', False, 0, True, False, 'libx265'),
    (2, 'rife', False, 0, False, True, 'libx265'), (3, 'realcugan', False, 0, False, False, 'hevc_nvenc')])
def test_multiple_hdr10plus_windows_roundtrip(app, tmp_path, windows, engine, rotate, bframes, vfr, dovi, codec):
    from anime_video_upscaler.hdr10plus_windows import (extract_hdr10plus_from_access_unit, inject_hdr10plus_into_access_unit,
        parse_hdr10plus_payload, replace_window_geometries)
    from test_hdr10plus_windows import _payload
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    if not all(path.is_file() for path in (ffmpeg, ffprobe, native)):
        pytest.skip('Real media tools and GPU engine are required')
    raw = tmp_path / 'raw.hevc'
    color = 'setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv'
    _run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=256x144:r=5:d=1.2', '-vf', color,
          '-c:v', 'libx265', '-pix_fmt', 'yuv420p10le', '-preset', 'ultrafast', '-bf', str(bframes),
          '-x265-params', 'log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc',
          '-f', 'hevc', raw])
    worker = DynamicHDRWorker('prepare', {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe)}, raw, tmp_path,
                              Media(256, 144, 1.2, 5), Settings())
    ranges = worker._packet_ranges(raw, 'fixture')
    orig = parse_hdr10plus_payload(_payload(windows))
    injected = tmp_path / 'windows.hevc'
    with raw.open('rb') as inp, injected.open('wb') as out:
        for index, (offset, size) in enumerate(ranges):
            geo = tuple(replace(g, center_x=g.center_x + index) for g in orig.geometries)
            payload = replace_window_geometries(orig.raw, geo)
            inp.seek(offset); out.write(inject_hdr10plus_into_access_unit(inp.read(size), payload))
    if dovi:
        dovi_tool = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
        dovi_config = tmp_path / 'dovi-fixture.json'
        dovi_config.write_text(json.dumps({'cm_version': 'V40', 'length': len(ranges)}), encoding='utf-8')
        rpu = tmp_path / 'dovi-fixture.bin'
        combined = tmp_path / 'both.hevc'
        _run([dovi_tool, 'generate', '-p', '8.1', '-j', dovi_config, '-o', rpu])
        _run([dovi_tool, 'inject-rpu', '-i', injected, '--rpu-in', rpu, '-o', combined])
        injected = combined
    source = tmp_path / ('windows.mkv' if bframes or vfr else 'windows.mp4')
    if bframes or vfr:
        mkvmerge = ffmpeg.parent.parent / 'hdr-metadata/mkvmerge.exe'
        args = [mkvmerge, '-q', '--default-duration', '0:5fps']
        if vfr:
            times = tmp_path / 'times.txt'
            times.write_text('# timestamp format v2\n0\n200\n500\n800\n950\n1300\n1600\n', encoding='utf-8')
            args += ['--timestamps', f'0:{times}']
        _run(args + ['-o', source, injected])
    else:
        _run([ffmpeg, '-v', 'error', '-r', '5', '-i', injected, '-c:v', 'copy',
              *(['-bsf:v', 'dovi_rpu=compression=none', '-strict', 'unofficial'] if dovi else []), source])
    if rotate:
        rotated = tmp_path / 'windows-rotated.mp4'
        _run([ffmpeg, '-v', 'error', '-display_rotation', '90', '-i', source, '-c', 'copy', rotated])
        source = rotated
    settings = replace(Settings(), engine=engine, cugan_model='models-nose', fps_multiplier=2, high_precision=True, codec=codec,
                       pixel_format='p010le' if codec == 'hevc_nvenc' else 'yuv420p10le',
                       preset='p4' if codec == 'hevc_nvenc' else 'ultrafast', target='custom', width=384, height=384,
                       aspect='pad', container='mkv', output_dir=str(tmp_path), copy_subtitles=False, audio='off')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-30:])
    assert 'Dynamic HDR frame count, metadata and output timing verified.' in logs
    probe = json.loads(_run([ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', job.output]).stdout)
    video = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
    assert int(video['nb_read_frames']) == len(ranges) * (2 if engine == 'rife' else 1)
    if dovi:
        assert any(side.get('dv_profile') == 8 for side in video.get('side_data_list', []))
    pts = [int(line) for line in _run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'packet=pts', '-of', 'csv=p=0', source]).stdout.decode().splitlines() if line]
    coding_indices = [i for _, i in sorted((pts[i], i) for i in range(len(pts)))]
    expected_indices = [i for i in coding_indices for _ in range(2 if engine == 'rife' else 1)]
    output_raw = tmp_path / 'verified-output.hevc'
    _run([ffmpeg, '-v', 'error', '-i', job.output, '-map', '0:v:0', '-c:v', 'copy',
          '-bsf:v', 'hevc_mp4toannexb', '-f', 'hevc', output_raw])
    output_ranges = worker._packet_ranges(output_raw, 'test-result')
    with output_raw.open('rb') as stream:
        for (offset, size), index in zip(output_ranges, expected_indices):
            stream.seek(offset)
            payload = parse_hdr10plus_payload(extract_hdr10plus_from_access_unit(stream.read(size)))
            geometry = payload.geometries[0]
            if rotate:
                assert (geometry.left, geometry.top, geometry.right, geometry.bottom) == (114, 83, 234, 368)
                assert (geometry.center_x, geometry.center_y, geometry.angle) == (159, round((195 - index + .5) * 1.5 - .5), 115)
            else:
                assert (geometry.left, geometry.top, geometry.right, geometry.bottom) == (15, 114, 300, 234)
                assert (geometry.center_x, geometry.center_y, geometry.angle) == (round((60 + index + .5) * 1.5 - .5), 159, 25)
            assert (geometry.inner_major, geometry.outer_major, geometry.outer_minor) == (30, 60, 45)
            assert payload.windows == orig.windows
    if vfr:
        output_times = [float(line.split(',')[0]) for line in _run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
                         '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'csv=p=0', job.output]).stdout.decode().splitlines() if line.split(',')[0]]
        expected_times = [0, .1, .2, .35, .5, .65, .8, .875, .95, 1.125, 1.3, 1.45]
        assert len(output_times) == len(expected_times)
        assert all(abs(actual - expected) < .002 for actual, expected in zip(output_times, expected_times))


def _sample(tmp_path, ffmpeg, kind, hdr_tool, dovi, audio_offset=0, rotation=0, mirror=''):
    item = {'BezierCurveData': {'Anchors': [143, 298, 447, 592, 731, 864, 891, 917, 938],
                                'KneePointX': 164, 'KneePointY': 240},
            'LuminanceParameters': {'AverageRGB': 263, 'LuminanceDistributions': {
                'DistributionIndex': [1, 5, 10, 25, 50, 75, 90, 95, 99],
                'DistributionValues': [0, 6080, 92, 1, 4, 107, 726, 1784, 5843]},
                'MaxScl': [7768, 6589, 6912]},
            'NumberOfWindows': 1, 'TargetedSystemDisplayMaximumLuminance': 400}
    frames = []
    for index in range(6):
        frame = json.loads(json.dumps(item))
        frame['LuminanceParameters']['AverageRGB'] += index * 10
        frame.update(SceneId=index, SceneFrameIndex=0, SequenceFrameIndex=index)
        frames.append(frame)
    metadata = {'JSONInfo': {'HDR10plusProfile': 'B', 'Version': '1.0'}, 'SceneInfo': frames,
                'SceneInfoSummary': {'SceneFirstFrameIndex': list(range(6)), 'SceneFrameNumbers': [1] * 6},
                'ToolInfo': {'Tool': 'test', 'Version': '1.0'}}
    (tmp_path / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
    color = 'setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc:range=tv'
    static = ('log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:'
              'master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400')
    source = tmp_path / 'source.mkv'
    if kind == 'hdr10plus':
        _run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=256x256:r=5:d=1.2',
              '-itsoffset', str(audio_offset), '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1.2',
              '-vf', color, '-c:v', 'libx265', '-preset', 'ultrafast', '-bf', '2',
              '-x265-params', static + ':dhdr10-info=metadata.json', '-pix_fmt', 'yuv420p10le',
              '-c:a', 'aac', '-shortest', source], cwd=tmp_path)
    else:
        config = {'cm_version': 'V40', 'length': 6, 'level6': {'max_display_mastering_luminance': 1000,
                  'min_display_mastering_luminance': 1, 'max_content_light_level': 1000, 'max_frame_average_light_level': 400}}
        (tmp_path / 'dovi.json').write_text(json.dumps(config), encoding='utf-8')
        _run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=s={"256x192" if rotation or mirror else "256x256"}:r=5:d=1.2', '-vf', color,
              '-c:v', 'libx265', '-preset', 'ultrafast', '-bf', '0', '-x265-params', static,
              '-pix_fmt', 'yuv420p10le', '-f', 'hevc', tmp_path / 'base.hevc'])
        _run([dovi, 'generate', '-p', '8.1', '-j', tmp_path / 'dovi.json', '--hdr10plus-json',
              tmp_path / 'metadata.json', '--hdr10plus-peak-source', 'max-scl', '-o', tmp_path / 'source-rpu.bin'])
        rpu = tmp_path / 'source-rpu.bin'
        if kind == 'p7mel':
            (tmp_path / 'mel.json').write_text('{"mode":1}', encoding='utf-8')
            rpu = tmp_path / 'source-mel-rpu.bin'
            _run([dovi, 'editor', '-i', tmp_path / 'source-rpu.bin', '-j', tmp_path / 'mel.json', '-o', rpu])
        if rotation or mirror:
            area = {'active_area': {'crop': False, 'presets': [{'id': 0, 'left': 8, 'right': 16, 'top': 24, 'bottom': 32}],
                                    'edits': {'0-5': 0}}}
            (tmp_path / 'area.json').write_text(json.dumps(area), encoding='utf-8')
            rpu = tmp_path / 'source-area-rpu.bin'
            _run([dovi, 'editor', '-i', tmp_path / 'source-rpu.bin', '-j', tmp_path / 'area.json', '-o', rpu])
        base = tmp_path / 'base.hevc'
        if kind == 'both':
            _run([hdr_tool, 'inject', '-i', base, '-j', tmp_path / 'metadata.json', '-o', tmp_path / 'base-plus.hevc'])
            base = tmp_path / 'base-plus.hevc'
        _run([dovi, 'inject-rpu', '-i', base, '--rpu-in', rpu,
              '-o', tmp_path / 'dovi.hevc'])
        _run([ffmpeg, '-v', 'error', '-fflags', '+genpts', '-r', '5', '-i', tmp_path / 'dovi.hevc',
              '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1.2',
              '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-bsf:v', 'dovi_rpu=compression=none',
              '-c:a', 'aac', source])
    if kind == 'p7mel':
        labeled = tmp_path / 'profile7-mel.mp4'
        _run([ffmpeg, '-v', 'error', '-i', source, '-c', 'copy', '-bsf:v', 'dovi_rpu=compression=none',
              '-strict', 'unofficial', labeled])
        # The synthetic bitstream contains an RPU generated in P7 MEL mode.
        # Explicitly signal P7 in the MP4 config so it exercises source detection.
        data = bytearray(labeled.read_bytes()); position = data.find(b'dvvC')
        assert position >= 0 and data[position + 6] == 0x10
        data[position + 6] = 0x0e
        data[position + 7] |= 0x02
        labeled.write_bytes(data)
        return labeled
    if rotation or mirror:
        rotated = tmp_path / 'rotated.mp4'
        _run([ffmpeg, '-v', 'error', '-display_rotation', str(rotation), *([f'-display_{mirror}flip'] if mirror else []), '-i', source, '-c', 'copy',
              '-bsf:v', 'dovi_rpu=compression=none', '-strict', 'unofficial', rotated])
        return rotated
    return source


def test_hevc_nal_scan_distinguishes_actual_dolby_metadata(app, tmp_path):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    hdr_tool = ffmpeg.parent.parent / 'hdr-metadata/hdr10plus_tool.exe'
    dovi = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, hdr_tool, dovi)):
        pytest.skip('Real media tools are required')
    source = _sample(tmp_path, ffmpeg, 'dovi', hdr_tool, dovi)
    plain = tmp_path / 'plain.mkv'
    _run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=96x64:rate=12:duration=1',
          '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', 'log-level=error', plain])
    media = Media(256, 256, 1.2, 5)
    worker = DynamicHDRWorker('prepare', {'ffmpeg': str(ffmpeg)}, source, tmp_path, media, Settings())
    assert worker._has_dovi_rpu_nal()
    worker.source = plain
    assert not worker._has_dovi_rpu_nal()


def _profile5_sample(tmp_path, ffmpeg, dovi):
    config = tmp_path / 'profile5.json'
    config.write_text(json.dumps({'cm_version': 'V40', 'length': 6}), encoding='utf-8')
    base, rpu, combined = (tmp_path / name for name in ('profile5-base.hevc', 'profile5-rpu.bin', 'profile5.hevc'))
    _run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=256x256:r=5:d=1.2',
          '-c:v', 'libx265', '-pix_fmt', 'yuv420p10le', '-preset', 'ultrafast', '-bf', '0',
          '-x265-params', 'log-level=error:range=full', '-f', 'hevc', base])
    _run([dovi, 'generate', '-p', '5', '-j', config, '-o', rpu])
    _run([dovi, 'inject-rpu', '-i', base, '--rpu-in', rpu, '-o', combined])
    source = tmp_path / 'profile5.mp4'
    _run([ffmpeg, '-v', 'error', '-r', '5', '-i', combined, '-c:v', 'copy',
          '-bsf:v', 'dovi_rpu=compression=none', '-strict', 'unofficial', source])
    return source


def test_profile5_rotation_retains_color_reshaping(app, tmp_path):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    dovi = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    converter = ffmpeg.parent.parent / 'dovi-ffmpeg/ffmpeg.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, dovi, converter)):
        pytest.skip('Real media tools and Vulkan converter are required')
    source = _profile5_sample(tmp_path, ffmpeg, dovi)
    rotated = tmp_path / 'profile5-rotated.mp4'
    _run([ffmpeg, '-v', 'error', '-display_rotation', '90', '-i', source, '-c', 'copy',
          '-bsf:v', 'dovi_rpu=compression=none', '-strict', 'unofficial', rotated])
    from anime_video_upscaler.pipeline import display_orientation_filters, dovi5_pq_filters
    media = Media.from_probe(json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', rotated]).stdout))
    common = [converter, '-v', 'error', '-init_hw_device', 'vulkan=vk:0', '-filter_hw_device', 'vk']
    output = ['-frames:v', '1', '-pix_fmt', 'yuv444p16le', '-f', 'rawvideo', '-']
    automatic = _run(common + ['-noautorotate', '-i', rotated, '-vf', ','.join(dovi5_pq_filters() + display_orientation_filters(media))] + output).stdout
    reference = _run(common + ['-noautorotate', '-i', rotated, '-vf', ','.join(dovi5_pq_filters() + ['transpose=2'])] + output).stdout
    assert automatic == reference
    sample = tmp_path / 'oriented-clip.mkv'
    _run(common + ['-noautorotate', '-i', rotated, '-vf', ','.join(dovi5_pq_filters() + display_orientation_filters(media)),
                   '-frames:v', '1', '-c:v', 'ffv1', sample])
    stream = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', sample]).stdout)['streams'][0]
    assert not any(item.get('side_data_type') == 'Display Matrix' for item in stream.get('side_data_list', []))


@pytest.mark.gpu
@pytest.mark.parametrize('engine,codec,container', [('rife', 'libx265', 'mkv'), ('realcugan', 'hevc_nvenc', 'mp4')])
def test_profile5_vulkan_conversion_preserves_frame_metadata(app, tmp_path, engine, codec, container):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    dovi = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    converter = ffmpeg.parent.parent / 'dovi-ffmpeg/ffmpeg.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, native, dovi, converter)):
        pytest.skip('Real media tools, Vulkan converter and GPU engine are required')
    source = _profile5_sample(tmp_path, ffmpeg, dovi)
    settings = replace(Settings(), engine=engine, cugan_model='models-nose', noise=0, scale=2,
                       fps_multiplier=2, high_precision=True, codec=codec,
                       pixel_format='p010le' if codec == 'hevc_nvenc' else 'yuv420p10le',
                       preset='p4' if codec == 'hevc_nvenc' else 'ultrafast', target='native', container=container,
                       output_dir=str(tmp_path), copy_subtitles=False, audio='off')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-25:])
    assert 'Dolby Vision profile 5 Vulkan color conversion verified.' in logs
    assert 'profile 5 was converted to profile 8.1' in job.error
    data = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', job.output]).stdout)
    video = next(stream for stream in data['streams'] if stream['codec_type'] == 'video')
    assert (video['color_primaries'], video['color_transfer'], video['color_space']) == ('bt2020', 'smpte2084', 'bt2020nc')
    assert any(side.get('dv_profile') == 8 and side.get('dv_bl_signal_compatibility_id') == 1
               for side in video.get('side_data_list', []))
    if engine == 'rife':
        import array
        from anime_video_upscaler.pipeline import dovi5_pq_filters, high_precision_input_filters, high_precision_output_filters, preview_command
        source_probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', source]).stdout)
        media = Media.from_probe(source_probe)
        reference = _run([converter, '-v', 'error', '-init_hw_device', 'vulkan=vk:0', '-filter_hw_device', 'vk',
                          '-i', source, '-vf', ','.join(dovi5_pq_filters() + high_precision_input_filters(media) + high_precision_output_filters(media)),
                          '-frames:v', '1', '-pix_fmt', 'yuv420p10le', '-f', 'rawvideo', '-']).stdout
        rendered = _run([ffmpeg, '-v', 'error', '-i', job.output, '-frames:v', '1', '-pix_fmt', 'yuv420p10le',
                         '-f', 'rawvideo', '-']).stdout
        expected, actual = array.array('H', reference), array.array('H', rendered)
        assert len(expected) == len(actual) and sum(abs(a - b) for a, b in zip(expected, actual)) / len(expected) < 8
        preview = _run(preview_command(settings, {'ffmpeg': str(ffmpeg)}, source, media, 0)).stdout
        assert preview.startswith(b'\x89PNG\r\n\x1a\n')


@pytest.mark.gpu
def test_real_profile5_sample_converts_to_verified_profile81(app, tmp_path):
    source = Path(os.environ.get('ANIME_TEST_DOVI_P5_SAMPLE', ''))
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    if not all(path.is_file() for path in (source, ffmpeg, ffprobe, native)):
        pytest.skip('Set ANIME_TEST_DOVI_P5_SAMPLE and the real GPU tools')
    settings = replace(Settings(), engine='rife', fps_multiplier=2, high_precision=True, codec='libx265',
                       pixel_format='yuv420p10le', preset='ultrafast', target='custom', width=256, height=144,
                       container='mkv', output_dir=str(tmp_path), copy_subtitles=False, audio='off')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=240_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-25:])
    assert 'Dolby Vision profile 5 Vulkan color conversion verified.' in logs
    data = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', job.output]).stdout)
    video = next(stream for stream in data['streams'] if stream['codec_type'] == 'video')
    assert any(side.get('dv_profile') == 8 and side.get('dv_bl_signal_compatibility_id') == 1
               for side in video.get('side_data_list', []))


@pytest.mark.gpu
@pytest.mark.parametrize('kind,engine,codec,container,pad', [('hdr10plus', 'realcugan', 'hevc_nvenc', 'mkv', False),
                                               ('hdr10plus', 'rife', 'libx265', 'mp4', False),
                                               ('dovi', 'realcugan', 'libx265', 'mkv', False),
                                               ('dovi', 'rife', 'hevc_nvenc', 'mp4', False),
                                               ('p7mel', 'realcugan', 'hevc_nvenc', 'mkv', False),
                                               ('p7mel', 'rife', 'libx265', 'mp4', False),
                                               ('both', 'realcugan', 'hevc_nvenc', 'mkv', True),
                                               ('both', 'rife', 'libx265', 'mp4', False)])
def test_dynamic_hdr_roundtrip_with_audio_and_interpolation(app, tmp_path, kind, engine, codec, container, pad):
    if pad:
        tmp_path = tmp_path / 'job,with,commas'
        tmp_path.mkdir()
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    hdr_tool, dovi = ffmpeg.parent.parent / 'hdr-metadata/hdr10plus_tool.exe', ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, native, hdr_tool, dovi)):
        pytest.skip('Real media tools and GPU engine are required')
    audio_offset = .3 if kind == 'hdr10plus' and engine == 'realcugan' else 0
    source = _sample(tmp_path, ffmpeg, kind, hdr_tool, dovi, audio_offset)
    from PySide6.QtGui import QColor, QImage
    subtitle = tmp_path / 'sub.srt'
    subtitle.write_text('1\n00:00:00,000 --> 00:00:01,000\nTest\n', encoding='utf-8')
    watermark = tmp_path / 'logo.png'
    logo = QImage(32, 16, QImage.Format.Format_ARGB32); logo.fill(QColor('white')); assert logo.save(str(watermark))
    settings = replace(Settings(), engine=engine, cugan_model='models-nose', scale=2, noise=0,
                       fps_multiplier=2, high_precision=True, codec=codec,
                       pixel_format='p010le' if codec == 'hevc_nvenc' else 'yuv420p10le',
                       preset='p4' if codec == 'hevc_nvenc' else 'ultrafast', target='custom' if pad else 'native',
                       width=512, height=288, aspect='pad' if pad else 'fit', container=container,
                       output_dir=str(tmp_path), copy_subtitles=False, audio='copy',
                       subtitle_mode='burn', subtitle_path=str(subtitle), watermark_path=str(watermark),
                       watermark_width=32, watermark_margin=4)
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-20:])
    assert 'metadata and output timing verified' in '\n'.join(logs)
    probe = json.loads(_run([ffprobe, '-v', 'error', '-count_frames', '-show_streams', '-of', 'json', job.output]).stdout)
    video = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
    assert int(video['nb_read_frames']) == (12 if engine == 'rife' else 6)
    assert any(stream['codec_type'] == 'audio' for stream in probe['streams'])
    assert video.get('has_b_frames') == 0
    if audio_offset:
        original = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', source]).stdout)
        def audio_delay(streams):
            audio = next(stream for stream in streams if stream['codec_type'] == 'audio')
            video_stream = next(stream for stream in streams if stream['codec_type'] == 'video')
            return float(audio.get('start_time', 0)) - float(video_stream.get('start_time', 0))
        assert abs(audio_delay(probe['streams']) - audio_delay(original['streams'])) < .025
    if kind in ('dovi', 'both', 'p7mel'):
        assert any(side.get('dv_profile') == 8 and side.get('dv_bl_signal_compatibility_id') == 1
                   for side in video.get('side_data_list', []))
    if kind == 'p7mel':
        assert 'profile 7 MEL was converted to profile 8.1' in job.error


@pytest.mark.gpu
@pytest.mark.parametrize('rotation,mirror,expected', [(90, '', (48, 64, 32, 16)),
                                                     (180, '', (32, 16, 64, 48)),
                                                     (270, '', (64, 48, 16, 32)),
                                                     (0, 'h', (32, 16, 48, 64)),
                                                     (0, 'v', (16, 32, 64, 48)),
                                                     (90, 'h', (64, 48, 32, 16)),
                                                     (90, 'v', (48, 64, 16, 32))])
def test_rotated_dolby_vision_active_area_roundtrip(app, tmp_path, rotation, mirror, expected):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    hdr_tool = ffmpeg.parent.parent / 'hdr-metadata/hdr10plus_tool.exe'
    dovi = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, native, hdr_tool, dovi)):
        pytest.skip('Real media tools and GPU engine are required')
    source = _sample(tmp_path, ffmpeg, 'dovi', hdr_tool, dovi, rotation=rotation, mirror=mirror)
    settings = replace(Settings(), engine='realcugan', cugan_model='models-nose', scale=2, noise=0,
                       high_precision=True, codec='libx265', pixel_format='yuv420p10le', preset='ultrafast',
                       target='native', container='mkv', output_dir=str(tmp_path), copy_subtitles=False, audio='copy')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-20:])
    probe = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', job.output]).stdout)
    video = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
    assert (video['width'], video['height']) == ((384, 512) if rotation % 180 else (512, 384))
    assert not any(side.get('rotation') for side in video.get('side_data_list', []))
    if mirror:
        def thumbnail(path):
            return _run([ffmpeg, '-v', 'error', '-i', path, '-vf', 'scale=64:64', '-frames:v', '1',
                         '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-']).stdout
        reference, output = thumbnail(source), thumbnail(job.output)
        reflected = b''.join(reference[(y * 64 + (63 - x)) * 3:(y * 64 + (64 - x)) * 3]
                             for y in range(64) for x in range(64))
        difference = lambda a, b: sum(abs(x - y) for x, y in zip(a, b))
        assert difference(output, reference) < difference(output, reflected) * .8
    _run([ffmpeg, '-v', 'error', '-i', job.output, '-c:v', 'copy', '-an', '-bsf:v', 'hevc_mp4toannexb',
          '-f', 'hevc', tmp_path / 'result.hevc'])
    _run([dovi, 'extract-rpu', '-i', tmp_path / 'result.hevc', '-o', tmp_path / 'result-rpu.bin'])
    _run([dovi, 'export', '-i', tmp_path / 'result-rpu.bin', '-d', 'level5=result-area.json'], cwd=tmp_path)
    preset = json.loads((tmp_path / 'result-area.json').read_text(encoding='utf-8'))['active_area']['presets'][0]
    assert tuple(preset[key] for key in ('left', 'right', 'top', 'bottom')) == expected


@pytest.mark.gpu
def test_rotated_hdr10plus_roundtrip(app, tmp_path):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    hdr_tool = ffmpeg.parent.parent / 'hdr-metadata/hdr10plus_tool.exe'
    dovi = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, native, hdr_tool, dovi)):
        pytest.skip('Real media tools and GPU engine are required')
    source = _sample(tmp_path, ffmpeg, 'hdr10plus', hdr_tool, dovi, rotation=90)
    settings = replace(Settings(), engine='rife', fps_multiplier=2, high_precision=True, codec='libx265',
                       pixel_format='yuv420p10le', preset='ultrafast', target='native', container='mkv',
                       output_dir=str(tmp_path), copy_subtitles=False, audio='copy')
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-20:])
    assert 'Dynamic HDR frame count, metadata and output timing verified.' in logs


@pytest.mark.gpu
@pytest.mark.parametrize('kind,codec,container,factor,model,engine', [('hdr10plus', 'libx265', 'mkv', 2, 'rife-v4.6', 'rife'),
                                                                       ('dovi', 'hevc_nvenc', 'mp4', 2, 'rife-v4.6', 'rife'),
                                                                       ('both', 'libx265', 'mp4', 2, 'rife-v4.6', 'rife'),
                                                                       ('static', 'libx265', 'mkv', 2, 'rife-v4.6', 'rife'),
                                                                       ('both', 'libx265', 'mkv', 3, 'rife-v2', 'rife'),
                                                                       ('hdr10plus', 'libx265', 'mkv', 1, 'rife-v4.6', 'realcugan'),
                                                                       ('dovi', 'hevc_nvenc', 'mp4', 1, 'rife-v4.6', 'realcugan')])
def test_rife_variable_rate_dynamic_hdr_roundtrip(app, tmp_path, kind, codec, container, factor, model, engine):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    native = Path(os.environ.get('ANIME_TEST_VIDEO2X', ''))
    hdr_tool = ffmpeg.parent.parent / 'hdr-metadata/hdr10plus_tool.exe'
    dovi = ffmpeg.parent.parent / 'hdr-metadata/dovi_tool.exe'
    mkvmerge = ffmpeg.parent.parent / 'hdr-metadata/mkvmerge.exe'
    if not all(path.is_file() for path in (ffmpeg, ffprobe, native, hdr_tool, dovi, mkvmerge)):
        pytest.skip('Real media tools, timestamp tool and GPU engine are required')
    original = _sample(tmp_path, ffmpeg, 'hdr10plus' if kind == 'static' else kind, hdr_tool, dovi)
    raw = tmp_path / 'vfr-source.hevc'
    _run([ffmpeg, '-v', 'error', '-i', original, '-map', '0:v:0', '-c:v', 'copy',
          '-bsf:v', 'hevc_mp4toannexb', '-f', 'hevc', raw])
    if kind == 'static':
        plain = tmp_path / 'vfr-static.hevc'
        _run([hdr_tool, 'remove', '-i', raw, '-o', plain])
        raw = plain
    times = tmp_path / 'timestamps.txt'
    times.write_text('# timestamp format v2\n0\n200\n500\n800\n950\n1300\n1600\n', encoding='utf-8')
    video = tmp_path / 'vfr-video.mkv'
    _run([mkvmerge, '-q', '-o', video, '--timestamps', f'0:{times}', raw])
    source = tmp_path / 'vfr-source.mkv'
    _run([ffmpeg, '-v', 'error', '-i', video, '-itsoffset', '0.15', '-i', original, '-map', '0:v:0', '-map', '1:a?',
          '-c', 'copy', source])
    subtitle = tmp_path / 'vfr-subtitle.srt'
    subtitle.write_text('1\n00:00:00,300 --> 00:00:01,100\nVFR subtitle\n', encoding='utf-8')
    settings = replace(Settings(), engine=engine, cugan_model='models-nose', noise=0, scale=2,
                       rife_model=model, fps_multiplier=max(2, factor), high_precision=True, codec=codec,
                       pixel_format='p010le' if codec == 'hevc_nvenc' else 'yuv420p10le',
                       preset='p4' if codec == 'hevc_nvenc' else 'ultrafast', target='native', container=container,
                       output_dir=str(tmp_path), copy_subtitles=False, audio='copy',
                       subtitle_mode='soft', subtitle_path=str(subtitle))
    runner = QueueRunner(); job = runner.enqueue([source], settings)[0]
    logs = []; runner.log.connect(logs.append)
    wait_runner(app, runner, {'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), 'video2x': str(native)}, timeout=180_000)
    assert job.state == 'done_warning', job.error + '\n' + '\n'.join(logs[-20:])
    assert 'Dynamic HDR frame count, metadata and output timing verified.' in logs
    frames = json.loads(_run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_frames',
                              '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', job.output]).stdout)['frames']
    actual = [float(frame['best_effort_timestamp_time']) for frame in frames]
    boundaries = [0, .2, .5, .8, .95, 1.3, 1.6]
    expected = [start + (end - start) * step / factor
                for start, end in zip(boundaries, boundaries[1:]) for step in range(factor)]
    assert len(actual) == len(expected)
    assert all(abs(value - actual[0] - target) < .002 for value, target in zip(actual, expected))
    def starts(path):
        streams = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', path]).stdout)['streams']
        return {stream['codec_type']: float(stream.get('start_time', 0)) for stream in streams}
    before, after = starts(source), starts(job.output)
    def first_audio_packet(path):
        packets = json.loads(_run([ffprobe, '-v', 'error', '-select_streams', 'a:0', '-read_intervals', '%+#1',
                                   '-show_entries', 'packet=pts_time', '-of', 'json', path]).stdout)['packets']
        return float(packets[0]['pts_time'])
    assert abs((first_audio_packet(job.output) - after['video']) - (first_audio_packet(source) - before['video'])) < .003
    subtitles = _run([ffmpeg, '-v', 'error', '-i', job.output, '-map', '0:s:0', '-f', 'srt', '-']).stdout.decode('utf-8')
    assert '00:00:00,300 --> 00:00:01,100' in subtitles
