import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIME_UPSCALER_HOME", str(tmp_path / "settings"))


@pytest.fixture(autouse=True)
def cleanup_qt_windows(isolated_config):
    """Destroy test windows before the isolated profile and Qt app are torn down."""
    yield
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent
    application = QApplication.instance()
    if application is None:
        return
    for window in application.topLevelWidgets():
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
