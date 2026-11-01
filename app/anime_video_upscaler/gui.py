"""Qt Widgets frontend. All editors read and write the same Settings instance."""
from __future__ import annotations

from dataclasses import fields, replace
import html
import json
import os
from pathlib import Path
import re
import time

from PySide6.QtCore import QAbstractTableModel, QLibraryInfo, QModelIndex, QObject, QProcess, Qt, QTimer, QTranslator, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableView, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, build_label
from .config import (CODECS, CPU_PRESETS, ENGINES, FACTORY_PRESETS, MODELS, RIFE_MODELS,
    SHADERS, FACTORY_PRESET_DESCRIPTIONS, Settings, VIDEO_EXTENSIONS, atomic_json, config_dir, detect_tools,
    encoder_presets, default_encoder_preset, quality_maximum, numeric_ranges,
    legacy_config_path, load_preset, preset_document, read_json)
from .i18n import Translator, available_languages
from .diagnostics import LogDialog, failure_summary, read_log
from .preview import PreviewPanel
from .runner import QueueRunner
from .queue_state import TERMINAL, load_queue, save_queue
from .subtitles import match_subtitles, read_subtitle
from .pipeline import encoder_probe_command, fitted_size, target_size

STYLE = """
QWidget { background:#202226; color:#e9e9ed; font-family:'Segoe UI'; font-size:13px; }
QMainWindow, QDialog { background:#191b1e; }
QLabel#brand { font-size:27px; font-weight:700; }
QLabel#muted { color:#a6a8b2; }
QLabel#section { color:#a9a1fa; font-size:12px; font-weight:600; }
QFrame#card { background:#202226; border:1px solid #35373e; border-radius:10px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
 background:#292c31; border:1px solid #41444c; border-radius:5px; padding:6px; selection-background-color:#6558aa; }
QComboBox::drop-down { border:0; width:22px; }
QPushButton { background:#30333a; border:1px solid #464951; padding:8px 13px; border-radius:6px; }
QPushButton:hover { background:#3c4049; border-color:#8e83dc; }
QPushButton:disabled { color:#70737d; background:#25272c; border-color:#33353b; }
QPushButton#primary { background:#a69bf4; color:#17151e; border:0; font-weight:700; }
QPushButton#primary:hover { background:#beb5ff; }
QPushButton#info { background:#303039; color:#bdb2ff; border:1px solid #555065; border-radius:10px; padding:0; font-weight:700; }
QPushButton#info:hover { background:#50476b; }
QTabWidget::pane { border:1px solid #35373e; border-radius:6px; }
QTabBar::tab { padding:10px 16px; color:#a6a8b2; background:#202226; }
QTabBar::tab:selected { color:#ded9ff; border-bottom:2px solid #a69bf4; }
QHeaderView::section { background:#25282d; color:#aaaeba; border:0; padding:9px; text-align:left; }
QTableView { background:#202226; alternate-background-color:#25272d; border:0; gridline-color:#33353c; }
QTableView::item { padding:8px; }
QTableView::item:selected { background:#3e3a58; }
QProgressBar { border:0; background:#30323a; border-radius:5px; text-align:center; min-height:18px; }
QProgressBar::chunk { background:#8072c8; border-radius:5px; }
QScrollArea { border:0; }
QScrollBar:vertical { background:#202226; width:10px; }
QScrollBar::handle:vertical { background:#4b4d58; min-height:25px; border-radius:4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QCheckBox { spacing:8px; }
QToolTip { background:#343740; color:#eee; border:1px solid #666; }
"""
STYLE += "\nQComboBox::down-arrow { image: url('" + (Path(__file__).with_name("assets") / "chevron.svg").as_posix() + "'); width:12px; height:8px; }\n"

# Field labels and choices are shared by main and advanced editors.
LABELS = {
    "engine":"AI engine", "model":"Real-ESRGAN model", "scale":"AI scale", "gpu":"GPU index",
    "noise":"Denoise level", "cugan_model":"Real-CUGAN model", "cugan_threads":"CUGAN threads",
    "cugan_syncgap":"CUGAN sync gap", "shader":"Anime4K shader", "rife_model":"RIFE model",
    "fps_multiplier":"FPS multiplier", "scene_threshold":"Scene threshold (%)", "rife_uhd":"RIFE UHD",
    "hwaccel":"Hardware decoder", "recover_shutdown":"Verify and recover a legacy engine shutdown crash", "target":"Output resolution", "width":"Target width", "height":"Target height",
    "resume_segments":"Resume using saved AI segments",
    "aspect":"Aspect ratio", "codec":"Video encoder", "quality":"Quality (CRF / CQ / QP)", "preset":"Encoder speed",
    "pixel_format":"Pixel format", "container":"Container", "audio":"Audio", "audio_bitrate":"AAC bitrate (bits/s)", "restoration":"Denoise before AI", "copy_subtitles":"Keep source subtitles",
    "subtitle_mode":"External subtitles", "subtitle_path":"Subtitle file", "subtitle_language":"Subtitle language", "watermark_path":"PNG watermark",
    "watermark_corner":"Watermark position", "watermark_x":"Watermark horizontal position", "watermark_y":"Watermark vertical position", "watermark_width":"Watermark width (px)",
    "watermark_margin":"Watermark margin (px)", "watermark_opacity":"Watermark opacity",
    "flicker":"Flicker reduction", "bit_rate":"Video bitrate (bits/s)", "rc_buffer_size":"Rate buffer (bits)",
    "rc_min_rate":"Minimum bitrate", "rc_max_rate":"Maximum bitrate", "qmin":"Minimum quantizer",
    "qmax":"Maximum quantizer", "gop_size":"GOP size", "max_b_frames":"Maximum B-frames",
    "keyint_min":"Minimum keyframe interval", "refs":"Reference frames", "thread_count":"Encoder threads",
    "extra_options":"Additional encoder options", "output_dir":"Output folder",
}
CHOICES = {
    "engine": ENGINES, "model":MODELS, "scale":(2,3,4), "cugan_model":("models-se","models-pro","models-nose"),
    "shader":SHADERS, "rife_model":RIFE_MODELS, "hwaccel":("none","cuda","d3d11va","dxva2","vulkan","qsv"),
    "target":("4k","2k","native","custom"), "aspect":("pad","fit"), "codec":CODECS,
    "pixel_format":("yuv420p","yuv420p10le","p010le","nv12","yuv444p","yuv444p10le"),
    "container":("mkv","mp4"), "audio":("copy","aac","off"), "subtitle_mode":("off","burn","soft"),
    "subtitle_language":("tur", "eng", "jpn", "deu", "fra", "spa", "ita", "por", "kor", "zho", "ara", "rus", "und"),
    "watermark_corner":("bottom-right","bottom-left","top-right","top-left","free"), "flicker":("off","mild"), "restoration":("off","mild"),
}
VALUE_LABELS = {
    "tur":"Türkçe", "eng":"English", "jpn":"日本語", "deu":"Deutsch", "fra":"Français", "spa":"Español", "ita":"Italiano", "por":"Português", "kor":"한국어", "zho":"中文", "ara":"العربية", "rus":"Русский", "und":"Unknown",
    "none":"Resize / encode only", "4k":"4K UHD · 3840 × 2160", "2k":"QHD · 2560 × 1440", "native":"AI output size", "custom":"Custom size",
    "pad":"Preserve ratio + black bars", "fit":"Fit inside target", "off":"Off", "mild":"Experimental · 3-frame mean",
    "free":"Free placement", "burn":"Burn into video", "soft":"Selectable subtitle track", "copy":"Copy original audio", "aac":"AAC · re-encode",
}


PRESET_GROUPS = (
    ("Anime · CPU encoding", "Anime", "cpu"),
    ("Anime · NVIDIA encoding", "Anime", "nvidia"),
    ("Anime · AMD encoding", "Anime", "amd"),
    ("Video · CPU encoding", "Video", "cpu"),
    ("Video · NVIDIA encoding", "Video", "nvidia"),
    ("Video · AMD encoding", "Video", "amd"),
)


def populate_preset_choices(box, presets, translate):
    box.clear()
    box.addItem(translate("Choose a preset…"), None)
    for title, content, hardware in PRESET_GROUPS:
        entries = []
        for name in FACTORY_PRESETS:
            settings = FACTORY_PRESETS[name]
            kind = "nvidia" if settings.codec.endswith("_nvenc") else "amd" if settings.codec.endswith("_amf") else "cpu"
            family = "Anime" if name.startswith("Anime ·") else "Video"
            if content == family and hardware == kind:
                entries.append(name)
        if not entries:
            continue
        box.addItem("── " + translate(title) + " ──", None)
        box.model().item(box.count()-1).setEnabled(False)
        for name in entries:
            box.addItem(translate(name), name)
    custom = [name for name in presets if name not in FACTORY_PRESETS]
    if custom:
        box.addItem("── " + translate("Custom") + " ──", None)
        box.model().item(box.count()-1).setEnabled(False)
        for name in custom:
            box.addItem(name, name)


class SettingsStore(QObject):
    changed = Signal()

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.value = settings or Settings()

    def update(self, key, value):
        values = {key:value}
        if key == "codec" and value != self.value.codec:
            values["preset"] = default_encoder_preset(value)
            values["quality"] = min(self.value.quality, quality_maximum(value))
        if key == "model" and value != "realesr-animevideov3":
            values["scale"] = 4
        self.value = replace(self.value, **values)
        self.changed.emit()

    def set(self, settings):
        self.value = settings
        self.changed.emit()

    def update_many(self, **values):
        self.value = replace(self.value, **values)
        self.changed.emit()


