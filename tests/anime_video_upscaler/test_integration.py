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
    from anime_video_upscaler.gui import MainWindow
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: tools)
    window = MainWindow()
    window.add_paths([str(media_files[0])])
    reports = []
    def capture(dialog):
        reports.append(dialog.findChild(QPlainTextEdit).toPlainText())
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QDialog, 'exec', capture)
    window.queue_report()
    assert len(reports) == 1
    assert media_files[0].name in reports[0]
    assert '96 × 64' in reports[0] and window.store.value.codec in reports[0] and 'GiB' in reports[0]
    window.close()


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
    assert "Audio, subtitles and startup time are excluded" in dialog.results.toPlainText()
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
def test_hdr_source_is_tone_mapped_to_sdr(app, tools, tmp_path, transfer):
    source = tmp_path / f"hdr-{transfer}.mkv"
    run([tools["ffmpeg"], "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=12:duration=1",
         "-vf", f"setparams=color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc",
         "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", f"log-level=error:colorprim=bt2020:transfer={transfer}:colormatrix=bt2020nc", "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", transfer,
         "-colorspace", "bt2020nc", str(source)])
    runner = QueueRunner()
    settings = replace(Settings(), engine="none", target="native", preset="ultrafast", output_dir=str(tmp_path), copy_subtitles=False)
    job = runner.enqueue([source], settings)[0]
    wait_runner(app, runner, tools)
    assert job.state == "done", job.error
    probe = json.loads(run([tools["ffprobe"], "-v", "error", "-show_streams", "-of", "json", str(job.output)]))
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert video["color_transfer"] == "bt709"
    assert video["color_primaries"] == "bt709"
    assert not Media.from_probe(probe).hdr


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
