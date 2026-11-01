"""Asynchronous, cancellable media checks for a queue before processing."""
import json

from PySide6.QtCore import QEventLoop, QProcess, QTimer, Qt
from PySide6.QtWidgets import QProgressDialog

from .pipeline import Media, validate_media


def check_media_queue(parent, jobs, ffprobe, tr):
    """Return errors, or None when cancelled. Actual jobs are rechecked at runtime."""
    dialog = QProgressDialog(tr("Checking videos before starting..."), tr("Cancel"), 0, len(jobs), parent)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    loop = QEventLoop()
    process = QProcess(dialog)
    process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    errors = []
    state = {"index": 0, "cancelled": False, "timed_out": False, "data": bytearray(), "too_large": False}

    def cancel():
        state["cancelled"] = True
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
        else:
            loop.quit()

    def timeout():
        state["timed_out"] = True
        process.kill()

    def read():
        state["data"].extend(bytes(process.readAllStandardOutput()))
        if len(state["data"]) > 4_000_000:
            state["too_large"] = True
            process.kill()

    def next_job():
        index = state["index"]
        dialog.setValue(index)
        if state["cancelled"] or index >= len(jobs):
            loop.quit()
            return
        job = jobs[index]
        dialog.setLabelText(tr("Checking videos before starting...") + f"\n{index + 1}/{len(jobs)}: {job.source.name}")
        state.update(data=bytearray(), timed_out=False, too_large=False)
        process.start(ffprobe, ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(job.source)])
        timer.start(30_000)

    def finish(code, status):
        timer.stop()
        read()
        if state["cancelled"]:
            loop.quit()
            return
        job = jobs[state["index"]]
        try:
            if state["timed_out"]:
                raise ValueError(tr("Media check timed out"))
            if state["too_large"] or code != 0 or status != QProcess.ExitStatus.NormalExit:
                raise ValueError(tr("Could not read video information"))
            media = Media.from_probe(json.loads(state["data"].decode("utf-8")))
            validate_media(job.settings, media)
            job.media_duration = media.duration
            job.source_hdr = media.hdr
            job.media_info = media
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            errors.append(f"{job.source.name}: {error}")
        state["index"] += 1
        QTimer.singleShot(0, next_job)

    def failed(error):
        if error == QProcess.ProcessError.FailedToStart:
            timer.stop()
            errors.append(tr("Could not start the media checking tool"))
            loop.quit()

    dialog.canceled.connect(cancel)
    process.readyReadStandardOutput.connect(read)
    process.finished.connect(finish)
    process.errorOccurred.connect(failed)
    timer.timeout.connect(timeout)
    QTimer.singleShot(0, next_job)
    loop.exec()
    timer.stop()
    cancelled = state["cancelled"]
    dialog.close()
    dialog.deleteLater()
    return None if cancelled else errors