def help_paragraphs(text):
    # Preserve translated text verbatim; split sentences without splitting numbers
    # such as 4.26 or 8.000.000. East Asian full stops also get their own line.
    paragraphs = []
    for block in text.splitlines():
        if block.strip():
            paragraphs.extend(part.strip() for part in re.split(
                r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜÀ-ÖØ-Þ0-9])|(?<=[。！？])\s*", block) if part.strip())
    return paragraphs


def help_html(text):
    return "".join("<p style='margin: 0 0 14px 0; line-height: 145%;'>" +
                   html.escape(part) + "</p>" for part in help_paragraphs(text))


class HelpDialog(QDialog):
    def __init__(self, title, explanation, close_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 360)
        self.setMinimumSize(440, 260)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 19px; font-weight: 700; color: #ded9ff;")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        body = QTextBrowser()
        body.setObjectName("helpBody")
        body.setStyleSheet("QTextBrowser { background:#25282e; color:#f0f0f4; border:1px solid #41444c; border-radius:6px; padding:12px; font-size:14px; }")
        body.setHtml(help_html(explanation))
        layout.addWidget(body, 1)
        buttons = QDialogButtonBox()
        buttons.addButton(close_text, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class SettingsForm(QWidget):
    def __init__(self, store, keys, translate, parent=None):
        super().__init__(parent)
        self.store, self.tr = store, translate
        self.controls = {}
        self.help_buttons = {}
        form = QFormLayout(self)
        form.setContentsMargins(8, 10, 8, 10)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for key in keys:
            value = getattr(store.value, key)
            if key in CHOICES or key == "preset":
                control = QComboBox()
                control.setMinimumContentsLength(10)
                control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
                for item in CHOICES.get(key, ()):
                    label = VALUE_LABELS.get(item, str(item)) if key not in ("hwaccel",) else str(item)
                    if key == "restoration" and item == "mild":
                        label = "Mild denoise"
                    control.addItem(self.tr(label), item)
                control.currentIndexChanged.connect(lambda _, k=key, c=control: self.store.update(k, c.currentData()))
            elif isinstance(value, bool):
                control = QCheckBox()
                control.toggled.connect(lambda v, k=key: self.store.update(k, v))
            elif isinstance(value, int):
                control = QSpinBox()
                control.setRange(*numeric_ranges(store.value.codec)[key])
                if key in ("width", "height"):
                    control.setSingleStep(2)
                if key == "audio_bitrate":
                    control.setRange(32000, 512000)
                    control.setSingleStep(16000)
                control.valueChanged.connect(lambda v, k=key: self.store.update(k, v))
            elif isinstance(value, float):
                control = QDoubleSpinBox()
                control.setRange(*numeric_ranges(store.value.codec)[key])
                control.setSingleStep(0.05 if key in ("watermark_x", "watermark_y", "watermark_opacity") else 1)
                control.valueChanged.connect(lambda v, k=key: self.store.update(k, v))
            elif key == "extra_options":
                control = QPlainTextEdit()
                control.setMaximumHeight(100)
                control.setPlaceholderText("tune=animation\nx264-params=aq-mode=3")
                control.textChanged.connect(lambda k=key, c=control: self.store.update(k, c.toPlainText()))
            else:
                control = QLineEdit()
                control.textEdited.connect(lambda v, k=key: self.store.update(k, v))
            control.setObjectName(key)
            control.setAccessibleName(self.tr(LABELS[key]))
            help_text = self.tr.help(key)
            tooltip = "<div style='max-width:440px; white-space:normal'>" + help_html(help_text) + "</div>"
            control.setToolTip(tooltip)
            self.controls[key] = control
            label_holder = QWidget()
            label_row = QHBoxLayout(label_holder)
            label_row.setContentsMargins(0, 0, 0, 0)
            label_row.setSpacing(5)
            label = QLabel(self.tr(LABELS[key]))
            label.setWordWrap(True)
            label.setToolTip(tooltip)
            label_row.addWidget(label, 1)
            info = QPushButton("i")
            info.setObjectName("info")
            info.setFixedSize(20,20)
            info.setToolTip(tooltip)
            info.setAccessibleName(self.tr("Help for") + " " + self.tr(LABELS[key]))
            info.clicked.connect(lambda _, k=key: HelpDialog(
                self.tr(LABELS[k]), self.tr.help(k), self.tr("Close"), self).exec())
            self.help_buttons[key] = info
            label_row.addWidget(info)
            if key.endswith("_path") or key == "output_dir":
                holder = QWidget()
                row = QHBoxLayout(holder)
                row.setContentsMargins(0,0,0,0)
                row.addWidget(control)
                choose = QPushButton("…")
                choose.setMaximumWidth(32)
                choose.setAccessibleName(self.tr("Choose") + " " + self.tr(LABELS[key]))
                choose.clicked.connect(lambda _, k=key: self.browse(k))
                row.addWidget(choose)
                form.addRow(label_holder, holder)
            else:
                form.addRow(label_holder, control)
        store.changed.connect(self.refresh)
        self.refresh()

    def browse(self, key):
        if key == "output_dir":
            value = QFileDialog.getExistingDirectory(self, self.tr("Output folder"))
        else:
            mask = "PNG (*.png)" if key == "watermark_path" else "Subtitles (*.srt *.ass *.ssa *.vtt)"
            value = QFileDialog.getOpenFileName(self, self.tr("Choose"), "", mask)[0]
        if value:
            self.store.update(key, value)

    def refresh(self):
        s = self.store.value
        for key, control in self.controls.items():
            control.blockSignals(True)
            value = getattr(s,key)
            if isinstance(control, QComboBox):
                if key == "preset":
                    presets = encoder_presets(s.codec)
                    control.clear()
                    for preset in presets:
                        control.addItem(preset, preset)
                control.setCurrentIndex(control.findData(value))
            elif isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, (QSpinBox,QDoubleSpinBox)):
                if key == "quality":
                    control.setRange(0, quality_maximum(s.codec))
                control.setValue(value)
            elif isinstance(control, QPlainTextEdit):
                if control.toPlainText() != value:
                    control.setPlainText(value)
            else:
                control.setText(value)
            control.blockSignals(False)
            enabled = True
            if key in ("width","height"):
                enabled = s.target == "custom"
            elif key == "model":
                enabled = s.engine == "realesrgan"
            elif key == "scale":
                enabled = s.engine in ("realesrgan","realcugan","libplacebo")
            elif key.startswith("cugan"):
                enabled = s.engine == "realcugan"
            elif key in ("rife_model","rife_uhd","fps_multiplier","scene_threshold"):
                enabled = s.engine == "rife"
            elif key == "shader":
                enabled = s.engine == "libplacebo"
            elif key in ("watermark_x", "watermark_y"):
                enabled = s.watermark_corner == "free"
            elif key == "audio_bitrate":
                enabled = s.audio == "aac"
            elif key == "quality":
                enabled = s.bit_rate == 0
            control.setEnabled(enabled)


