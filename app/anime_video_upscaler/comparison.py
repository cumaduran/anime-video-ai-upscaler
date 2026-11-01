"""Short, isolated real-processing comparison using the same queue pipeline."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import os
import shutil
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from .config import Settings, config_dir, default_encoder_preset, quality_maximum
from .diagnostics import LogDialog, failure_summary, read_log
from .preview import PreviewPanel
from .runner import QueueRunner


class CompareDialog(QDialog):
    def __init__(self, source, media, timestamp, settings, tools, translate, store_type, parent=None):
        super().__init__(parent)
        self.source, self.media, self.settings, self.tools, self.tr = Path(source), media, settings, tools, translate
        self.store_type = store_type
        self.process = None
        self.clip_timeout = QTimer(self)
        self.clip_timeout.setSingleShot(True)
        self.clip_timeout.timeout.connect(self._clip_timed_out)
        self.clip_timed_out = False
        self.runner = QueueRunner(self)
        self.log_lines = deque(maxlen=2500)
        self.log_dialog = None
        self.runner.log.connect(self.log_lines.append)
        self.runner.changed.connect(self._job_changed)
        self.runner.busy_changed.connect(self._busy_changed)
        self.job = None
        self.temp = None
        self.reference_temp = None
        self.reference = None
        self.closing = False
        self.clip = None
        self.sample_start = timestamp
        self.sample_length = 3.0
        self.sample_started_at = None
        self.sample_results = []
        self.sample_observations = {}
        self.setWindowTitle(self.tr("AI comparison · short sample"))
        self.resize(1300, 760)
        layout = QVBoxLayout(self)
        note = QLabel(self.tr("Create a short temporary sample using the selected AI and encoder. Source audio, subtitles and watermark are excluded. Nothing is uploaded."))
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(self.tr("Start (seconds)")))
        self.start_time = QDoubleSpinBox()
        self.start_time.setDecimals(1)
        self.start_time.setRange(0, max(0, media.duration - 0.5) if media.duration else 86400)
        self.start_time.setValue(min(timestamp, self.start_time.maximum()))
        controls.addWidget(self.start_time)
        controls.addWidget(QLabel(self.tr("Length (seconds)")))
        self.length = QDoubleSpinBox()
        self.length.setDecimals(1)
        self.length.setRange(0.5, 10.0)
        self.length.setValue(3.0)
        controls.addWidget(self.length)
        self.run_button = QPushButton(self.tr("Process sample"))
        self.run_button.clicked.connect(self.start)
        controls.addWidget(self.run_button)
        self.cancel_button = QPushButton(self.tr("Cancel sample"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        layout.addLayout(controls)
        models = QHBoxLayout()
        models.addWidget(QLabel(self.tr("Sample AI model")))
        self.model_choice = QComboBox()
        for text, value in (("Current settings", "current"),
                            ("Real-ESRGAN anime video · 2×", "anime2"),
                            ("Real-ESRGAN anime video · 4×", "anime4"),
                            ("Real-ESRGAN anime detail · 4×", "anime-detail"),
                            ("Real-ESRGAN general video · 4×", "general"),
                            ("Real-CUGAN anime · 2×", "cugan"),
                            ("Resize only · no AI", "none")):
            self.model_choice.addItem(self.tr(text), value)
        models.addWidget(self.model_choice, 1)
        self.recovery = QCheckBox(self.tr("Verify and recover the known engine shutdown crash"))
        self.recovery.setChecked(True)
        self.recovery.setToolTip(self.tr("Only applies to this temporary comparison. A recovered result is marked with a warning and must be reviewed."))
        models.addWidget(self.recovery)
        self.log_button = QPushButton(self.tr("View log"))
        self.log_button.clicked.connect(self.show_log)
        models.addWidget(self.log_button)
        layout.addLayout(models)
        encoding_row = QHBoxLayout()
        encoding_row.addWidget(QLabel(self.tr("Sample encoder")))
        self.encoder_choice = QComboBox()
        for label, value in (("Current encoder", "current"), ("CPU HEVC", "libx265"),
                             ("NVIDIA HEVC", "hevc_nvenc"), ("AMD HEVC", "hevc_amf")):
            self.encoder_choice.addItem(self.tr(label), value)
        encoding_row.addWidget(self.encoder_choice, 1)
        encoding_row.addWidget(QLabel(self.tr("Quality (CRF / CQ / QP)")))
        self.sample_quality = QSpinBox()
        self.sample_quality.setRange(0, quality_maximum(settings.codec))
        self.sample_quality.setValue(settings.quality)
        encoding_row.addWidget(self.sample_quality)
        self.encoder_choice.currentIndexChanged.connect(self._sample_encoder_changed)
        layout.addLayout(encoding_row)
        compare_actions = QHBoxLayout()
        self.keep_reference_button = QPushButton(self.tr("Keep result as reference"))
        self.keep_reference_button.setEnabled(False)
        self.keep_reference_button.clicked.connect(self.keep_reference)
        compare_actions.addWidget(self.keep_reference_button)
        self.clear_reference_button = QPushButton(self.tr("Compare with original"))
        self.clear_reference_button.setEnabled(False)
        self.clear_reference_button.clicked.connect(self.clear_reference)
        compare_actions.addWidget(self.clear_reference_button)
        self.previous_frame_button = QPushButton(self.tr("Previous frame"))
        self.next_frame_button = QPushButton(self.tr("Next frame"))
        for button, direction in ((self.previous_frame_button, -1), (self.next_frame_button, 1)):
            button.setToolTip(self.tr("Step by the source frame interval; the displayed timestamp is approximate."))
            button.clicked.connect(lambda checked=False, step=direction: self.step_frame(step))
            compare_actions.addWidget(button)
        compare_actions.addStretch()
        self.playback_button = QPushButton(self.tr("Play A/B comparison"))
        self.playback_button.setEnabled(False)
        self.playback_button.clicked.connect(self.open_playback)
        compare_actions.addWidget(self.playback_button)
        layout.addLayout(compare_actions)
        self.results = QPlainTextEdit()
        self.results.setReadOnly(True)
        self.results.setMaximumHeight(86)
        self.results.setPlaceholderText(self.tr("Processed samples: model, elapsed time, file size and encoding settings"))
        layout.addWidget(self.results)
        self.media_details = QLabel()
        self.media_details.setWordWrap(True)
        layout.addWidget(self.media_details)
        self.status = QLabel(self.tr("Choose a position and process a sample."))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        previews = QHBoxLayout()
        # Both panes show the actual decoded clips at their own natural size;
        # no extra AI, watermark or subtitle is applied to either preview.
        view_settings = replace(Settings(), engine="none", target="native", audio="off", copy_subtitles=False)
        sample_note = "Sampled playback of this clip (up to 2 fps) · no audio"
        self.before = PreviewPanel(store_type(view_settings, self), tools, translate, self, note=sample_note)
        self.after = PreviewPanel(store_type(view_settings, self), tools, translate, self, note=sample_note)
        self.after.media_ready.connect(self._show_media_details)
        self._show_media_details(None)
        for title, panel in (("Original sample", self.before), ("Processed sample", self.after)):
            column = QVBoxLayout()
            heading = QLabel(self.tr(title))
            if panel is self.before:
                self.before_heading = heading
            else:
                self.after_heading = heading
            heading.setObjectName("section")
            column.addWidget(heading)
            column.addWidget(panel, 1)
            previews.addLayout(column, 1)
            panel.play_button.hide()
        self.after.slider.valueChanged.connect(self.before.slider.setValue)
        self.before.slider.valueChanged.connect(self.after.slider.setValue)
        self.after.slider.hide()
        layout.addLayout(previews, 1)
        hint = QLabel(self.tr("Use the left timeline to compare matching moments. Previews are sampled and silent; inspect the exported video for final quality."))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        buttons = QDialogButtonBox()
        buttons.addButton(self.tr("Close"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _show_media_details(self, output):
        source = self.media
        text = self.tr("Source") + f": {source.width} × {source.height} · {source.fps:.3g} fps · {source.duration:.1f}s"
        if output:
            text += "\n" + self.tr("Processed sample") + f": {output.width} × {output.height} · {output.fps:.3g} fps · {output.duration:.1f}s"
            if self.job and self.job.output and self.job.output.is_file():
                text += f" · {self.active_settings.codec} · {self.job.output.stat().st_size / 1024**2:.2f} MiB"
        self.media_details.setText(text)

    def open_playback(self):
        if not self.job or self.job.state not in ("done", "done_warning") or not self.job.output:
            return
        before = self.reference if self.reference else self.clip
        if not before or not Path(before).is_file() or not self.job.output.is_file():
            self._show_error(self.tr("Select a video to preview a frame."))
            return
        from .playback import PlaybackDialog
        dialog = PlaybackDialog(before, self.job.output, self.source, self.sample_start,
            self.sample_length, self.media.fps, self.tr, self.before_heading.text(),
            self.after_heading.text(), self,
            initial_frames=(self.before.canvas.frame, self.after.canvas.frame),
            initial_position=self.before.slider.value())
        dialog.exec()
        dialog.deleteLater()

    def step_frame(self, direction):
        if not self.before.slider.isEnabled():
            return
        fps = self.before.media.fps if self.before.media and self.before.media.fps > 0 else self.media.fps
        if fps <= 0:
            return
        current = self.before.slider.value()
        frame = max(0, round(current * fps / 1000) + direction)
        self.before.slider.setValue(round(frame * 1000 / fps))

    def keep_reference(self):
        if not self.job or self.job.state not in ("done", "done_warning") or not self.job.output or not self.job.output.is_file():
            return
        new_temp = None
        try:
            new_temp = tempfile.TemporaryDirectory(prefix=".compare-reference-", dir=config_dir(), ignore_cleanup_errors=True)
            reference = Path(new_temp.name) / "reference.mkv"
            try:
                os.link(self.job.output, reference)
            except OSError:
                shutil.copyfile(self.job.output, reference)
            self.before.clear_source_and_wait()
            if self.reference_temp:
                self.reference_temp.cleanup()
            self.reference_temp, self.reference = new_temp, reference
            self.before_heading.setText(self.tr("Reference processed sample") + " · " + self.active_model_label)
            self.before.set_source(reference)
            self.start_time.setValue(self.sample_start)
            self.length.setValue(self.sample_length)
            self.start_time.setEnabled(False)
            self.length.setEnabled(False)
            self.clear_reference_button.setEnabled(True)
            self.status.setText(self.tr("Reference saved. Choose another model and process the same sample."))
        except OSError as error:
            if new_temp:
                new_temp.cleanup()
            self._show_error(error)

    def clear_reference(self):
        try:
            self.before.clear_source_and_wait()
        except OSError as error:
            self._show_error(error)
            return
        if self.reference_temp:
            self.reference_temp.cleanup()
        self.reference_temp = self.reference = None
        self.before_heading.setText(self.tr("Original sample"))
        self.start_time.setEnabled(True)
        self.length.setEnabled(True)
        self.clear_reference_button.setEnabled(False)
        if self.clip and self.clip.is_file():
            self.before.set_source(self.clip)

    def sample_settings(self):
        choice = self.model_choice.currentData()
        choices = {
            "anime2": dict(engine="realesrgan", model="realesr-animevideov3", scale=2, noise=0),
            "anime4": dict(engine="realesrgan", model="realesr-animevideov3", scale=4, noise=0),
            "anime-detail": dict(engine="realesrgan", model="realesrgan-plus-anime", scale=4, noise=0),
            "general": dict(engine="realesrgan", model="realesrgan-plus", scale=4, noise=0),
            "cugan": dict(engine="realcugan", cugan_model="models-se", scale=2, noise=0),
            "none": dict(engine="none"),
        }
        codec = self.encoder_choice.currentData()
        encoder = {} if codec == "current" else {"codec": codec, "preset": default_encoder_preset(codec), "extra_options": ""}
        return replace(self.settings, **choices.get(choice, {}), **encoder,
                       quality=self.sample_quality.value(), recover_shutdown=self.recovery.isChecked(), resume_segments=False)

    def _sample_encoder_changed(self):
        codec = self.encoder_choice.currentData()
        self.sample_quality.setMaximum(quality_maximum(self.settings.codec if codec == "current" else codec))

    @staticmethod
    def describe_model(settings):
        if settings.engine == "realesrgan":
            return f"{settings.model} · {settings.scale}×"
        if settings.engine == "realcugan":
            return f"Real-CUGAN · {settings.cugan_model} · {settings.scale}×"
        if settings.engine == "libplacebo":
            return f"Anime4K · {settings.shader}"
        if settings.engine == "rife":
            return f"RIFE · {settings.rife_model}"
        return "No AI"

    def show_log(self, summary=None):
        path = config_dir() / "logs" / (self.job.id + ".log") if self.job else None
        content = read_log(path, "\n".join(self.log_lines))
        title = self.tr("Processing log")
        message = failure_summary(summary or self.status.text(), self.tr, content)
        if self.log_dialog is None:
            self.log_dialog = LogDialog(title, message, content, self.tr, path, self)
        else:
            self.log_dialog.set_contents(message, content, path)
        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()

    def _show_error(self, message):
        self.status.setText(str(message))
        self.show_log(str(message))

    def start(self):
        if self.process or self.runner.busy:
            return
        try:
            self.active_settings = self.sample_settings()
            self.sample_started_at = time.monotonic()
            self.active_settings.validate()
            self.active_model_label = self.describe_model(self.active_settings)
            self.after_heading.setText(self.tr("Processed sample") + " · " + self.active_model_label)
            if self.media.duration and self.start_time.value() + self.length.value() > self.media.duration + 0.01:
                raise ValueError(self.tr("The sample extends beyond the end of the video."))
            config_dir().mkdir(parents=True, exist_ok=True)
            if self.temp:
                self.before.clear_source_and_wait()
                self.after.clear_source_and_wait()
                self.temp.cleanup()
                if self.reference:
                    self.before.set_source(self.reference)
            self.temp = tempfile.TemporaryDirectory(prefix=".compare-", dir=config_dir(), ignore_cleanup_errors=True)
            self.clip = Path(self.temp.name) / "sample.mkv"
            self.job = None
            self.log_lines.clear()
            self.runner.jobs.clear()
            self.runner._reserved.clear()
            self.run_button.setEnabled(False)
            self.playback_button.setEnabled(False)
            self.keep_reference_button.setEnabled(False)
            self.clear_reference_button.setEnabled(False)
            self.model_choice.setEnabled(False)
            self.encoder_choice.setEnabled(False)
            self.sample_quality.setEnabled(False)
            self.recovery.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.status.setText(self.tr("Preparing sample…"))
            process = QProcess(self)
            self.process = process
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.sample_start = self.start_time.value()
            self.sample_length = self.length.value()
            command = [self.tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                       "-ss", f"{self.start_time.value():.3f}", "-i", str(self.source),
                       "-t", f"{self.length.value():.3f}", "-map", f"0:{self.media.video_index}",
                       "-an", "-sn", "-dn"]
            if self.media.hdr:
                from .pipeline import hdr_to_sdr_filters
                command += ["-vf", ",".join(hdr_to_sdr_filters(self.media)), "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
            command += ["-c:v", "ffv1", "-pix_fmt", "yuv444p",
                       "-fps_mode", "passthrough", str(self.clip)]
            process.finished.connect(self._clip_finished)
            process.errorOccurred.connect(self._clip_error)
            self.clip_timed_out = False
            process.start(command[0], command[1:])
            self.clip_timeout.start(60_000)
        except (ValueError, OSError, KeyError) as error:
            self._show_error(error)
            self.run_button.setEnabled(True)
            self.model_choice.setEnabled(True)
            self.encoder_choice.setEnabled(True)
            self.sample_quality.setEnabled(True)
            self.recovery.setEnabled(True)
            self.clear_reference_button.setEnabled(bool(self.reference))
            self.cancel_button.setEnabled(False)

    def _clip_timed_out(self):
        if self.process:
            self.clip_timed_out = True
            self.process.kill()

    def _clip_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.process:
            self.clip_timeout.stop()
            self._show_error(self.process.errorString())
            self.model_choice.setEnabled(True)
            self.encoder_choice.setEnabled(True)
            self.sample_quality.setEnabled(True)
            self.recovery.setEnabled(True)
            self.clear_reference_button.setEnabled(bool(self.reference))
            self.process.deleteLater()
            self.process = None
            self.run_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            if self.closing:
                self._close_now()

    def _clip_finished(self, code, status):
        if not self.process:
            return
        process, self.process = self.process, None
        self.clip_timeout.stop()
        error = bytes(process.readAllStandardOutput()).decode("utf-8", "replace").strip()
        if self.clip_timed_out:
            error = self.tr("Sample preparation timed out.")
        process.deleteLater()
        if self.closing:
            self._close_now()
            return
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            self._show_error(error or self.tr("Sample preparation failed or was cancelled."))
            self.model_choice.setEnabled(True)
            self.encoder_choice.setEnabled(True)
            self.sample_quality.setEnabled(True)
            self.recovery.setEnabled(True)
            self.clear_reference_button.setEnabled(bool(self.reference))
            self.run_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            return
        try:
            if not self.reference:
                self.before.set_source(self.clip)
            settings = replace(self.active_settings, output_dir=self.temp.name, container="mkv", audio="off",
                               copy_subtitles=False, subtitle_mode="off", subtitle_path="", watermark_path="")
            self.job = self.runner.enqueue([self.clip], settings)[0]
            self.runner.start(self.tools)
        except (ValueError, OSError, IndexError) as error:
            self._show_error(error)
            self.model_choice.setEnabled(True)
            self.encoder_choice.setEnabled(True)
            self.sample_quality.setEnabled(True)
            self.recovery.setEnabled(True)
            self.clear_reference_button.setEnabled(bool(self.reference))
            self.run_button.setEnabled(True)
            self.cancel_button.setEnabled(False)

    def _job_changed(self, job):
        self.status.setText(self.tr(job.state) + f" · {job.percent:.0f}%" + (" · " + job.error if job.error else ""))
        if job.state == "failed":
            self.show_log(job.error)

    def _busy_changed(self, busy):
        if busy:
            return
        self.run_button.setEnabled(True)
        self.model_choice.setEnabled(True)
        self.encoder_choice.setEnabled(True)
        self.sample_quality.setEnabled(True)
        self.recovery.setEnabled(True)
        self.clear_reference_button.setEnabled(bool(self.reference))
        self.cancel_button.setEnabled(False)
        if self.closing:
            self._close_now()
            return
        if self.job and self.job.state in ("done", "done_warning") and self.job.output and self.job.output.is_file():
            elapsed = time.monotonic() - self.runner.processing_started_at if self.runner.processing_started_at else 0
            size = self.job.output.stat().st_size / (1024 * 1024)
            entry = (f"{self.sample_start:.1f}s + {self.sample_length:.1f}s · {self.active_model_label} · "
                     f"{self.active_settings.codec} / {self.active_settings.quality} · {elapsed:.1f}s · {size:.2f} MiB")
            sample_duration = min(self.sample_length, max(0, self.media.duration - self.sample_start)) if self.media.duration else self.sample_length
            if sample_duration > 0 and self.media.duration > 0:
                key = repr(self.active_settings.export())
                observations = self.sample_observations.setdefault(key, [])
                observations.append((elapsed / sample_duration, size / sample_duration))
                times = [rate * self.media.duration / 60 for rate, _ in observations]
                sizes = [rate * self.media.duration for _, rate in observations]
                time_range = f"{min(times):.1f}–{max(times):.1f}" if len(times) > 1 else f"{times[0]:.1f}"
                size_range = f"{min(sizes):.0f}–{max(sizes):.0f}" if len(sizes) > 1 else f"{sizes[0]:.0f}"
                entry += ("\n" + self.tr("Estimated full video from this sample (video only; approximate)")
                          + f": {time_range} min · {size_range} MiB · {self.tr('Samples')}: {len(observations)}")
                entry += "\n" + self.tr("Range uses observed samples with identical settings; it is not a guarantee. Audio, subtitles and startup time are excluded.")
            self.sample_results.append(entry)
            self.results.setPlainText("\n".join(self.sample_results))
            self.playback_button.setEnabled(True)
            if not self.reference:
                self.before.set_source(self.clip)
            self.after.set_source(self.job.output)
            self.keep_reference_button.setEnabled(True)
            if self.job.state == "done":
                self.status.setText(self.tr("Sample ready. Use the timeline to compare."))

    def cancel(self):
        if self.process:
            self.process.kill()
        if self.runner.busy:
            self.runner.cancel()

    def reject(self):
        if self.process or self.runner.busy:
            self.closing = True
            self.status.setText(self.tr("Stopping sample…"))
            self.cancel()
            return
        self._close_now()

    def _close_now(self):
        self.before.shutdown()
        self.after.shutdown()
        if self.temp:
            self.temp.cleanup()
            self.temp = None
        if self.reference_temp:
            self.reference_temp.cleanup()
            self.reference_temp = self.reference = None
        super().reject()

    def closeEvent(self, event):
        if self.process or self.runner.busy:
            self.reject()
            event.ignore()
        else:
            self.before.shutdown()
            self.after.shutdown()
            if self.temp:
                self.temp.cleanup()
                self.temp = None
            if self.reference_temp:
                self.reference_temp.cleanup()
                self.reference_temp = self.reference = None
            event.accept()
