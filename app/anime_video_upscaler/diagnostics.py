"""Visible, bounded and copyable diagnostics for failed processing jobs."""
from pathlib import Path
from .disk_policy import contains_disk_error
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QVBoxLayout)


class LogDialog(QDialog):
    def __init__(self, title, summary, contents, translate, log_path=None, parent=None):
        super().__init__(parent)
        self.tr = translate
        self.log_path = Path(log_path) if log_path else None
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(850, 560)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.summary)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(3000)
        layout.addWidget(self.text, 1)
        actions = QHBoxLayout()
        copy = QPushButton(self.tr("Copy log"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        actions.addWidget(copy)
        self.folder = QPushButton(self.tr("Open log folder"))
        self.folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path.parent))))
        actions.addWidget(self.folder)
        actions.addStretch()
        close = QPushButton(self.tr("Close"))
        close.clicked.connect(self.close)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.set_contents(summary, contents, log_path)

    def set_contents(self, summary, contents, log_path=None):
        self.log_path = Path(log_path) if log_path else None
        self.folder.setEnabled(bool(self.log_path and self.log_path.parent.is_dir()))
        self.summary.setText(summary)
        self.text.setPlainText(contents[-120_000:] or self.tr("No diagnostic output is available."))


def read_log(path, fallback=""):
    try:
        if path and Path(path).is_file():
            with Path(path).open("rb") as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 120_000))
                return stream.read().decode("utf-8", "replace")
    except OSError:
        pass
    return fallback


def failure_summary(message, translate, log_text=""):
    summary = str(message)
    lowered = (summary + "\n" + log_text).casefold()
    if any(term in lowered for term in ("invalid utf-8", "error decoding subtitles", "invalid srt subtitle", "subtitle validation failed", "unsupported subtitle encoding")):
        return translate("The subtitle could not be read. Check its encoding and timestamps, then retry the video.") + "\n\n" + summary
    if contains_disk_error(summary) or ("exited with code" in summary.casefold() and contains_disk_error(log_text)):
        return translate("Not enough free space on the output drive. Choose an output folder on a drive with more free space; temporary files are created there.") + "\n\n" + summary
    if "-1073741819" in summary or "0xC0000005" in summary or "3221225477" in summary:
        summary += "\n\n" + translate("The AI engine stopped unexpectedly. Check the log for the actual cause.")
        if "Video processed successfully" in log_text and "Total frames processed:" in log_text:
            summary += "\n\n" + translate("The engine reported completion before crashing. Verified recovery is optional and recovered results still require visual review.")
    return summary