def scroll(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    return area


class QueueModel(QAbstractTableModel):
    def __init__(self, runner, translate, parent=None):
        super().__init__(parent)
        self.runner, self.tr = runner, translate

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.runner.jobs)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        job = self.runner.jobs[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            name = job.source.name + ("  ✦" if job.settings_locked else "")
            return (name, self.tr(job.state), f"{job.percent:.0f}%", job.output.name if job.output else "—")[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return job.error or str(job.source)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.tr(("Video", "Status", "Progress", "Output")[section])


class DropTable(QTableView):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        event.acceptProposedAction()
        self.files_dropped.emit(paths)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME + " " + __version__ + " · dev")
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("assets") / "icon.svg")))
        self.resize(1600, 950)
        self.setMinimumSize(1000, 680)
        self.store = SettingsStore(parent=self)
        self.runner = QueueRunner(self)
        self.language = "en"
        self.tools = detect_tools()
        self.presets = dict(FACTORY_PRESETS)
        self.error_log_dialog = None
        self._startup_error = ""
        self._queue_error = ""
        self._closing = False
        self._loading_editor = False
        self.sync_settings = True
        legacy = legacy_config_path()
        first_run = not (config_dir() / "settings.json").exists() and not (legacy and legacy.exists())
        self._load()
        self._shared_settings = self.store.value
        self._load_queue()
        # Restored processing snapshots can differ from the last shared edit.
        # Keep those settings and display independent mode instead of overwriting
        # them or claiming that the restored videos all share the same settings.
        if self.sync_settings and any(job.settings != self._shared_settings for job in self.runner.jobs):
            self.sync_settings = False
        self.tr = Translator(self.language)
        self._qt_translator = None
        self.install_qt_translation()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save)
        self._queue_timer = QTimer(self)
        self._queue_timer.setSingleShot(True)
        self._queue_timer.timeout.connect(self._save_queue)
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._refresh_timing)
        self._queue_started_at = None
        self._queue_states = {job.id: job.state for job in self.runner.jobs}
        self.store.changed.connect(self._settings_changed)
        self.runner.changed.connect(self._job_changed)
        self.runner.log.connect(self._log)
        self.runner.busy_changed.connect(self._busy_changed)
        self._building = False
        self.build()
        if first_run and QApplication.platformName() != "offscreen":
            QTimer.singleShot(0, self.setup_wizard)
        if self._startup_error:
            self._log(self._startup_error)
        if self._queue_error:
            self._log(self._queue_error)

    def button(self, text, callback, primary=False):
        button = QPushButton(self.tr(text))
        button.clicked.connect(callback)
        if primary:
            button.setObjectName("primary")
        return button

    def build(self):
        self._building = True
        previous = self.centralWidget()
        previous_log = self.logs.toPlainText() if previous and hasattr(self,"logs") else ""
        previous_preset = self.preset_box.currentData() if previous and hasattr(self,"preset_box") else None
        previous_source = self.preview.source if previous and hasattr(self,"preview") else None
        previous_position = self.preview.slider.value() if previous and hasattr(self,"preview") else 0
        if previous and hasattr(self,"preview"):
            self.preview.shutdown()
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24,20,24,20)
        layout.setSpacing(16)
        header = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(self.windowIcon().pixmap(56,56))
        header.addWidget(mark)
        title_box = QVBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("brand")
        title_box.addWidget(title)
        sub = QLabel(self.tr("A considered finish for every frame") + "  ·  " + __version__ + "  ·  " + self.tr("Development preview") + "  ·  " + build_label())
        sub.setObjectName("muted")
        title_box.addWidget(sub)
        header.addLayout(title_box)
        header.addStretch()
        header.addWidget(self.button("Tools", self.tool_dialog))
        self.language_box = QComboBox()
        for code, label in available_languages():
            self.language_box.addItem(label,code)
        self.language_box.setCurrentIndex(self.language_box.findData(self.language))
        self.language_box.currentIndexChanged.connect(self.change_language)
        header.addWidget(self.language_box)
        layout.addLayout(header)
        splitter = QSplitter()
        left = QFrame()
        left.setObjectName("card")
        left_layout = QVBoxLayout(left)
        preset_heading = QLabel(self.tr("PRESETS"))
        preset_heading.setObjectName("section")
        left_layout.addWidget(preset_heading)
        preset_row = QHBoxLayout()
        self.preset_box = QComboBox()
        self.refresh_presets(previous_preset)
        self.preset_box.currentIndexChanged.connect(self.show_preset_description)
        preset_row.addWidget(self.preset_box,1)
        self.apply_preset_button = self.button("Apply", self.apply_preset)
        preset_row.addWidget(self.apply_preset_button)
        left_layout.addLayout(preset_row)
        preset_actions = QHBoxLayout()
        self.preset_action_buttons = [self.button("Save preset", self.save_preset),
                                      self.button("Import", self.import_preset),
                                      self.button("Export", self.export_preset)]
        for button in self.preset_action_buttons:
            preset_actions.addWidget(button)
        left_layout.addLayout(preset_actions)
        self.preset_description = QLabel()
        self.preset_description.setWordWrap(True)
        self.preset_description.setObjectName("muted")
        self.preset_description.setMinimumHeight(40)
        left_layout.addWidget(self.preset_description)
        self.show_preset_description()
        left_layout.addWidget(self.button("Setup guide", self.setup_wizard))
        left_layout.addWidget(self.button("Hardware compatibility", self.tool_dialog))
        self.sync_checkbox = QCheckBox(self.tr("Sync settings across videos"))
        self.sync_checkbox.setObjectName("syncVideoSettings")
        self.sync_checkbox.setChecked(self.sync_settings)
        self.sync_checkbox.setToolTip(self.tr("Checked: use the same settings for all videos. Unchecked: edit each selected video separately. A video currently processing keeps its settings."))
        self.sync_checkbox.toggled.connect(self.change_sync_settings)
        left_layout.addWidget(self.sync_checkbox)
        self.detail_settings_toggle = QCheckBox(self.tr("Show detailed settings"))
        left_layout.addWidget(self.detail_settings_toggle)
        controls = QTabWidget()
        self.settings_tabs = controls
        self.basics = SettingsForm(self.store, ["engine","model","scale","target","width","height","aspect","codec","preset","quality","bit_rate","container","output_dir"], self.tr)
        controls.addTab(scroll(self.basics), self.tr("Video"))
        finish = QWidget()
        finish_layout = QVBoxLayout(finish)
        self.finish_form = SettingsForm(self.store,["audio","audio_bitrate","restoration","copy_subtitles","subtitle_mode","subtitle_path","subtitle_language","watermark_path","watermark_corner","watermark_x","watermark_y","watermark_width","watermark_margin","watermark_opacity","flicker"],self.tr)
        finish_layout.addWidget(self.finish_form)
        warning = QLabel(self.tr("Flicker reduction is experimental. It may alter flashes or scene cuts. Test a short clip; removal is not guaranteed."))
        warning.setWordWrap(True)
        warning.setObjectName("muted")
        finish_layout.addWidget(warning)
        depth_warning = QLabel(self.tr("AI processing uses 8-bit RGB. A 10-bit export cannot restore precision lost in this stage."))
        depth_warning.setWordWrap(True)
        depth_warning.setObjectName("muted")
        finish_layout.addWidget(depth_warning)
        hdr_warning = QLabel(self.tr("HDR input is tone-mapped to SDR (BT.709); HDR highlights and metadata are not preserved."))
        hdr_warning.setWordWrap(True)
        hdr_warning.setObjectName("muted")
        finish_layout.addWidget(hdr_warning)
        finish_layout.addStretch()
        controls.addTab(scroll(finish),self.tr("Finishing"))
        self.detail_settings_toggle.toggled.connect(self._show_detailed_settings)
        self._show_detailed_settings(False)
        left_layout.addWidget(controls)
        self.advanced_button = self.button("Advanced settings", self.advanced_dialog)
        left_layout.addWidget(self.advanced_button)
        self.settings_scope_label = QLabel()
        self.settings_scope_label.setObjectName("muted")
        self.settings_scope_label.setWordWrap(True)
        left_layout.addWidget(self.settings_scope_label)
        # Settings are constructed first, then placed on the right after the
        # queue and preview so that the central canvas gets the most room.
        left.setMinimumWidth(340)
        right = QFrame()
        right.setObjectName("card")
        right.setMinimumWidth(350)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10,10,10,10)
        queue_header = QHBoxLayout()
        self.count_label = QLabel(f"{self.tr('Queue')} · {len(self.runner.jobs)}")
        self.count_label.setObjectName("section")
        queue_header.addWidget(self.count_label)
        queue_header.addStretch()
        right_layout.addLayout(queue_header)
        right_layout.addWidget(self.button("Add videos",self.add_dialog))
        queue_actions = QHBoxLayout()
        queue_actions.addWidget(self.button("Remove selected",self.remove_selected))
        queue_actions.addWidget(self.button("Retry selected",self.retry_selected))
        right_layout.addLayout(queue_actions)
        order_actions = QHBoxLayout()
        order_actions.addWidget(self.button("Move up", lambda: self.move_selected(-1)))
        order_actions.addWidget(self.button("Move down", lambda: self.move_selected(1)))
        right_layout.addLayout(order_actions)
        right_layout.addWidget(self.button("Match subtitles from folder", self.match_subtitles_dialog))
        right_layout.addWidget(self.button("Manage recovery files", self.manage_checkpoints))
        right_layout.addWidget(self.button("Queue readiness report", self.queue_report))
        right_layout.addWidget(self.button("Open selected output", self.open_selected_output))
        right_layout.addWidget(self.button("Settings for selected video", self.job_settings_dialog))
        drop_hint = QLabel(self.tr("Drop videos here · multiple files and Unicode names supported"))
        drop_hint.setObjectName("muted")
        drop_hint.setWordWrap(True)
        right_layout.addWidget(drop_hint)
        queue_hint = QLabel(self.tr("Queue is saved locally. Interrupted jobs require Retry selected."))
        queue_hint.setObjectName("muted")
        queue_hint.setWordWrap(True)
        right_layout.addWidget(queue_hint)
        self.queue_model = QueueModel(self.runner,self.tr,self)
        self.table = DropTable()
        self.table.setModel(self.queue_model)
        self.table.files_dropped.connect(self.add_paths)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(120)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.ResizeToContents)
        right_layout.addWidget(self.table,1)
        center = QWidget()
        center.setMinimumWidth(330)
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0,0,0,0)
        self.preview_store = SettingsStore(self.store.value, center)
        self.preview = PreviewPanel(self.preview_store, self.tools, self.tr, center)
        self.preview.watermark_moved.connect(self.move_watermark)
        self.preview.watermark_resized.connect(self.resize_watermark)
        self.preview.media_ready.connect(lambda media: self._refresh_output_summary())
        center_layout.addWidget(self.preview,1)
        self.output_summary = QLabel()
        self.output_summary.setWordWrap(True)
        self.output_summary.setObjectName("muted")
        center_layout.addWidget(self.output_summary)
        self.subtitle_summary = QLabel()
        self.subtitle_summary.setWordWrap(True)
        self.subtitle_summary.setObjectName("muted")
        center_layout.addWidget(self.subtitle_summary)
        center_layout.addWidget(self.button("Compare AI sample", self.compare_sample))
        self.table.selectionModel().currentRowChanged.connect(self.preview_selection)
        self.table.selectionModel().selectionChanged.connect(self._refresh_start_controls)
        tabs = QTabWidget()
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1500)
        self.logs.setPlainText(previous_log)
        tabs.addTab(self.logs,self.tr("Activity log"))
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        tabs.addTab(self.summary,self.tr("All current settings"))
        tabs.setMaximumHeight(160)
        self.details_tabs = tabs
        self.details_toggle = QPushButton(self.tr("Show activity and settings"))
        self.details_toggle.setCheckable(True)
        self.details_toggle.toggled.connect(tabs.setVisible)
        center_layout.addWidget(self.details_toggle)
        tabs.setVisible(False)
        center_layout.addWidget(tabs)
        splitter.addWidget(right)
        splitter.addWidget(center)
        splitter.addWidget(left)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setStretchFactor(2, 1)
        splitter.setSizes([480,700,420])
        layout.addWidget(splitter,1)
        footer = QHBoxLayout()
        self.status_label = QLabel(self.tr("Ready"))
        footer.addWidget(self.status_label,1)
        self.timing_label = QLabel()
        self.timing_label.setObjectName("muted")
        self.timing_label.setWordWrap(True)
        self.timing_label.setMaximumWidth(530)
        footer.addWidget(self.timing_label)
        self.support_link = QLabel(
            '<a href="https://github.com/cumaduran" style="color:#a6a8b2; text-decoration:none;">'
            + html.escape(self.tr("Support the project")) + ' · @cumaduran</a>')
        self.support_link.setObjectName("supportProject")
        self.support_link.setOpenExternalLinks(True)
        self.support_link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.support_link.setAccessibleName(self.tr("Support the project") + " · @cumaduran")
        self.support_link.setToolTip("GitHub · https://github.com/cumaduran")
        footer.addWidget(self.support_link)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(300)
        footer.addWidget(self.progress,1)
        self.cancel_button = self.button("Stop queue",self.runner.cancel)
        footer.addWidget(self.cancel_button)
        self.start_button = self.button("Start queue",self.start,True)
        footer.addWidget(self.start_button)
        layout.addLayout(footer)
        self.setCentralWidget(root)
        if previous:
            previous.deleteLater()
        self._building = False
        if previous_source:
            for index, job in enumerate(self.runner.jobs):
                if job.source == previous_source:
                    self.table.selectRow(index)
                    # A selected preview is probed asynchronously, so retain its
                    # requested position until the metadata becomes available.
                    self.preview.restore_position = previous_position
                    break
        if not self.table.currentIndex().isValid() and self.runner.jobs:
            self.table.selectRow(0)
        self._refresh_settings_view()
        self._busy_changed(self.runner.busy)

    def _refresh_settings_view(self):
        self.summary.setPlainText("\n".join(f"{self.tr(LABELS[f.name])}: {getattr(self.store.value,f.name)}" for f in fields(Settings)))
        self._sync_preview_settings()
        self._update_settings_scope()
        self._refresh_output_summary()

    def _show_detailed_settings(self, shown):
        for key in ("scale", "width", "height", "aspect", "preset", "bit_rate"):
            self.basics.layout().setRowVisible(self.basics.controls[key], shown)
        for key in ("audio_bitrate", "restoration", "watermark_corner", "watermark_x", "watermark_y", "watermark_width", "watermark_margin", "watermark_opacity", "flicker"):
            self.finish_form.layout().setRowVisible(self.finish_form.controls[key], shown)

    def _refresh_output_summary(self):
        if not hasattr(self, "output_summary"):
            return
        job = self.selected_job()
        settings = job.settings if job else self.store.value
        media = self.preview.media if job and self.preview.source == job.source else None
        size = "—"
        if media:
            w, h = target_size(settings, media) if settings.aspect == "pad" else fitted_size(settings, media)
            size = f"{w} × {h}"
        subtitle_count = len(media.subtitle_codecs) if media and settings.copy_subtitles else 0 if not settings.copy_subtitles else "—"
        self.output_summary.setText(self.tr("Output") + f": {size} · {settings.codec} · {settings.container.upper()} · " + self.tr("Audio") + ": " + self.tr(VALUE_LABELS.get(settings.audio, settings.audio)) + " · " + self.tr("Source subtitles") + f": {subtitle_count}")
        if settings.subtitle_mode == "off" or not settings.subtitle_path:
            self.subtitle_summary.setText(self.tr("External subtitles") + ": " + self.tr("Off"))
            self.subtitle_summary.setToolTip("")
            return
        try:
            text, encoding = read_subtitle(Path(settings.subtitle_path))
            lines = [re.sub(r"<[^>]*>", "", line).strip() for line in text.splitlines() if line.strip() and "-->" not in line and not line.strip().isdigit()]
            sample = " / ".join(lines[:2])[:180]
            detail = f"{Path(settings.subtitle_path).name} · {encoding} · {sample}"
            self.subtitle_summary.setText(self.tr("External subtitles") + ": " + detail)
            self.subtitle_summary.setToolTip(detail)
        except (OSError, ValueError) as error:
            self.subtitle_summary.setText(self.tr("External subtitle problem") + ": " + str(error))
            self.subtitle_summary.setToolTip(str(error))

    def _load_editor_settings(self, settings):
        if self.store.value != settings:
            self._loading_editor = True
            try:
                self.store.set(settings)
            finally:
                self._loading_editor = False
        self._refresh_settings_view()

    def _can_edit_job(self, job):
        return job is not None and job.state in TERMINAL and job is not self.runner.current

    def _update_settings_scope(self):
        job = self.selected_job()
        editable = self.sync_settings or self._can_edit_job(job) or not self.runner.jobs
        self.settings_tabs.setEnabled(editable)
        self.advanced_button.setEnabled(editable)
        self.preset_box.setEnabled(editable)
        self.apply_preset_button.setEnabled(editable and self.preset_box.currentData() is not None)
        for button in self.preset_action_buttons:
            button.setEnabled(editable)
        if self.sync_settings:
            label = self.tr("Shared settings for all videos. A video currently processing keeps its settings.")
        elif self._can_edit_job(job):
            label = self.tr("Editing only: {name}").format(name=job.source.name)
        elif job:
            label = self.tr("This video is processing. Select another video to edit its settings.")
        else:
            label = self.tr("No video selected. Settings become defaults for new videos.") if not self.runner.jobs else self.tr("Select a video to edit its settings.")
        self.settings_scope_label.setText(label)

    def change_sync_settings(self, enabled):
        self.sync_settings = bool(enabled)
        if self.sync_settings:
            self._settings_changed()
        else:
            if self.selected_job() is None and self.runner.jobs:
                self.table.selectRow(0)
            job = self.selected_job()
            if job:
                self._load_editor_settings(job.settings)
        self._update_settings_scope()
        self._save_timer.start(600)
        self._queue_timer.start(600)

    def _settings_changed(self):
        if self._building or self._loading_editor:
            return
        selected = self.selected_job()
        if self.sync_settings:
            self._shared_settings = self.store.value
            for job in self.runner.jobs:
                if self._can_edit_job(job):
                    job.settings = self.store.value
                    job.settings_locked = False
        elif self._can_edit_job(selected):
            selected.settings = self.store.value
            selected.settings_locked = True
        elif not self.runner.jobs:
            self._shared_settings = self.store.value
        if self.runner.jobs:
            self.queue_model.dataChanged.emit(self.queue_model.index(0,0),self.queue_model.index(len(self.runner.jobs)-1,3))
        self._refresh_settings_view()
        self._save_timer.start(600)
        self._queue_timer.start(600)

    def _load_queue(self):
        path = config_dir() / "queue.json"
        if not path.exists():
            return
        try:
            self.runner.jobs = load_queue(path)
        except (ValueError, OSError, TypeError, KeyError) as error:
            self._queue_error = f"Queue could not be loaded: {error}. Original file retained."

    def _save_queue(self):
        if self._queue_error:
            return
        try:
            # Do not overwrite the last readable snapshot with a temporarily
            # invalid combination while a user is editing a video setting.
            for job in self.runner.jobs:
                job.settings.validate()
            save_queue(config_dir() / "queue.json", self.runner.jobs)
        except (ValueError, OSError) as error:
            self._log(f"Queue could not be saved: {error}")

    def _load(self):
        path = config_dir() / "settings.json"
        if not path.exists():
            legacy = legacy_config_path()
            if legacy is None or not legacy.exists():
                return
            path = legacy
        try:
            raw = read_json(path)
            if raw.get("schema") != 1:
                raise ValueError("Unsupported settings schema")
            self.store.value = Settings.from_dict(raw.get("settings",{}))
            saved_sync = raw.get("sync_settings", True)
            if type(saved_sync) is not bool:
                raise ValueError("Invalid sync settings preference")
            self.sync_settings = saved_sync
            self.language = raw.get("language","en")
            if self.language not in dict(available_languages()):
                self.language = "en"
            # Never import legacy paths pointing at other installed applications.
            for preset in raw.get("presets",[]):
                name, settings = load_preset(preset)
                if name in FACTORY_PRESETS:
                    if settings == FACTORY_PRESETS[name]:
                        continue
                    # Earlier builds allowed overwriting factory names. Preserve the
                    # user's settings under a new name without changing the system preset.
                    base = name + " (Custom)"
                    name = base
                    suffix = 2
                    while name in self.presets:
                        name = f"{base} {suffix}"
                        suffix += 1
                elif name in self.presets:
                    base = name
                    suffix = 2
                    while name in self.presets:
                        name = f"{base} ({suffix})"
                        suffix += 1
                self.presets[name] = settings
        except (ValueError,OSError,KeyError,TypeError) as error:
            self._startup_error = f"Settings could not be loaded: {error}. Original file retained."

    def _save(self):
        # Never replace an unreadable user file or persist a temporarily invalid edit.
        if self._startup_error:
            return
        try:
            self._shared_settings.validate()
            atomic_json(config_dir()/"settings.json", {"schema":1,"language":self.language,
                "sync_settings":self.sync_settings, "settings":self._shared_settings.export(),
                "presets":[preset_document(name,s) for name,s in self.presets.items() if name not in FACTORY_PRESETS]})
        except ValueError:
            pass
        except OSError as error:
            self._log(str(error))

    def _log(self, text):
        if hasattr(self,"logs"):
            self.logs.appendPlainText(text)

    def error(self, error):
        QMessageBox.warning(self,self.tr("Please check"),str(error))

    def add_dialog(self):
        paths = QFileDialog.getOpenFileNames(self,self.tr("Add videos"),"","Videos (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.m2ts *.wmv)")[0]
        self.add_paths(paths)

    def add_paths(self, paths):
        # Incremental batches keep Qt responsive during large Explorer drops.
        self._add_paths_batch(list(paths), self.store.value)

    def _add_paths_batch(self, paths, initial_settings):
        if self._closing:
            return
        chunk = paths[:100]
        del paths[:100]
        valid = [Path(p) for p in chunk if Path(p).suffix.lower() in VIDEO_EXTENSIONS and Path(p).is_file()]
        previous = self.selected_job()
        settings = self._shared_settings if self.sync_settings else initial_settings
        self.queue_model.beginResetModel()
        self.runner.enqueue(valid,settings)
        self.queue_model.endResetModel()
        self._queue_timer.start(600)
        if self.runner.jobs and not self.table.currentIndex().isValid():
            row = self.runner.jobs.index(previous) if previous in self.runner.jobs else 0
            self.table.selectRow(row)
        self.count_label.setText(f"{self.tr('Queue')} · {len(self.runner.jobs)}")
        self._busy_changed(self.runner.busy)
        if len(valid) < len(chunk):
            self._log(self.tr("Some dropped items are not supported local video files."))
        if paths:
            QTimer.singleShot(0, lambda: self._add_paths_batch(paths, initial_settings))

    def remove_selected(self):
        selected = {i.row() for i in self.table.selectionModel().selectedRows()}
        self.queue_model.beginResetModel()
        self.runner.jobs[:] = [j for i,j in enumerate(self.runner.jobs) if i not in selected or j is self.runner.current]
        self.queue_model.endResetModel()
        self._queue_timer.start(600)
        self.count_label.setText(f"{self.tr('Queue')} · {len(self.runner.jobs)}")
        self._busy_changed(self.runner.busy)

    def move_selected(self, direction):
        # Do not change the order beneath an active runner.
        if self.runner.busy or direction not in (-1, 1):
            return
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows or rows[0] + direction < 0 or rows[-1] + direction >= len(self.runner.jobs):
            return
        self.queue_model.beginResetModel()
        for row in (rows if direction < 0 else reversed(rows)):
            other = row + direction
            self.runner.jobs[row], self.runner.jobs[other] = self.runner.jobs[other], self.runner.jobs[row]
        self.queue_model.endResetModel()
        selection = self.table.selectionModel()
        from PySide6.QtCore import QItemSelectionModel
        for row in rows:
            selection.select(self.queue_model.index(row + direction, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        selection.setCurrentIndex(self.queue_model.index(rows[0] + direction, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
        self._save_queue()

    def open_selected_output(self):
        job = self.selected_job()
        if job and job.output and job.output.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(job.output)))
        else:
            self.error(self.tr("The selected video has no completed output to open."))

    def manage_checkpoints(self):
        if self.runner.busy:
            self.error(self.tr("Stop the queue before managing recovery files."))
            return
        selected = self.selected_job()
        initial = selected.settings.output_dir if selected and selected.settings.output_dir else (str(selected.source.parent) if selected else self.store.value.output_dir)
        folder = QFileDialog.getExistingDirectory(self, self.tr("Choose an output folder"), initial or "")
        if not folder:
            return
        from .checkpoints import find_checkpoints, remove_checkpoint
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Manage recovery files"))
        dialog.resize(760, 450)
        layout = QVBoxLayout(dialog)
        note = QLabel(self.tr("Only recognized recovery folders in the chosen output folder are shown. Removing them prevents reuse of those saved segments."))
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QListWidget()
        listing.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(listing)
        status = QLabel()
        layout.addWidget(status)
        records = []
        def refresh():
            nonlocal records
            listing.clear()
            try:
                records = find_checkpoints(Path(folder))
                for item in records:
                    listing.addItem(f"{Path(item.source).name} · {item.segments} {self.tr('segments')} · {item.size / 1024**3:.2f} GiB\n{item.path.name}")
                    listing.item(listing.count() - 1).setToolTip(item.source + "\n" + str(item.path))
                status.setText(self.tr("Recovery folders") + f": {len(records)}")
            except OSError as error:
                status.setText(str(error))
        def remove():
            if self.runner.busy:
                status.setText(self.tr("Stop the queue before managing recovery files."))
                return
            chosen = [records[listing.row(item)] for item in listing.selectedItems()]
            if not chosen:
                return
            if QMessageBox.question(dialog, self.tr("Remove recovery files"), self.tr("Remove selected recovery folders? Saved segments cannot be recovered.") + f"\n\n{len(chosen)}") != QMessageBox.StandardButton.Yes:
                return
            errors = []
            for item in chosen:
                try:
                    remove_checkpoint(item)
                except (OSError, ValueError) as error:
                    errors.append(f"{item.path.name}: {error}")
            refresh()
            if errors:
                status.setText("\n".join(errors))
        actions = QHBoxLayout()
        refresh_button = QPushButton(self.tr("Refresh"))
        refresh_button.clicked.connect(refresh)
        remove_button = QPushButton(self.tr("Remove selected"))
        remove_button.clicked.connect(remove)
        actions.addWidget(refresh_button)
        actions.addWidget(remove_button)
        layout.addLayout(actions)
        refresh()
        dialog.exec()

    def queue_report(self):
        if self.runner.busy:
            self.error(self.tr("Stop the queue before checking the report."))
            return
        jobs = [job for job in self.runner.jobs if job.state == "queued"]
        if not jobs:
            self.error(self.tr("No waiting videos."))
            return
        probe = self.tools.get("ffprobe", "")
        if not probe or not Path(probe).is_file():
            self.error(self.tr("Could not start the media checking tool"))
            return
        from .preflight import check_media_queue
        from .pipeline import lossless_working_bytes, output_path
        from .disk_policy import RESERVE_BYTES
        import shutil
        for job in jobs:
            job.media_info = None
        problems = check_media_queue(self, jobs, probe, self.tr)
        if problems is None:
            return
        lines = [self.tr("Results are estimates. Disk space and encoder availability are checked again during processing.")]
        for job in jobs:
            lines.append("\n" + job.source.name)
            try:
                job.settings.validate()
                output = output_path(job.source, job.settings, set(), job.output_name)
                folder = output.parent
                while not folder.exists() and folder != folder.parent:
                    folder = folder.parent
                free_bytes = shutil.disk_usage(folder).free
                lines.append(self.tr("Output folder") + f": {output.parent} · {free_bytes / 1024**3:.1f} GiB " + self.tr("free"))
                if free_bytes < RESERVE_BYTES:
                    lines.append(self.tr("Warning: free space is below the 1 GiB safety reserve."))
                lines.append(self.tr("Final encoder") + f": {job.settings.codec} · {self.tr('AI engine')}: {job.settings.engine}")
                if job.media_info:
                    media = job.media_info
                    lines.append(f"{media.width} × {media.height} · {media.fps:.3g} fps · {media.duration:.1f} s")
                    reference = lossless_working_bytes(job.settings, media)
                    if reference:
                        lines.append(self.tr("Uncompressed working-data reference, not a disk-space prediction") + f": {reference / 1024**3:.1f} GiB")
                    if media.hdr:
                        lines.append(self.tr("HDR input is tone-mapped to SDR (BT.709); HDR highlights and metadata are not preserved."))
            except (OSError, ValueError) as error:
                lines.append(str(error))
        if problems:
            lines.append("\n" + self.tr("Check these videos before starting:") + "\n" + "\n".join(problems))
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Queue readiness report"))
        dialog.resize(800, 560)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit("\n".join(lines))
        text.setReadOnly(True)
        layout.addWidget(text)
        dialog.exec()

    def match_subtitles_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr("Choose the subtitle folder"))
        if not folder:
            return
        jobs = [job for job in self.runner.jobs if job is not self.runner.current and job.state in TERMINAL]
        try:
            matches, unmatched = match_subtitles([job.source for job in jobs], Path(folder))
        except OSError as error:
            self.error(error)
            return
        lines = [f"{source.name}  →  {subtitle.name}" for source, subtitle in matches.items()]
        lines += [self.tr("No unique match") + f": {source.name}" for source in unmatched]
        if not matches:
            self.error(self.tr("No unique subtitle matches found.") + "\n\n" + "\n".join(lines[:50]))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Review subtitle matches"))
        dialog.resize(720, 500)
        layout = QVBoxLayout(dialog)
        note = QLabel(self.tr("Review these matches. Existing external subtitles for matched videos will be replaced. Unmatched videos are unchanged."))
        note.setWordWrap(True)
        layout.addWidget(note)
        listing = QPlainTextEdit("\n".join(lines))
        listing.setReadOnly(True)
        layout.addWidget(listing)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        jobs = [job for job in jobs if job in self.runner.jobs and self._can_edit_job(job)]
        if not jobs:
            self.error(self.tr("No eligible videos remain for subtitle matching."))
            return
        applied = sum(job.source in matches for job in jobs)
        if self.sync_settings:
            self.sync_checkbox.setChecked(False)
        for job in jobs:
            if job.source in matches:
                mode = job.settings.subtitle_mode if job.settings.subtitle_mode != "off" else "soft"
                job.settings = replace(job.settings, subtitle_path=str(matches[job.source]), subtitle_mode=mode)
                job.settings_locked = True
                row = self.runner.jobs.index(job)
                self.queue_model.dataChanged.emit(self.queue_model.index(row, 0), self.queue_model.index(row, 3))
        if self.selected_job():
            self._load_editor_settings(self.selected_job().settings)
        self._save_queue()
        self.status_label.setText(self.tr("Subtitle matches applied") + f": {applied} / {len(matches)}")

    def selected_job(self):
        current = self.table.currentIndex()
        row = current.row() if current.isValid() else -1
        return self.runner.jobs[row] if 0 <= row < len(self.runner.jobs) else None

    def _sync_preview_settings(self):
        job = self.selected_job()
        settings = job.settings if job else self.store.value
        if self.preview_store.value != settings:
            self.preview_store.set(settings)

    def preview_selection(self, current, previous):
        del current, previous
        job = self.selected_job()
        if self.sync_settings:
            self._load_editor_settings(self._shared_settings)
        elif job:
            self._load_editor_settings(job.settings)
        else:
            self._refresh_settings_view()
        self.preview.set_source(job.source if job else None)

    def move_watermark(self, x, y):
        job = self.selected_job()
        if not self.sync_settings and not self._can_edit_job(job):
            return
        self.store.update_many(watermark_corner="free", watermark_x=x, watermark_y=y)

    def resize_watermark(self, width):
        job = self.selected_job()
        if not self.sync_settings and not self._can_edit_job(job):
            return
        self.store.update("watermark_width", width)

    def compare_sample(self):
        job = self.selected_job()
        if not job or not self.preview.media:
            self.error(self.tr("Select a video and wait for its preview to load."))
            return
        if self.runner.busy:
            self.error(self.tr("Stop the queue before processing a comparison sample."))
            return
        from .comparison import CompareDialog
        from .pipeline import validate_media
        settings = job.settings
        try:
            validate_media(replace(settings, container="mkv", copy_subtitles=False), self.preview.media)
        except ValueError as error:
            self.error(error)
            return
        dialog = CompareDialog(job.source, self.preview.media, self.preview.slider.value()/1000,
                               settings, self.tools, self.tr, SettingsStore, self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def job_settings_dialog(self):
        job = self.selected_job()
        if not self._can_edit_job(job):
            self.error(self.tr("Select a video that is not currently processing."))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Settings for selected video") + " · " + job.source.name)
        dialog.resize(760, 730)
        draft = SettingsStore(job.settings, dialog)
        layout = QVBoxLayout(dialog)
        note = QLabel(self.tr("Shared settings for all videos. A video currently processing keeps its settings.") if self.sync_settings else self.tr("These settings apply only to this video. Other videos keep their settings."))
        note.setWordWrap(True)
        layout.addWidget(note)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel(self.tr("Output name (without extension)")))
        output_name = QLineEdit(job.output_name)
        output_name.setPlaceholderText(self.tr("Automatic"))
        output_row.addWidget(output_name, 1)
        layout.addLayout(output_row)
        preset_row = QHBoxLayout()
        preset_box = QComboBox()
        populate_preset_choices(preset_box, self.presets, self.tr)
        preset_row.addWidget(preset_box, 1)
        apply_preset = QPushButton(self.tr("Apply"))
        def use_preset():
            name = preset_box.currentData()
            if name is None:
                return
            old = draft.value
            values = {"subtitle_path": old.subtitle_path, "watermark_path": old.watermark_path,
                      "output_dir": old.output_dir, "gpu": old.gpu, "hwaccel": old.hwaccel,
                      "recover_shutdown": old.recover_shutdown}
            if name in FACTORY_PRESETS:
                values.update(subtitle_mode=old.subtitle_mode, subtitle_language=old.subtitle_language, watermark_corner=old.watermark_corner,
                              watermark_x=old.watermark_x, watermark_y=old.watermark_y,
                              watermark_width=old.watermark_width, watermark_margin=old.watermark_margin,
                              watermark_opacity=old.watermark_opacity)
            draft.set(replace(self.presets[name], **values))
        apply_preset.clicked.connect(use_preset)
        preset_row.addWidget(apply_preset)
        layout.addLayout(preset_row)
        tabs = QTabWidget()
        groups = [("AI", ["engine","model","scale","gpu","noise","cugan_model","cugan_threads","cugan_syncgap","shader","rife_model","fps_multiplier","scene_threshold","rife_uhd","hwaccel","recover_shutdown","resume_segments"]),
                  ("Encoder", ["codec","preset","quality","pixel_format","bit_rate","rc_buffer_size","rc_min_rate","rc_max_rate","qmin","qmax","gop_size","max_b_frames","keyint_min","refs","thread_count","extra_options"]),
                  ("Finishing", ["target","width","height","aspect","container","audio","audio_bitrate","restoration","copy_subtitles","subtitle_mode","subtitle_path","subtitle_language","watermark_path","watermark_corner","watermark_x","watermark_y","watermark_width","watermark_margin","watermark_opacity","flicker","output_dir"])]
        for label, keys in groups:
            tabs.addTab(scroll(SettingsForm(draft, keys, self.tr)), self.tr(label))
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        inherit = buttons.addButton(self.tr("Use shared settings"), QDialogButtonBox.ButtonRole.ResetRole)
        def reset():
            job.output_name = ""
            self._load_editor_settings(self._shared_settings)
            self._settings_changed()
            job.settings_locked = False
            self._job_changed(job)
            dialog.accept()
        def accept():
            try:
                draft.value.validate()
                from .pipeline import output_path
                # Validate the requested name without reserving it or touching files.
                output_path(job.source, draft.value, set(), output_name.text())
            except (ValueError, OSError) as error:
                self.error(error)
                return
            job.output_name = output_name.text()
            self._load_editor_settings(draft.value)
            self._settings_changed()
            self._job_changed(job)
            dialog.accept()
        inherit.clicked.connect(reset)
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def retry_selected(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes and self.table.currentIndex().isValid():
            indexes = [self.table.currentIndex()]
        retried = []
        for index in indexes:
            job = self.runner.jobs[index.row()]
            if job is not self.runner.current and job.state in ("failed","cancelled","done","done_warning","interrupted"):
                job.state,job.percent,job.error,job.output = "queued",0,"",None
                retried.append(job)
                self._job_changed(job)
        selected = self.selected_job()
        if selected:
            self._load_editor_settings(selected.settings)
        self._busy_changed(self.runner.busy)
        if retried:
            message = self.tr("Selected videos queued again") + f": {len(retried)}"
            if self.runner.busy:
                message += " · " + self.tr("The running queue will pick them up automatically.")
            self.status_label.setText(message)
            self._log(message)
        else:
            self.status_label.setText(self.tr("Select a failed, cancelled, interrupted or completed video to retry."))

    def _selected_restartable_jobs(self):
        return [self.runner.jobs[index.row()] for index in self.table.selectionModel().selectedRows()
                if 0 <= index.row() < len(self.runner.jobs)
                and self._can_edit_job(self.runner.jobs[index.row()])
                and self.runner.jobs[index.row()].state != "queued"]

    def _refresh_start_controls(self, *args):
        del args
        if not hasattr(self, "start_button"):
            return
        waiting = any(job.state == "queued" for job in self.runner.jobs)
        retry = self._selected_restartable_jobs() if not waiting else []
        self.start_button.setEnabled(not self.runner.busy and (waiting or bool(retry)))
        message = "Start all waiting videos." if waiting else "No waiting videos. Start queue will reprocess the selected videos with their current settings."
        self.start_button.setToolTip(self.tr(message))

    def start(self):
        if self.runner.busy:
            return
        try:
            waiting = [job for job in self.runner.jobs if job.state == "queued"]
            jobs = waiting or self._selected_restartable_jobs()
            if not jobs:
                self.error(self.tr("Add videos or select a video to process again."))
                return
            problems = []
            for job in jobs:
                try:
                    job.settings.validate()
                    if not job.source.is_file():
                        raise ValueError(self.tr("Input file no longer exists"))
                    s = job.settings
                    if s.subtitle_mode != "off" and (not Path(s.subtitle_path).is_file() or Path(s.subtitle_path).suffix.lower() not in (".srt", ".ass", ".ssa", ".vtt")):
                        raise ValueError(self.tr("Select an SRT, ASS, SSA or VTT subtitle file"))
                    if s.watermark_path and (not Path(s.watermark_path).is_file() or Path(s.watermark_path).suffix.lower() != ".png"):
                        raise ValueError(self.tr("Select a PNG watermark"))
                    from .pipeline import output_path
                    output_path(job.source, s, set(), job.output_name)
                except (ValueError, OSError) as error:
                    problems.append(f"{job.source.name}: {error}")
            if problems:
                self.error(self.tr("Check these videos before starting:") + "\n\n" + "\n".join(problems[:30]) + (f"\n... +{len(problems) - 30}" if len(problems) > 30 else ""))
                return
            probe = self.tools.get("ffprobe", "")
            if probe and Path(probe).is_file():
                from .preflight import check_media_queue
                problems = check_media_queue(self, jobs, probe, self.tr)
                if problems is None:
                    return
                if problems:
                    self.error(self.tr("Check these videos before starting:") + "\n\n" + "\n".join(problems[:30]) + (f"\n... +{len(problems) - 30}" if len(problems) > 30 else ""))
                    return
                hdr_jobs = [job.source.name for job in jobs if job.source_hdr]
                if hdr_jobs:
                    message = self.tr("HDR input is tone-mapped to SDR (BT.709); HDR highlights and metadata are not preserved.")
                    message += "\n\n" + "\n".join(hdr_jobs[:20]) + (f"\n... +{len(hdr_jobs) - 20}" if len(hdr_jobs) > 20 else "")
                    if QMessageBox.question(self, self.tr("HDR to SDR"), message) != QMessageBox.StandardButton.Yes:
                        return
            if not waiting:
                # A deliberate Start click retries only the selected inactive jobs.
                # Merely editing settings never queues completed work by itself.
                for job in jobs:
                    job.state, job.percent, job.error = "queued", 0, ""
                    self._job_changed(job)
            self.runner.start(self.tools)
        except (ValueError,OSError) as error:
            self.error(error)
        finally:
            self._refresh_start_controls()

    def _job_changed(self, job):
        if job not in self.runner.jobs:
            return
        if self._queue_states.get(job.id) != job.state:
            self._queue_states[job.id] = job.state
            self._save_queue()
        else:
            self._queue_timer.start(600)
        if self.selected_job() is job:
            self._update_settings_scope()
        row = self.runner.jobs.index(job)
        self.queue_model.dataChanged.emit(self.queue_model.index(row,0),self.queue_model.index(row,3))
        self.status_label.setText(f"{self.tr(job.state)} · {job.source.name[:52]}")
        if job.state == "failed":
            path = config_dir() / "logs" / (job.id + ".log")
            content = read_log(path, self.logs.toPlainText())
            summary = job.source.name + "\n" + failure_summary(job.error, self.tr, content)
            if self.error_log_dialog is None:
                self.error_log_dialog = LogDialog(self.tr("Processing log"), summary, content, self.tr, path, self)
            else:
                self.error_log_dialog.set_contents(summary, content, path)
            self.error_log_dialog.show()
            self.error_log_dialog.raise_()
        unknown = job.state in ("probe","subtitle_check","help","filters","encoders","encoder_check","normalize","assemble","verify","verify_ai")
        self.progress.setRange(0,0 if unknown else 100)
        self.progress.setValue(int(job.percent))
        self._refresh_start_controls()

    def _busy_changed(self, busy):
        self._update_settings_scope()
        self._refresh_start_controls()
        self.cancel_button.setEnabled(busy)
        self.language_box.setEnabled(not busy)
        self.support_link.setVisible(not busy)
        if not busy:
            self._clock_timer.stop()
            self._queue_started_at = None
            self.timing_label.clear()
            self.progress.setRange(0,100)
            self.status_label.setText(self.tr("Ready"))
        else:
            if self._queue_started_at is None:
                self._queue_started_at = time.monotonic()
            self._clock_timer.start()
            self._refresh_timing()

    def _refresh_timing(self):
        job = self.runner.current
        if not self.runner.busy or not job or not self.runner.started_at:
            return
        now = time.monotonic()
        fmt = lambda seconds: PreviewPanel.time_label(max(0, seconds) * 1000)
        elapsed = now - self.runner.started_at
        parts = [self.tr("Elapsed") + " " + fmt(elapsed)]
        waiting = sum(j.state == "queued" for j in self.runner.jobs)
        if job.state in ("stream", "encode") and self.runner.processing_started_at and 2 <= job.percent < 99:
            duration = now - self.runner.processing_started_at
            # The legacy AI path's encode percentage is scaled across stages.
            if job.state == "stream" or job.settings.engine == "none":
                remaining = duration * (100 - job.percent) / job.percent
                parts.append(self.tr("Remaining (estimate)") + " " + fmt(remaining))
                if waiting:
                    waiting_jobs = [j for j in self.runner.jobs if j.state == "queued"]
                    full_job_time = duration * 100 / job.percent
                    if job.media_duration > 0 and all(j.media_duration > 0 for j in waiting_jobs):
                        queue_remaining = remaining + full_job_time * sum(j.media_duration for j in waiting_jobs) / job.media_duration
                    else:
                        queue_remaining = remaining + waiting * full_job_time
                    parts.append(self.tr("Queue remaining, assuming similar videos") + " ≈" + fmt(queue_remaining))
        elif job.state.startswith("resume_") and self.runner.processing_started_at and 2 <= job.percent < 80:
            duration = now - self.runner.processing_started_at
            parts.append(self.tr("AI remaining (estimate)") + " " + fmt(duration * (80 - job.percent) / job.percent))
        if self.runner.speed_fps and self.runner.speed_fps > 0:
            parts.append(f"{self.runner.speed_fps:.1f} fps")
        parts.append(self.tr("Waiting") + f" {waiting}")
        if self._queue_started_at:
            parts.append(self.tr("Queue elapsed") + " " + fmt(now - self._queue_started_at))
        self.timing_label.setText(" · ".join(parts))

    def change_language(self):
        if self._building:
            return
        self.language = self.language_box.currentData()
        self.tr = Translator(self.language)
        self.install_qt_translation()
        self.build()
        self._save()

    def install_qt_translation(self):
        app = QApplication.instance()
        if self._qt_translator is not None:
            app.removeTranslator(self._qt_translator)
            self._qt_translator.deleteLater()
            self._qt_translator = None
        if self.language == "en":
            return
        translator = QTranslator(self)
        translations = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        names = [self.language]
        if "_" in self.language:
            names.append(self.language.split("_")[0])
        # Qt ships Brazilian Portuguese common dialogs, but no European Portuguese
        # catalog. The application strings remain European Portuguese.
        if self.language == "pt_PT":
            names.append("pt_BR")
        if any(translator.load(f"qtbase_{name}", translations) for name in names):
            app.installTranslator(translator)
            self._qt_translator = translator
        else:
            translator.deleteLater()

    def advanced_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Advanced settings"))
        dialog.resize(760,730)
        draft = SettingsStore(self.store.value,dialog)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        groups = [("AI",["engine","model","scale","gpu","noise","cugan_model","cugan_threads","cugan_syncgap","shader","rife_model","fps_multiplier","scene_threshold","rife_uhd","hwaccel","recover_shutdown","resume_segments"]),
                  ("Encoder",["codec","preset","quality","pixel_format","bit_rate","rc_buffer_size","rc_min_rate","rc_max_rate","qmin","qmax","gop_size","max_b_frames","keyint_min","refs","thread_count","extra_options"]),
                  ("Finishing",["target","width","height","aspect","container","audio","audio_bitrate","restoration","copy_subtitles","subtitle_mode","subtitle_path","subtitle_language","watermark_path","watermark_corner","watermark_x","watermark_y","watermark_width","watermark_margin","watermark_opacity","flicker","output_dir"])]
        for label, keys in groups:
            tabs.addTab(scroll(SettingsForm(draft,keys,self.tr)),self.tr(label))
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        def accept():
            try:
                draft.value.validate()
                self.store.set(draft.value)
                dialog.accept()
            except ValueError as error:
                self.error(error)
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def tool_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Bundled tools"))
        dialog.resize(820,280)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        for key,value in self.tools.items():
            row = QHBoxLayout()
            edit = QLineEdit(value or self.tr("Missing from app folder"))
            edit.setReadOnly(True)
            row.addWidget(edit)
            form.addRow(self.tr("AI engine") if key == "video2x" else key,row)
        layout.addLayout(form)
        hint = QLabel(self.tr("The application uses its own bundled tools. It does not use files from other installed applications."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        check = QPushButton(self.tr("Test selected GPU encoder"))
        check_status = QLabel(self.tr("Select a GPU encoder. A selected video tests its output size; otherwise a 256 × 256 sample is used."))
        check_status.setWordWrap(True)
        layout.addWidget(check)
        layout.addWidget(check_status)
        probe = QProcess(dialog)
        probe.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        timeout = QTimer(dialog)
        timeout.setSingleShot(True)
        probe_size = {"generic": False}
        def start_probe():
            settings = self.store.value
            media = self.preview.media
            if not settings.codec.endswith(("_nvenc", "_amf")):
                check_status.setText(self.tr("Select a NVIDIA or AMD encoder first."))
                return
            selected = self.selected_job()
            if not self.tools.get("ffmpeg"):
                check_status.setText(self.tr("Could not start the media checking tool"))
                return
            if not selected or self.preview.source != selected.source:
                media = None
            probe_size["generic"] = media is None
            try:
                settings.validate()
                command = encoder_probe_command(settings, self.tools, media)
            except ValueError as error:
                check_status.setText(str(error))
                return
            check.setEnabled(False)
            check_status.setText(self.tr("Checking selected encoder…"))
            probe.start(command[0], command[1:])
            timeout.start(30000)
        def finish_probe(code, status):
            timeout.stop()
            check.setEnabled(True)
            detail = bytes(probe.readAllStandardOutput()).decode("utf-8", "replace").strip()
            if code == 0 and status == QProcess.ExitStatus.NormalExit:
                message = "Encoder test passed at 256 × 256. Larger output sizes may still fail." if probe_size["generic"] else "Encoder test passed at the selected output size. AI GPU compatibility is checked separately when processing starts."
                check_status.setText(self.tr(message))
            else:
                check_status.setText(self.tr("Encoder test failed. Check the GPU, driver, codec and output size.") + ("\n" + detail[-1500:] if detail else ""))
        probe.finished.connect(finish_probe)
        probe.errorOccurred.connect(lambda error: (check.setEnabled(True), timeout.stop(), check_status.setText(probe.errorString())) if error == QProcess.ProcessError.FailedToStart else None)
        timeout.timeout.connect(probe.kill)
        check.clicked.connect(start_probe)
        dialog.finished.connect(lambda _: probe.kill() if probe.state() != QProcess.ProcessState.NotRunning else None)
        gpu_button = QPushButton(self.tr("List Vulkan devices"))
        gpu_status = QLabel(self.tr("A listed device still needs a successful AI processing test."))
        gpu_status.setWordWrap(True)
        layout.addWidget(gpu_button)
        layout.addWidget(gpu_status)
        gpu_probe = QProcess(dialog)
        gpu_probe.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        gpu_timeout = QTimer(dialog)
        gpu_timeout.setSingleShot(True)
        def start_gpu_probe():
            engine = self.tools.get("video2x", "")
            if not engine or not Path(engine).is_file():
                gpu_status.setText(self.tr("AI engine is missing."))
                return
            gpu_button.setEnabled(False)
            gpu_status.setText(self.tr("Checking Vulkan devices…"))
            gpu_probe.setWorkingDirectory(str(Path(engine).parent))
            gpu_probe.start(engine, ["--list-devices"])
            gpu_timeout.start(30000)
        def finish_gpu_probe(code, state):
            gpu_timeout.stop()
            gpu_button.setEnabled(True)
            output = bytes(gpu_probe.readAllStandardOutput()).decode("utf-8", "replace").strip()
            prefix = self.tr("A listed device still needs a successful AI processing test.") if code == 0 and state == QProcess.ExitStatus.NormalExit else self.tr("Vulkan device check failed.")
            gpu_status.setText(prefix + ("\n" + output[-2500:] if output else ""))
        gpu_probe.finished.connect(finish_gpu_probe)
        gpu_probe.errorOccurred.connect(lambda error: (gpu_timeout.stop(), gpu_button.setEnabled(True), gpu_status.setText(gpu_probe.errorString())) if error == QProcess.ProcessError.FailedToStart else None)
        gpu_timeout.timeout.connect(gpu_probe.kill)
        gpu_button.clicked.connect(start_gpu_probe)
        dialog.finished.connect(lambda _: gpu_probe.kill() if gpu_probe.state() != QProcess.ProcessState.NotRunning else None)
        layout.addWidget(self.button("Open logs",lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir()/"logs")))))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def choose_exe(self, edit):
        path = QFileDialog.getOpenFileName(self,self.tr("Choose"),"","Applications (*.exe);;All files (*)")[0]
        if path:
            edit.setText(path)

    def setup_wizard(self):
        if self.runner.busy or self._closing:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Setup guide"))
        layout = QVBoxLayout(dialog)
        note = QLabel(self.tr("Choose a starting point. Compare a short sample before processing a full video."))
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        content = QComboBox()
        for label, value in (("Anime", "anime"), ("Live-action video", "video"), ("Resize without AI", "resize")):
            content.addItem(self.tr(label), value)
        hardware = QComboBox()
        for label, value in (("CPU encoding", "cpu"), ("NVIDIA encoding", "nvidia"), ("AMD encoding", "amd")):
            hardware.addItem(self.tr(label), value)
        content.currentIndexChanged.connect(lambda: hardware.setEnabled(content.currentData() != "resize"))
        form.addRow(self.tr("Content"), content)
        form.addRow(self.tr("Final encoder"), hardware)
        layout.addLayout(form)
        warning = QLabel(self.tr("AI profiles require a compatible Vulkan GPU even with CPU encoding. Hardware encoding requires a supported GPU and driver."))
        warning.setWordWrap(True)
        layout.addWidget(warning)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        choices = {
            ("anime", "cpu"): "Anime · 4K balanced",
            ("anime", "nvidia"): "Anime · NVIDIA HEVC",
            ("anime", "amd"): "Anime · QHD AMD HEVC",
            ("video", "cpu"): "Video · 4K general AI CPU encode",
            ("video", "nvidia"): "Video · 4K general AI NVIDIA",
            ("video", "amd"): "Video · 4K detail AMD HEVC",
            ("resize", "cpu"): "Resize only · 4K",
            ("resize", "nvidia"): "Resize only · 4K",
            ("resize", "amd"): "Resize only · 4K",
        }
        name = choices[(content.currentData(), hardware.currentData())]
        self.preset_box.setCurrentIndex(self.preset_box.findData(name))
        self.apply_preset()

    def apply_preset(self):
        name = self.preset_box.currentData()
        if name is None:
            return
        s = self.presets[name]
        # Keep machine-specific sidecars and destinations when switching a portable preset.
        old = self.store.value
        values = {"subtitle_path": old.subtitle_path, "watermark_path": old.watermark_path,
                  "output_dir": old.output_dir, "gpu": old.gpu, "hwaccel": old.hwaccel,
                  "recover_shutdown": old.recover_shutdown}
        if self.preset_box.currentData() in FACTORY_PRESETS:
            values.update(subtitle_mode=old.subtitle_mode, subtitle_language=old.subtitle_language, watermark_corner=old.watermark_corner,
                          watermark_x=old.watermark_x, watermark_y=old.watermark_y,
                          watermark_width=old.watermark_width, watermark_margin=old.watermark_margin,
                          watermark_opacity=old.watermark_opacity)
        self.store.set(replace(s, **values))

    def save_preset(self):
        name,ok = QInputDialog.getText(self,self.tr("Save preset"),self.tr("Preset name"))
        if not ok:
            return
        try:
            document = preset_document(name,self.store.value)
            name = document["name"]
            if name in FACTORY_PRESETS:
                raise ValueError(self.tr("System presets cannot be overwritten. Choose another name."))
            self.presets[name] = self.store.value
            self.refresh_presets(name)
            self.show_preset_description()
            self._save()
        except ValueError as error:
            self.error(error)

    def import_preset(self):
        path = QFileDialog.getOpenFileName(self,self.tr("Import"),"","Preset (*.json)")[0]
        if path:
            try:
                name,s = load_preset(read_json(Path(path)))
                if name in self.presets:
                    base = name
                    i=2
                    while name in self.presets:
                        name=f"{base} ({i})"
                        i+=1
                self.presets[name]=s
                self.refresh_presets(name)
                self.show_preset_description()
                self.apply_preset()
                self._save()
            except (ValueError,OSError,KeyError) as error:
                self.error(error)

    def export_preset(self):
        path = QFileDialog.getSaveFileName(self,self.tr("Export"),"anime-preset.json","Preset (*.json)")[0]
        if path:
            try:
                atomic_json(Path(path),preset_document(self.preset_box.currentData() or "Current settings",self.store.value))
            except (ValueError,OSError) as error:
                self.error(error)

    def refresh_presets(self, selected=None):
        self.preset_box.blockSignals(True)
        populate_preset_choices(self.preset_box, self.presets, self.tr)
        if selected is not None:
            index = self.preset_box.findData(selected)
            if index >= 0:
                self.preset_box.setCurrentIndex(index)
        self.preset_box.blockSignals(False)

    def show_preset_description(self):
        if not hasattr(self, "preset_description"):
            return
        name = self.preset_box.currentData()
        job = self.selected_job() if hasattr(self, "table") else None
        editable = self.sync_settings or self._can_edit_job(job) or not self.runner.jobs
        self.apply_preset_button.setEnabled(editable and name is not None)
        if name is None:
            description = self.tr("Select a preset, then choose Apply to load its settings.")
        elif name in FACTORY_PRESET_DESCRIPTIONS:
            description = self.tr(FACTORY_PRESET_DESCRIPTIONS[name]) + "  " + self.tr("Select Apply to load this preset.")
        else:
            description = self.tr("Your saved preset. Select Apply to load its settings.")
        self.preset_description.setText(description)
        self.preset_description.setToolTip(description)

    def closeEvent(self,event):
        if self.runner.busy:
            self.error(self.tr("Stop the queue before closing the application."))
            event.ignore()
            return
        self._closing = True
        self._save_timer.stop()
        self._queue_timer.stop()
        self._save()
        self._save_queue()
        self.preview.shutdown()
        if self._qt_translator is not None:
            QApplication.instance().removeTranslator(self._qt_translator)
            self._qt_translator.deleteLater()
            self._qt_translator = None
        event.accept()


def main():
    from .windows_errors import configure_windows_error_mode
    configure_windows_error_mode()
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("cumaduran")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
