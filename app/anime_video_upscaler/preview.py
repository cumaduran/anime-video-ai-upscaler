"""Asynchronous source-frame preview with the final placement filter graph."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile

from PySide6.QtCore import QProcess, QTimer, Qt, Signal, QRectF
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout

from .config import config_dir
from .subtitles import stage_subtitle
from .pipeline import Media, fitted_size, target_size, preview_command


class FrameCanvas(QLabel):
    watermark_moved = Signal(float, float)
    watermark_resized = Signal(int)

    def __init__(self, placeholder, parent=None):
        super().__init__(parent)
        self.placeholder = placeholder
        self.frame = QImage()
        self.placement = None
        self.drag_offset = None
        self.drag_rect = None
        self.drag_mode = None
        self.max_watermark_width = 4096
        self.drag_outline = QFrame(self)
        self.drag_outline.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drag_outline.setStyleSheet("background: rgba(166,155,244,55); border: 2px dashed #d7cfff;")
        self.drag_outline.hide()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumHeight(150)
        self.setStyleSheet("background:#111216; border:1px solid #383a43; border-radius:6px; color:#a6a8b2;")
        self.setText(placeholder)

    def set_frame(self, frame):
        self.frame = frame
        self._redraw()

    def set_message(self, message):
        self.placement = None
        self.drag_offset = self.drag_rect = None
        self.drag_outline.hide()
        self.frame = QImage()
        self.clear()
        self.setText(message)

    def _redraw(self):
        if not self.frame.isNull():
            self.setPixmap(QPixmap.fromImage(self.frame).scaled(
                self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def set_placement(self, canvas_width, canvas_height, watermark_width, watermark_height, x, y):
        self.placement = (canvas_width, canvas_height, watermark_width, watermark_height, x, y)

    def _image_rect(self):
        if self.frame.isNull():
            return QRectF()
        scaled = self.frame.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width()-scaled.width())/2, (self.height()-scaled.height())/2,
                      scaled.width(), scaled.height())

    def _watermark_rect(self):
        if not self.placement:
            return QRectF()
        cw, ch, ww, wh, x, y = self.placement
        rect = self._image_rect()
        if rect.isEmpty() or not cw or not ch:
            return QRectF()
        width, height = rect.width()*ww/cw, rect.height()*wh/ch
        return QRectF(rect.left()+max(0,rect.width()-width)*x,
                      rect.top()+max(0,rect.height()-height)*y, width, height)

    def mousePressEvent(self, event):
        rect = self._watermark_rect()
        if event.button() == Qt.MouseButton.LeftButton and rect.contains(event.position()):
            self.drag_offset = event.position() - rect.topLeft()
            self.drag_mode = "resize" if (event.position().x() >= rect.right()-14 and event.position().y() >= rect.bottom()-14) else "move"
            self.drag_rect = rect
            self.drag_outline.setGeometry(rect.toAlignedRect())
            self.drag_outline.show()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and self.drag_rect is not None:
            image = self._image_rect()
            if self.drag_mode == "resize":
                cw, ch, ww, wh, _, _ = self.placement
                ratio = wh/ww if ww else 1
                max_width = min(image.right()-self.drag_rect.left(), image.width()*self.max_watermark_width/cw,
                                (image.bottom()-self.drag_rect.top())/ratio if ratio else image.width())
                width = min(max(event.position().x()-self.drag_rect.left(), 8*image.width()/cw), max_width)
                self.drag_rect.setWidth(max(1, width))
                self.drag_rect.setHeight(max(1, width*ratio))
            else:
                pos = event.position() - self.drag_offset
                self.drag_rect.moveLeft(min(max(pos.x(), image.left()), max(image.left(), image.right()-self.drag_rect.width())))
                self.drag_rect.moveTop(min(max(pos.y(), image.top()), max(image.top(), image.bottom()-self.drag_rect.height())))
            self.drag_outline.setGeometry(self.drag_rect.toAlignedRect())
            event.accept()
            return
        self.setCursor(Qt.CursorShape.OpenHandCursor if self._watermark_rect().contains(event.position()) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_offset is not None and self.drag_rect is not None:
            image = self._image_rect()
            dx = max(0.0, image.width()-self.drag_rect.width())
            dy = max(0.0, image.height()-self.drag_rect.height())
            x = (self.drag_rect.left()-image.left())/dx if dx else 0.0
            y = (self.drag_rect.top()-image.top())/dy if dy else 0.0
            mode = self.drag_mode
            width = round(self.drag_rect.width() / image.width() * self.placement[0]) if image.width() else 8
            self.drag_offset = self.drag_rect = self.drag_mode = None
            self.drag_outline.hide()
            if mode == "resize":
                self.watermark_resized.emit(min(4096, max(8, width)))
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
            self.watermark_moved.emit(min(1.0,max(0.0,x)), min(1.0,max(0.0,y)))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._redraw()


class PreviewPanel(QFrame):
    watermark_moved = Signal(float, float)
    watermark_resized = Signal(int)
    media_ready = Signal(object)

    def __init__(self, store, tools, translate, parent=None, *, note=None):
        super().__init__(parent)
        self.store, self.tools, self.tr = store, tools, translate
        self.source = None
        self.media = None
        self.process = None
        self.generation = 0
        self._closing = False
        self.restore_position = None
        self._jobs = set()
        self._job_cleanup = {}
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        top = QHBoxLayout()
        label = QLabel(self.tr("PREVIEW"))
        label.setObjectName("section")
        top.addWidget(label)
        self.status = QLabel(self.tr("Select a queued video"))
        self.status.setObjectName("muted")
        top.addWidget(self.status, 1)
        self.retry_button = QPushButton(self.tr("Retry preview"))
        self.retry_button.setVisible(False)
        self.retry_button.clicked.connect(self.retry)
        top.addWidget(self.retry_button)
        layout.addLayout(top)
        self.canvas = FrameCanvas(self.tr("Select a video to preview a frame."))
        self.canvas.setToolTip(self.tr("Drag the watermark to move it; drag its lower-right corner to resize."))
        self.canvas.watermark_moved.connect(self.watermark_moved)
        self.canvas.watermark_resized.connect(self.watermark_resized)
        layout.addWidget(self.canvas, 1)
        controls = QHBoxLayout()
        self.play_button = QPushButton(self.tr("Play"))
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self.toggle_playback)
        controls.addWidget(self.play_button)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._seeked)
        controls.addWidget(self.slider, 1)
        self.clock = QLabel("00:00 / 00:00")
        controls.addWidget(self.clock)
        layout.addLayout(controls)
        note_label = QLabel(self.tr(note or "Sampled source preview (up to 2 fps) · AI, denoise and flicker are not shown · no audio"))
        note_label.setObjectName("muted")
        note_label.setWordWrap(True)
        layout.addWidget(note_label)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(200)
        self.debounce.timeout.connect(self._render)
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(500)
        self.play_timer.timeout.connect(self._tick)
        self.store.changed.connect(self.request_refresh)

    @staticmethod
    def time_label(ms):
        seconds = max(0, int(ms // 1000))
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def _seeked(self, value):
        self.clock.setText(f"{self.time_label(value)} / {self.time_label(self.slider.maximum())}")
        self.request_refresh()

    def retry(self):
        path = self.source
        self.source = None
        self.set_source(path)

    def _show_error(self, detail):
        detail = str(detail).strip()
        message = self.tr("Preview unavailable")
        self.status.setText(message)
        self.status.setToolTip(detail)
        self.canvas.set_message(message + ("\n\n" + detail if detail else ""))
        self.retry_button.setVisible(bool(self.source))
        self._stop_playback()

    def set_source(self, path):
        path = Path(path) if path else None
        if path == self.source:
            return
        self._stop_playback()
        self._abort()
        self.source, self.media = path, None
        self.retry_button.setVisible(False)
        self.status.setToolTip("")
        self.slider.setEnabled(False)
        self.play_button.setEnabled(False)
        self.slider.setRange(0, 0)
        self.canvas.set_message(self.tr("Select a video to preview a frame."))
        if not path:
            self.status.setText(self.tr("Select a queued video"))
            return
        if not self.tools.get("ffprobe") or not self.tools.get("ffmpeg"):
            self._show_error(self.tr("Bundled preview tools are missing"))
            return
        self.status.setText(self.tr("Loading preview…"))
        command = [self.tools["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
        self._start(command, path.parent, "probe")

    def request_refresh(self):
        if self.source and self.media and not self._closing:
            self.debounce.start()

    def clear_source_and_wait(self):
        """Release input files before a temporary comparison folder is removed."""
        self.debounce.stop()
        self.set_source(None)
        for process in list(self._jobs):
            if process.state() != QProcess.ProcessState.NotRunning and not process.waitForFinished(2000):
                raise OSError("Preview process did not release the temporary sample")

    def _render(self):
        if not self.source or not self.media or self._closing:
            return
        self._abort()
        settings = self.store.value
        subtitle_name = watermark_name = ""
        temp = None
        try:
            config_dir().mkdir(parents=True, exist_ok=True)
            temp = tempfile.TemporaryDirectory(prefix=".preview-", dir=config_dir(), ignore_cleanup_errors=True)
            work = Path(temp.name)
            if settings.watermark_path:
                path = Path(settings.watermark_path)
                if not path.is_file() or path.suffix.lower() != ".png":
                    raise ValueError(self.tr("Choose a valid PNG watermark"))
                watermark_name = "watermark.png"
                shutil.copyfile(path, work / watermark_name)
                logo = QImage(str(path))
                if logo.isNull() or not logo.width() or not logo.height():
                    raise ValueError(self.tr("Choose a valid PNG watermark"))
                fit_w, fit_h = fitted_size(settings, self.media)
                cw, ch = target_size(settings, self.media) if settings.aspect == "pad" else (fit_w, fit_h)
                ww = min(settings.watermark_width, fit_w)
                self.canvas.max_watermark_width = min(4096, fit_w)
                wh = ww * logo.height() / logo.width()
                if settings.watermark_corner == "free":
                    x, y = settings.watermark_x, settings.watermark_y
                else:
                    mx, my = max(0, cw-ww), max(0, ch-wh)
                    x = min(1, settings.watermark_margin/mx) if settings.watermark_corner.endswith("left") and mx else max(0, 1-settings.watermark_margin/mx) if mx else 0
                    y = min(1, settings.watermark_margin/my) if settings.watermark_corner.startswith("top") and my else max(0, 1-settings.watermark_margin/my) if my else 0
                self.canvas.set_placement(cw, ch, ww, wh, x, y)
            else:
                self.canvas.placement = None
            if settings.subtitle_mode == "burn":
                path = Path(settings.subtitle_path)
                if not path.is_file() or path.suffix.lower() not in (".srt", ".ass", ".ssa", ".vtt"):
                    raise ValueError(self.tr("Choose a valid subtitle file"))
                subtitle_name = "subtitle" + path.suffix.lower()
                stage_subtitle(path, work / subtitle_name)
            command = preview_command(settings, self.tools, self.source, self.media,
                                      self.slider.value() / 1000, subtitle_name, watermark_name)
            self.status.setText(self.tr("Rendering preview…"))
            self._start(command, work, "frame", temp)
        except (ValueError, OSError) as error:
            if temp:
                temp.cleanup()
            self._show_error(error)

    def _start(self, command, cwd, kind, temp=None):
        process = QProcess(self)
        self.process = process
        self._jobs.add(process)
        generation = self.generation
        output = bytearray()
        errors = bytearray()
        settled = [False]
        self._job_cleanup[process] = (temp, settled)
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        timed_out = [False]
        def on_timeout():
            timed_out[0] = True
            process.kill()
        timeout.timeout.connect(on_timeout)
        process.setWorkingDirectory(str(cwd))
        def read():
            output.extend(bytes(process.readAllStandardOutput()))
            errors.extend(bytes(process.readAllStandardError()))
            if len(output) > 20_000_000 or len(errors) > 2_000_000:
                process.kill()
        process.readyReadStandardOutput.connect(read)
        process.readyReadStandardError.connect(read)
        def finish(code, status):
            if settled[0]:
                return
            settled[0] = True
            timeout.stop()
            read()
            self._jobs.discard(process)
            self._job_cleanup.pop(process, None)
            if temp is not None:
                temp.cleanup()
            current = process is self.process and generation == self.generation and not self._closing
            if process is self.process:
                self.process = None
            process.deleteLater()
            if not current:
                return
            if code != 0 or status != QProcess.ExitStatus.NormalExit:
                detail = bytes(errors[-4000:]).decode("utf-8", "replace").strip()
                if timed_out[0]:
                    detail = self.tr("Preview timed out. Try again or use a shorter video.")
                elif not detail:
                    detail = process.errorString()
                self._show_error(detail)
                return
            try:
                if kind == "probe":
                    self.media = Media.from_probe(json.loads(bytes(output)))
                    self.media_ready.emit(self.media)
                    maximum = min(2_147_483_647, int(self.media.duration * 1000))
                    self.slider.setRange(0, maximum)
                    self.slider.setEnabled(maximum > 0)
                    self.play_button.setEnabled(maximum > 0)
                    # Long videos often start with a black frame. Start at one
                    # second while leaving zero available for precise seeking.
                    position = self.restore_position
                    if position is None:
                        position = 1000 if maximum > 2000 else 0
                    position = min(maximum, max(0, position))
                    self.restore_position = None
                    if position:
                        self.slider.setValue(position)
                    else:
                        self._seeked(0)
                else:
                    frame = QImage.fromData(bytes(output), "PNG")
                    if frame.isNull():
                        raise ValueError("Invalid preview frame")
                    self.canvas.set_frame(frame)
                    self.status.setText(self.source.name)
                    self.status.setToolTip("")
                    self.retry_button.setVisible(False)
            except (ValueError, KeyError, TypeError) as error:
                self._show_error(error)
        process.finished.connect(finish)
        def on_error(error):
            if error == QProcess.ProcessError.FailedToStart:
                finish(-1, QProcess.ExitStatus.CrashExit)
        process.errorOccurred.connect(on_error)
        process.start(command[0], command[1:])
        timeout.start(30_000)

    def _abort(self):
        self.generation += 1
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self.process = None

    def toggle_playback(self):
        if self.play_timer.isActive():
            self._stop_playback()
        else:
            if self.slider.value() >= self.slider.maximum():
                self.slider.setValue(0)
            self.play_button.setText(self.tr("Pause"))
            self.play_timer.start()

    def _stop_playback(self):
        self.play_timer.stop()
        self.play_button.setText(self.tr("Play"))

    def _tick(self):
        # Sampled silent preview prioritizes responsive seeking and settings changes.
        if self.process is not None or self.debounce.isActive():
            return
        value = self.slider.value() + 500
        if value >= self.slider.maximum():
            self.slider.setValue(self.slider.maximum())
            self._stop_playback()
        else:
            self.slider.setValue(value)

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.debounce.stop()
        self.play_timer.stop()
        self.generation += 1
        self.process = None
        # Prevent a late finished/error callback from touching a window whose
        # QObject children are already being torn down. Finish each owned child
        # before its temporary sidecar directory is removed.
        for process in list(self._jobs):
            temp, settled = self._job_cleanup.pop(process, (None, [False]))
            settled[0] = True
            for signal in (process.readyReadStandardOutput, process.readyReadStandardError,
                           process.finished, process.errorOccurred):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(2000)
            if temp is not None:
                temp.cleanup()
            self._jobs.discard(process)
            process.deleteLater()
