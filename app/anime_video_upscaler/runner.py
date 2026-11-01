"""Non-blocking Qt process controller. The GUI never waits for a child process."""
from __future__ import annotations

import codecs
import hashlib
from dataclasses import dataclass
from fractions import Fraction
import json
import logging
import math
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .config import Settings, config_dir
from .streaming import StreamingJob
from .subtitles import stage_subtitle
from .disk_policy import RESERVE_BYTES, contains_disk_error, disk_error_text, preflight_threshold
from .pipeline import (ANSI, Media, ai_command, encoder_probe_command, final_command, hdr_to_sdr_filters, output_path,
                       lossless_working_bytes, progress_value, publish_output, validate_media, validate_output_streams)

RESUME_SEGMENT_SECONDS = 60


@dataclass
class Job:
    source: Path
    settings: Settings
    id: str = ""
    state: str = "queued"
    percent: float = 0
    output: Path | None = None
    error: str = ""
    settings_locked: bool = False
    output_name: str = ""
    media_duration: float = 0.0
    source_hdr: bool = False
    media_info: Media | None = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4().hex


class QueueRunner(QObject):
    changed = Signal(object)
    log = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: list[Job] = []
        self.current: Job | None = None
        self.process: QProcess | None = None
        self.stream_worker = None
        self.use_streaming = False
        self.busy = False
        self.cancelled = False
        self.tools: dict[str, str] = {}
        self._temp = None
        self._reserved: set[str] = set()
        self._logger = None
        self._capture = ""
        self._line_buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._timed_out)
        self._timeout_error = ""
        self._disk_error_seen = False
        self._disk_watch = QTimer(self)
        self._disk_watch.setInterval(1000)
        self._disk_watch.timeout.connect(self._check_disk_space)
        self.started_at = None
        self.processing_started_at = None
        self.speed_fps = None
        self._resume_dir = None

    def enqueue(self, paths: list[Path], settings: Settings) -> list[Job]:
        existing = {os.path.normcase(str(j.source.resolve())) for j in self.jobs}
        added = []
        for path in paths:
            path = path.resolve()
            key = os.path.normcase(str(path))
            if key in existing:
                continue
            existing.add(key)
            job = Job(path, settings)
            self.jobs.append(job)
            added.append(job)
        return added

    def start(self, tools: dict[str, str]) -> None:
        if self.busy or not any(j.state == "queued" for j in self.jobs):
            return
        required = {"ffmpeg", "ffprobe"}
        if any(j.settings.engine != "none" for j in self.jobs if j.state == "queued"):
            required.add("video2x")
        for key in required:
            if not tools.get(key) or not Path(tools[key]).is_file():
                raise ValueError(f"Bundled {key} is missing. Prepare the application runtime; see the development guide.")
        self.tools = {key: str(Path(value).resolve()) if value else "" for key, value in tools.items()}
        self.cancelled = False
        self.busy = True
        self.busy_changed.emit(True)
        QTimer.singleShot(0, self._next)

    def cancel(self) -> None:
        if not self.busy:
            return
        self.cancelled = True
        self._timeout.stop()
        if self.stream_worker is not None:
            self.stream_worker.stop()
            return
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            # These tools spawn no children. On Windows kill terminates only this job's process.
            self.process.kill()
        else:
            self._finish("cancelled")

    def _next(self):
        if self.cancelled:
            self._idle()
            return
        self.current = next((j for j in self.jobs if j.state == "queued"), None)
        if self.current is None:
            self._idle()
            return
        job = self.current
        try:
            self.started_at = time.monotonic()
            self.processing_started_at = None
            self.speed_fps = None
            self._resume_dir = None
            job.settings.validate()
            if not job.source.is_file():
                raise ValueError("Input file no longer exists")
            job.output = output_path(job.source, job.settings, self._reserved, job.output_name)
            job.output.parent.mkdir(parents=True, exist_ok=True)
            self._temp = tempfile.TemporaryDirectory(prefix=".anime-job-", dir=job.output.parent)
            self.work = Path(self._temp.name)
            self._disk_watch.start()
            self.partial = self.work / ("final." + job.settings.container)
            self.intermediate = self.work / "upscaled.mkv"
            self.normalized = self.work / "decoded.mkv"
            self.subtitle_name = ""
            subtitle_encoding = ""
            self.watermark_name = ""
            self._recovery_warning = ""
            self._normalized_frames = 0
            self._ai_reported_frames = 0
            self._ai_success = False
            self._processed_images = 0
            self.use_streaming = False
            self.cugan_tool = Path(self.tools["video2x"]).parent / "avue-realcugan.exe" if job.settings.engine == "realcugan" else None
            self.use_cugan_reference = bool(self.cugan_tool and self.cugan_tool.is_file())
            s = job.settings
            self._open_log(job)
            self._write(f"Input: {job.source}\nOutput: {job.output}\nSettings: {json.dumps(s.export(), ensure_ascii=False)}")
            if s.subtitle_mode != "off":
                path = Path(s.subtitle_path)
                if not path.is_file() or path.suffix.lower() not in (".srt", ".ass", ".ssa", ".vtt"):
                    raise ValueError("Select an SRT, ASS, SSA or VTT subtitle file")
                self.subtitle_name = "subtitle" + path.suffix.lower()
                subtitle_encoding = stage_subtitle(path, self.work / self.subtitle_name)
            if s.watermark_path:
                path = Path(s.watermark_path)
                if not path.is_file() or path.suffix.lower() != ".png":
                    raise ValueError("Select a PNG watermark")
                self.watermark_name = "watermark.png"
                shutil.copyfile(path, self.work / self.watermark_name)
            if subtitle_encoding:
                self._write(f"External subtitle prepared as UTF-8 (source encoding: {subtitle_encoding}). Original file unchanged.")
            self._launch("probe", [self.tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(job.source)], self.work)
        except (ValueError, OSError) as error:
            self._finish("failed", str(error))

    def _open_log(self, job):
        directory = config_dir() / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger("anime-upscaler." + job.id)
        self._logger.propagate = False
        self._logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(directory / (job.id + ".log"), maxBytes=4_000_000, backupCount=1, encoding="utf-8")
        self._logger.addHandler(handler)

    def _write(self, message):
        message = ANSI.sub("", message).strip()
        if message:
            self.log.emit(message)
            if self._logger:
                self._logger.info(message)

    def _launch(self, stage, command, cwd):
        if self.cancelled:
            self._finish("cancelled")
            return
        self.stage = stage
        self.current.state = stage
        self.changed.emit(self.current)
        self._capture = ""
        self._line_buffer = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._timeout_error = ""
        self._disk_error_seen = False
        process = QProcess(self)
        self.process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(str(cwd))
        process.readyReadStandardOutput.connect(self._read)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._exited)
        self._write(subprocess.list2cmdline(command))
        process.start(command[0], command[1:])
        if stage in ("subtitle_check", "resume_count", "resume_verify_new", "resume_verify_existing", "verify_source_timing"):
            self._timeout.start(300_000)
        elif stage in ("probe", "help", "filters", "encoders", "encoder_check", "verify"):
            self._timeout.start(30_000)

    def _read(self):
        if self.process is None:
            return
        text = self._decoder.decode(bytes(self.process.readAllStandardOutput()))
        if self.stage in ("probe", "subtitle_check", "help", "filters", "encoders", "encoder_check", "verify", "verify_ai", "resume_count", "resume_verify_new", "resume_verify_existing"):
            self._capture += text
            if len(self._capture) > 4_000_000:
                self._timeout_error = "Unexpectedly large tool response"
                self.process.kill()
                return
        self._line_buffer += text
        lines = re.split(r"[\r\n]", self._line_buffer)
        self._line_buffer = lines.pop()[-16000:]
        for line in lines:
            self._consume(line)

    def _consume(self, line):
        if not self.current:
            return
        clean = ANSI.sub("", line).strip()
        if self.stage == "verify_source_timing":
            try:
                pts, duration = clean.split(",")[:2]
                pts, duration = float(pts), float(duration)
                if duration >= 0 and all(map(math.isfinite, (pts, duration))):
                    self._timing_start = min(self._timing_start, pts)
                    self._timing_end = max(self._timing_end, pts + duration)
            except (ValueError, TypeError):
                pass
            return
        if self.stage == "encode" and clean.startswith("fps="):
            try:
                self.speed_fps = float(clean.partition("=")[2])
            except ValueError:
                pass
        if contains_disk_error(clean):
            self._disk_error_seen = True
        if self.stage == "normalize" and clean.startswith("frame="):
            try:
                self._normalized_frames = int(clean.partition("=")[2].strip())
            except ValueError:
                pass
        if self.stage == "ai_frames" and " -> " in clean and clean.endswith(" done"):
            self._processed_images += 1
            if self._normalized_frames > 0:
                self.current.percent = max(self.current.percent, min(79.0, 80 * self._processed_images / self._normalized_frames))
                self.changed.emit(self.current)
        if self.stage == "ai":
            if "Video processed successfully" in clean:
                self._ai_success = True
            if clean.startswith("Total frames processed:"):
                try:
                    self._ai_reported_frames = int(clean.partition(":")[2].strip())
                except ValueError:
                    pass
        value = progress_value(line, self.stage, getattr(self, "media", Media(1, 1, 0, 0)).duration, self.current.settings.engine != "none")
        if value is not None:
            self.current.percent = max(self.current.percent, value)
            self.changed.emit(self.current)
        if self.stage in ("ai", "ai_frames", "assemble", "encode", "normalize", "resume_decode", "resume_ai", "resume_concat") and line and not re.match(r"^(frame\s*=|fps=|stream_\d|bitrate=|total_size=|out_time|dup_frames=|drop_frames=|speed=|progress=)", ANSI.sub("", line)):
            self._write(line)

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.process:
            message = self.process.errorString()
            self._timeout.stop()
            self.process.deleteLater()
            self.process = None
            self._finish("failed", message)

    def _check_disk_space(self):
        if not self.current or not self._temp or self.cancelled:
            return
        try:
            free = shutil.disk_usage(self.work).free
        except OSError:
            return
        if free >= RESERVE_BYTES:
            return
        self._disk_watch.stop()
        self._timeout_error = disk_error_text(free, RESERVE_BYTES, self.current.output.parent)
        self._write(self._timeout_error)
        if self.stream_worker is not None:
            self.stream_worker.stop(self._timeout_error)
            return
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        else:
            self._finish("failed", self._timeout_error)

    def _timed_out(self):
        self._timeout_error = f"Tool response timed out after {self._timeout.interval() // 1000} seconds"
        if self.process:
            self.process.kill()

    def _exited(self, code, status):
        self._timeout.stop()
        self._read()
        self._consume(self._line_buffer + self._decoder.decode(b"", final=True))
        self.process.deleteLater()
        self.process = None
        if self.cancelled:
            self._finish("cancelled")
            return
        if self._timeout_error or code != 0 or status != QProcess.ExitStatus.NormalExit or self._disk_error_seen:
            if self._disk_error_seen:
                try:
                    free = shutil.disk_usage(self.work).free
                except OSError:
                    free = 0
                self._finish("failed", disk_error_text(free, RESERVE_BYTES, self.current.output.parent))
                return
            # Explicit opt-in, narrowly limited to a reproduced legacy shutdown defect.
            # A full decode + exact frame-count check follows; success text alone is insufficient.
            expected_frames = self._normalized_frames
            if self.current.settings.engine == "rife":
                expected_frames = (expected_frames - 1) * self.current.settings.fps_multiplier + 1
            if (self.stage == "ai" and self.current.settings.recover_shutdown and
                    (code & 0xFFFFFFFF) == 0xC0000005 and self._ai_success and
                    expected_frames > 0 and self._ai_reported_frames == expected_frames and
                    not self._timeout_error):
                recovery = "Legacy AI engine crashed during shutdown (0xC0000005). Recovered output requires full frame verification."
                self._recovery_warning = " ".join(filter(None, (self._recovery_warning, recovery)))
                self._write(recovery)
                self._verify_ai()
                return
            if self._capture:
                self._write(self._capture[-8000:])
            message = self._timeout_error or f"{self.stage} exited with code {code}. See the job log."
            if self.stage == "encoder_check":
                message = f"{self.current.settings.codec}: GPU encoder initialization failed. Check hardware, driver, codec and log."
            if self.stage == "subtitle_check":
                message = "Subtitle validation failed. Check its encoding and timestamps. See the job log."
            self._finish("failed", message)
            return
        try:
            self._advance()
        except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
            self._finish("failed", str(error))

    def _advance(self):
        job = self.current
        s = job.settings
        if self.stage == "probe":
            self.probe_data = json.loads(self._capture)
            self.media = Media.from_probe(self.probe_data)
            validate_media(s, self.media)
            job.media_duration = self.media.duration
            if shutil.disk_usage(self.work).free < RESERVE_BYTES:
                raise ValueError(disk_error_text(shutil.disk_usage(self.work).free, RESERVE_BYTES, job.output.parent))
            self._subtitle_checks = []
            if self.subtitle_name:
                self._subtitle_checks.append((self.subtitle_name, "0:s:0"))
            if s.copy_subtitles:
                text_codecs = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
                for stream in self.probe_data.get("streams", []):
                    if stream.get("codec_type") == "subtitle" and stream.get("codec_name") in text_codecs:
                        self._subtitle_checks.append((str(job.source), f"0:{int(stream['index'])}"))
            self._check_next_subtitle()
        elif self.stage == "subtitle_check":
            self._write("Subtitle decoded successfully before processing.")
            self._check_next_subtitle()
        elif self.stage == "help":
            self.help_text = self._capture
            self.use_streaming = "stream-nut-v1" in self.help_text and not s.resume_segments
            if s.resume_segments and s.engine == "realcugan":
                if "stream-nut-v1" not in self.help_text:
                    raise ValueError("Real-CUGAN resume requires the bundled streaming-capable engine")
                self.use_cugan_reference = False
            if self.use_streaming:
                self.use_cugan_reference = False
            if s.engine == "realcugan":
                self._cugan_model_path()
            self._check_working_space()
            # Validate model capabilities before spending time decoding.
            if self.use_cugan_reference:
                self._cugan_model_path()
            else:
                ai_command(s, self.tools, self.normalized, self.intermediate, self.media, self.help_text)
                if s.engine == "realcugan" and not self.use_streaming and "stream-nut-v1" not in self.help_text:
                    self._recovery_warning = "Legacy Real-CUGAN may ignore the selected sync gap; use the bundled reference backend."
                    self._write(self._recovery_warning)
            self._check_filters()
        elif self.stage == "filters":
            required = {"scale", "pad", "setsar", "null"}
            if self.media.hdr:
                required.update(("zscale", "tonemap", "format", "setparams", "sidedata"))
            if s.subtitle_mode == "burn":
                required.add("subtitles")
            if s.watermark_path:
                required.update(("overlay", "colorchannelmixer"))
            if s.flicker == "mild":
                required.add("deflicker")
            if s.restoration == "mild":
                required.add("hqdn3d")
            if s.engine == "rife":
                required.add("tpad")
            found = set(re.findall(r"^\s*[.A-Z|]{2,3}\s+(\w+)\s", self._capture, re.M))
            if required - found:
                raise ValueError("Missing FFmpeg filters: " + ", ".join(sorted(required - found)))
            self._launch("encoders", [self.tools["ffmpeg"], "-hide_banner", "-encoders"], self.work)
        elif self.stage == "encoders":
            if not re.search(r"\b" + re.escape(s.codec) + r"\s", self._capture):
                raise ValueError(f"FFmpeg does not include {s.codec}")
            # Validate the final settings before starting expensive AI processing.
            final_command(s, self.tools, job.source, self.intermediate, self.partial, self.media, self.subtitle_name, self.watermark_name)
            if s.codec.endswith(("_nvenc", "_amf")):
                self._launch("encoder_check", encoder_probe_command(s, self.tools, self.media), self.work)
                return
            self._begin_processing()
        elif self.stage == "encoder_check":
            self._begin_processing()
        elif self.stage == "resume_count":
            streams = json.loads(self._capture).get("streams", [])
            self._resume_total_frames = int(streams[0].get("nb_read_frames", 0)) if streams else 0
            if self._resume_total_frames < 1:
                raise ValueError("Cannot count source frames for resume mode")
            self._prepare_resume()
        elif self.stage == "resume_decode":
            self._resume_partial.unlink(missing_ok=True)
            self._resume_ai_output = self.work / "resume-ai.mkv"
            self._resume_ai_output.unlink(missing_ok=True)
            self._launch("resume_ai", ai_command(s, self.tools, self._resume_input, self._resume_ai_output, self.media, self.help_text), Path(self.tools["video2x"]).parent)
        elif self.stage == "resume_ai":
            filters = "trim=start_frame=1,setpts=PTS-STARTPTS" if s.engine == "rife" and self._resume_index else "setpts=PTS-STARTPTS"
            self._launch("resume_trim", [self.tools["ffmpeg"], "-v", "error", "-nostdin", "-n", "-i", str(self._resume_ai_output), "-vf", filters, "-c:v", "ffv1", "-g", "1", "-threads", "1", "-pix_fmt", "bgr0", "-fps_mode", "passthrough", str(self._resume_partial)], self.work)
        elif self.stage == "resume_trim":
            self._verify_resume_segment(self._resume_partial, "resume_verify_new")
        elif self.stage in ("resume_verify_new", "resume_verify_existing"):
            data = json.loads(self._capture)
            streams = data.get("streams", [])
            frames = int(streams[0].get("nb_read_frames", 0)) if streams else 0
            if frames != self._resume_expected:
                raise ValueError("Saved AI segment frame verification failed; remove the affected checkpoint and retry")
            if self.stage == "resume_verify_new":
                os.replace(self._resume_partial, self._resume_segment)
            self._write(f"Verified segment {self._resume_index + 1}/{self._resume_count}: {frames} frames")
            self._resume_index += 1
            self.current.percent = min(80.0, 80.0 * self._resume_index / self._resume_count)
            self.changed.emit(self.current)
            self._resume_next()
        elif self.stage == "resume_concat":
            self._encode()
        elif self.stage == "normalize":
            if self.use_cugan_reference:
                self._run_cugan_frames()
            else:
                self._launch("ai", ai_command(s, self.tools, self.normalized, self.intermediate, self.media, self.help_text), Path(self.tools["video2x"]).parent)
        elif self.stage == "ai_frames":
            self._assemble_cugan_frames()
        elif self.stage in ("ai", "assemble"):
            self._verify_ai()
        elif self.stage == "verify_ai":
            result = json.loads(self._capture)
            video = next(stream for stream in result["streams"] if stream["codec_type"] == "video")
            expected = (self._normalized_frames - 1) * s.fps_multiplier + 1 if s.engine == "rife" else self._normalized_frames
            if expected < 1 or int(video.get("nb_read_frames", 0)) != expected or result.get("error"):
                raise ValueError("AI frame-count verification failed; no final output will be published")
            self.normalized.unlink(missing_ok=True)
            self._encode()
        elif self.stage == "encode":
            self._launch("verify", [self.tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(self.partial)], self.work)
        elif self.stage == "verify":
            output_probe = json.loads(self._capture)
            result = Media.from_probe(output_probe)
            if self.media.hdr:
                output_video = next(stream for stream in output_probe["streams"] if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic"))
                if output_video.get("color_transfer") != "bt709" or output_video.get("color_primaries") != "bt709":
                    raise ValueError("SDR output color metadata could not be verified; output was not published")
            validate_output_streams(s, self.probe_data, output_probe, bool(self.subtitle_name))
            from .pipeline import fitted_size, target_size
            expected = target_size(s, self.media) if s.aspect == "pad" else fitted_size(s, self.media)
            if (result.width, result.height) != expected:
                raise ValueError("Output dimensions differ from the requested dimensions")
            tolerance = max(0.15, 2 / (self.media.fps or 24))
            if self.media.duration and result.duration and abs(result.duration - self.media.duration) > tolerance:
                source_video = next(stream for stream in self.probe_data["streams"] if stream.get("index") == self.media.video_index)
                if source_video.get("duration") not in (None, "N/A", "0", "0.000000"):
                    raise ValueError(f"Output video duration differs from input ({result.duration:.3f}s vs {self.media.duration:.3f}s); output was not published")
                self._verified_output_duration = result.duration
                self._timing_start, self._timing_end = float('inf'), float('-inf')
                self._write("Container duration differs; checking source video packet timestamps.")
                self._launch("verify_source_timing", [self.tools["ffprobe"], "-v", "error", "-select_streams", str(self.media.video_index), "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0", str(job.source)], self.work)
                return
            self._publish_verified()
        elif self.stage == "verify_source_timing":
            source_duration = self._timing_end - self._timing_start
            tolerance = max(0.15, 2 / (self.media.fps or 24))
            if not 0 < source_duration < float('inf'):
                raise ValueError("Cannot verify source video timestamps; output was not published")
            self._write(f"Source video duration: {source_duration:.3f}s; output video duration: {self._verified_output_duration:.3f}s.")
            if abs(source_duration - self._verified_output_duration) > tolerance:
                raise ValueError(f"Output video duration differs from input ({self._verified_output_duration:.3f}s vs {source_duration:.3f}s); output was not published")
            self._publish_verified()

    def _publish_verified(self):
        job = self.current
        if self.partial.stat().st_size < 1:
            raise ValueError("Encoder produced an empty file")
        publish_output(self.partial, job.output)
        self._cleanup_resume()
        job.percent = 100
        self._finish("done_warning" if self._recovery_warning else "done", self._recovery_warning)

    def _check_working_space(self):
        free = shutil.disk_usage(self.work).free
        if self.use_streaming:
            threshold = RESERVE_BYTES + 64 * 1024**2
            if free < threshold:
                raise ValueError(disk_error_text(free, threshold, self.current.output.parent))
            self._write("Streaming mode: source -> lossless pipe -> AI -> final encoder. No full-video AI intermediate is stored; final output still needs free disk. The 1 GiB reserve is monitored.")
            return
        reference = lossless_working_bytes(self.current.settings, self.media)
        threshold = preflight_threshold(reference)
        if free < threshold:
            raise ValueError(disk_error_text(free, threshold, self.current.output.parent))
        if reference is not None:
            self._write(f"Temporary AI files: uncompressed pixel-data reference {reference / 1024**3:.1f} GiB; free space {free / 1024**3:.1f} GiB. The safety threshold is 5% of that reference plus 1 GiB; it is not a prediction or guarantee. Actual peak, container overhead and final output differ.")

    def _after_subtitle_check(self):
        if self.current.settings.engine != "none":
            self._launch("help", [self.tools["video2x"], "--help"], Path(self.tools["video2x"]).parent)
        else:
            self._check_filters()

    def _check_next_subtitle(self):
        if not self._subtitle_checks:
            self._after_subtitle_check()
            return
        path, stream = self._subtitle_checks.pop(0)
        self._launch("subtitle_check", [self.tools["ffmpeg"], "-hide_banner", "-v", "error", "-xerror", "-nostdin", "-y", "-i", path, "-map", stream, "-c:s", "srt", "-f", "srt", os.devnull], self.work)

    def _prepare_resume(self):
        job = self.current
        source_stat = job.source.stat()
        engine_stat = Path(self.tools["video2x"]).stat()
        engine_dir = Path(self.tools["video2x"]).parent
        model_dir = job.settings.engine
        components = sorted(list(engine_dir.glob("*.dll")) + list((engine_dir / "models" / model_dir).rglob("*")))
        component_hashes = {}
        with Path(self.tools["video2x"]).open("rb") as stream:
            component_hashes["engine"] = hashlib.file_digest(stream, "sha256").hexdigest()
        for component in components:
            if component.is_file():
                with component.open("rb") as stream:
                    component_hashes[str(component.relative_to(engine_dir))] = hashlib.file_digest(stream, "sha256").hexdigest()
        signature = {"version": 3, "components": component_hashes, "source": str(job.source.resolve()), "size": source_stat.st_size,
                     "mtime_ns": source_stat.st_mtime_ns, "engine_size": engine_stat.st_size,
                     "engine_mtime_ns": engine_stat.st_mtime_ns, "settings": job.settings.export(),
                     "fps": self.media.fps, "frames": self._resume_total_frames, "segment_seconds": RESUME_SEGMENT_SECONDS}
        fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        directory = job.output.parent / f".anime-resume-{job.id}-{fingerprint}"
        if directory.is_symlink():
            raise ValueError("Checkpoint folder cannot be a symbolic link")
        directory.mkdir(exist_ok=True)
        manifest = directory / "manifest.json"
        if manifest.exists():
            if manifest.is_symlink() or json.loads(manifest.read_text(encoding="utf-8")) != signature:
                raise ValueError("Checkpoint metadata does not match the current job")
        else:
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump(signature, stream, ensure_ascii=False, sort_keys=True)
        self._resume_dir = directory
        self._resume_chunk_frames = max(1, round(self.media.fps * RESUME_SEGMENT_SECONDS))
        self._resume_count = (self._resume_total_frames + self._resume_chunk_frames - 1) // self._resume_chunk_frames
        self._resume_index = 0
        self._write(f"Resume mode saves verified AI segments in {directory}; it requires substantial extra disk space.")
        self._resume_next()

    def _resume_next(self):
        if self._resume_index >= self._resume_count:
            listing = self._resume_dir / "segments.ffconcat"
            with listing.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write("ffconcat version 1.0\n")
                for index in range(self._resume_count):
                    stream.write(f"file 'part-{index:06d}.mkv'\n")
            self._launch("resume_concat", [self.tools["ffmpeg"], "-v", "error", "-nostdin", "-n", "-f", "concat", "-safe", "1", "-i", str(listing), "-map", "0:v:0", "-c:v", "copy", str(self.intermediate)], self.work)
            return
        start = self._resume_index * self._resume_chunk_frames
        source_frames = min(self._resume_chunk_frames, self._resume_total_frames - start)
        overlap = int(self.current.settings.engine == "rife" and start > 0)
        self._resume_expected = source_frames
        if self.current.settings.engine == "rife":
            self._resume_expected = source_frames * self.current.settings.fps_multiplier
            if not overlap:
                self._resume_expected -= self.current.settings.fps_multiplier - 1
        self._resume_segment = self._resume_dir / f"part-{self._resume_index:06d}.mkv"
        self._resume_partial = self._resume_dir / f"part-{self._resume_index:06d}.partial.mkv"
        if self._resume_segment.is_symlink() or self._resume_partial.is_symlink():
            raise ValueError("Checkpoint files cannot be symbolic links")
        if self._resume_segment.is_file():
            self._verify_resume_segment(self._resume_segment, "resume_verify_existing")
            return
        self._resume_input = self.work / "resume-input.mkv"
        self._resume_input.unlink(missing_ok=True)
        seek = max(0, (start - overlap) / self.media.fps + self.media.video_start_offset)
        command = [self.tools["ffmpeg"], "-v", "error", "-nostdin", "-n", "-ss", f"{seek:.9f}", "-i", str(self.current.source),
                   "-map", f"0:{self.media.video_index}", "-an", "-sn", "-dn", "-frames:v", str(source_frames + overlap),
                   "-vf", ",".join(hdr_to_sdr_filters(self.media) + ["setpts=PTS-STARTPTS"]), "-c:v", "ffv1", "-pix_fmt", "bgr0", "-fps_mode", "passthrough", str(self._resume_input)]
        self._launch("resume_decode", command, self.work)

    def _verify_resume_segment(self, path, stage):
        self._launch(stage, [self.tools["ffprobe"], "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)], self.work)

    def _cleanup_resume(self):
        if not self._resume_dir:
            return
        try:
            for index in range(self._resume_count):
                (self._resume_dir / f"part-{index:06d}.mkv").unlink(missing_ok=True)
                (self._resume_dir / f"part-{index:06d}.partial.mkv").unlink(missing_ok=True)
            for name in ("segments.ffconcat", "manifest.json"):
                (self._resume_dir / name).unlink(missing_ok=True)
            self._resume_dir.rmdir()
        except OSError as error:
            self._write(f"Could not remove completed checkpoint files: {error}")

    def _stream_progress(self, value):
        if self.current:
            self.current.percent = max(self.current.percent, value)
            self.changed.emit(self.current)

    def _stream_fps(self, value):
        self.speed_fps = value

    def _stream_exited(self):
        worker = self.stream_worker
        self.stream_worker = None
        error, frames = worker.error, worker.source_frames
        worker.deleteLater()
        if self.cancelled:
            self._finish("cancelled")
        elif error:
            self._finish("failed", error)
        else:
            self._normalized_frames = frames
            self._launch("verify", [self.tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(self.partial)], self.work)

    def _begin_processing(self):
        job = self.current
        s = job.settings
        self.processing_started_at = time.monotonic()
        if s.resume_segments:
            self._launch("resume_count", [self.tools["ffprobe"], "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "json", str(job.source)], self.work)
            return
        if self.use_streaming:
            self.stage = self.current.state = "stream"
            self.changed.emit(self.current)
            worker = StreamingJob(s, self.tools, job.source, self.partial, self.work, self.media,
                                  self.help_text, self.subtitle_name, self.watermark_name, self)
            self.stream_worker = worker
            worker.log.connect(self._write)
            worker.progress.connect(self._stream_progress)
            worker.fps.connect(self._stream_fps)
            worker.finished.connect(self._stream_exited)
            worker.start()
        elif s.engine == "none":
            self._encode()
        else:
            if self.use_cugan_reference:
                self.frame_inputs = self.work / "source-frames"
                self.frame_outputs = self.work / "upscaled-frames"
                self.frame_inputs.mkdir()
                self.frame_outputs.mkdir()
                self._write("Real-CUGAN reference backend: lossless PNG frames use extra disk space; source frame rate is reconstructed as CFR. Inspect long outputs for sync.")
                command = [self.tools["ffmpeg"], "-hide_banner", "-nostdin", "-n", "-i", str(job.source),
                           "-map", f"0:{self.media.video_index}", "-an", "-sn", "-dn"]
                filters = hdr_to_sdr_filters(self.media)
                if s.restoration == "mild":
                    filters.append("hqdn3d=1.5:1.5:3:3")
                if filters:
                    command += ["-vf", ",".join(filters)]
                command += ["-fps_mode", "passthrough", "-progress", "pipe:1", "-nostats", str(self.frame_inputs / "%09d.png")]
                self._launch("normalize", command, self.work)
                return
            # The legacy decoder is fed lossless intra frames to retain delayed frames.
            command = [self.tools["ffmpeg"], "-hide_banner", "-nostdin", "-n", "-i", str(job.source),
                       "-map", f"0:{self.media.video_index}", "-an", "-sn", "-dn"]
            filters = hdr_to_sdr_filters(self.media)
            if s.restoration == "mild":
                filters.append("hqdn3d=1.5:1.5:3:3")
            if filters:
                command += ["-vf", ",".join(filters)]
            command += ["-c:v", "ffv1", "-pix_fmt", "bgr0", "-fps_mode", "passthrough",
                        "-progress", "pipe:1", "-nostats", str(self.normalized)]
            self._launch("normalize", command, self.work)

    def _cugan_model_path(self):
        s = self.current.settings
        model = Path(self.tools["video2x"]).parent / "models" / "realcugan" / s.cugan_model
        suffix = "conservative" if s.noise == -1 else "no-denoise" if s.noise == 0 else f"denoise{s.noise}x"
        name = f"up{s.scale}x-{suffix}"
        if not all((model / (name + extension)).is_file() for extension in (".param", ".bin")):
            raise ValueError(f"Bundled Real-CUGAN model files are missing: {s.cugan_model}/{name}")
        return model

    def _run_cugan_frames(self):
        s = self.current.settings
        model = self._cugan_model_path()
        # models-nose has no synchronized feature maps in the upstream backend.
        sync = 0 if s.cugan_model == "models-nose" else s.cugan_syncgap
        command = [str(self.cugan_tool), "-i", str(self.frame_inputs), "-o", str(self.frame_outputs),
                   "-n", str(s.noise), "-s", str(s.scale), "-t", "0", "-c", str(sync),
                   "-m", str(model), "-g", str(s.gpu), "-j", f"1:{s.cugan_threads}:1", "-f", "png", "-v"]
        self._launch("ai_frames", command, self.cugan_tool.parent)

    def _assemble_cugan_frames(self):
        if self._normalized_frames < 1:
            raise ValueError("No source frames were extracted")
        inputs = {p.name for p in self.frame_inputs.glob("*.png") if p.is_file()}
        outputs = {p.name for p in self.frame_outputs.glob("*.png") if p.is_file() and p.stat().st_size > 0}
        expected = {f"{i:09d}.png" for i in range(1, self._normalized_frames + 1)}
        if inputs != expected or outputs != expected:
            raise ValueError("Real-CUGAN did not produce every expected frame; no output will be published")
        fps = Fraction(self.media.fps).limit_denominator(1_000_000)
        command = [self.tools["ffmpeg"], "-hide_banner", "-nostdin", "-n", "-v", "error", "-xerror",
                   "-framerate", f"{fps.numerator}/{fps.denominator}", "-start_number", "1",
                   "-i", str(self.frame_outputs / "%09d.png"), "-frames:v", str(self._normalized_frames),
                   "-an", "-c:v", "ffv1", "-pix_fmt", "bgr0", "-progress", "pipe:1", "-nostats", str(self.intermediate)]
        self._launch("assemble", command, self.work)

    def _verify_ai(self):
        # Count_frames decodes the complete FFV1 video. Fatal errors make the command fail;
        # nonfatal decoder diagnostics make JSON parsing fail instead of being ignored.
        self._launch("verify_ai", [self.tools["ffprobe"], "-v", "error", "-err_detect", "explode",
            "-count_frames", "-show_streams", "-show_error", "-of", "json", str(self.intermediate)], self.work)

    def _check_filters(self):
        self._launch("filters", [self.tools["ffmpeg"], "-hide_banner", "-filters"], self.work)

    def _encode(self):
        s = self.current.settings
        source = self.current.source
        video = source if s.engine == "none" else self.intermediate
        self._launch("encode", final_command(s, self.tools, source, video, self.partial, self.media, self.subtitle_name, self.watermark_name), self.work)

    def _finish(self, state, error=""):
        self._disk_watch.stop()
        if self.current:
            if state not in ("done", "done_warning") and self.current.output:
                self._reserved.discard(os.path.normcase(str(self.current.output.resolve())))
            self.current.state = state
            self.current.error = error
            self._write(error or state)
            self.changed.emit(self.current)
        if self._logger:
            for handler in self._logger.handlers[:]:
                handler.close()
                self._logger.removeHandler(handler)
            self._logger = None
        if self._temp:
            try:
                self._temp.cleanup()
            except OSError as cleanup_error:
                self.log.emit(f"Temporary files could not be removed: {cleanup_error}")
            self._temp = None
        self.current = None
        QTimer.singleShot(0, self._next)

    def _idle(self):
        self.current = None
        self.busy = False
        self.busy_changed.emit(False)
