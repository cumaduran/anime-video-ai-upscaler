from dataclasses import replace
from pathlib import Path
import pytest

from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDropEvent

from anime_video_upscaler.config import FACTORY_PRESETS, Settings, atomic_json, config_dir, preset_document
from anime_video_upscaler.gui import DropTable, MainWindow, SettingsForm, SettingsStore
from anime_video_upscaler.i18n import Translator, available_languages
from anime_video_upscaler.helptext import HELP
from anime_video_upscaler.runner import QueueRunner


@pytest.fixture(autouse=True)
def no_real_preview_process_for_fake_gui_files(monkeypatch):
    # GUI tests create empty files only to test selection and settings. Running
    # ffprobe on them introduces unrelated asynchronous work and teardown races.
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools', lambda: {"ffmpeg":"", "ffprobe":"", "video2x":""})


def test_basic_and_advanced_share_encoder_state(app):
    store=SettingsStore()
    basic=SettingsForm(store,["codec","preset","quality"],Translator())
    advanced=SettingsForm(store,["codec","preset","quality"],Translator())
    basic.controls["codec"].setCurrentIndex(basic.controls["codec"].findData("hevc_nvenc"))
    assert store.value.preset == "p6"
    assert advanced.controls["preset"].currentData() == "p6"
    advanced.controls["quality"].setValue(18)
    assert basic.controls["quality"].value() == 18
    store.set(replace(Settings(),quality=23))
    assert advanced.controls["quality"].value() == 23
    basic.deleteLater()
    advanced.deleteLater()


def test_custom_size_controls_do_not_allow_zero(app):
    store = SettingsStore(replace(Settings(), target='custom', aspect='fit'))
    form = SettingsForm(store, ['width', 'height'], Translator())
    form.controls['height'].setValue(0)
    assert form.controls['height'].value() == 2
    assert store.value.height == 2
    form.deleteLater()


def test_native_qt_drop_preserves_unicode_spaces_and_ignores_remote(app,tmp_path):
    table=DropTable()
    received=[]
    table.files_dropped.connect(received.extend)
    names=[tmp_path/"Türkçe video 01.mkv",tmp_path/"[test] 'name'.mp4"]
    mime=QMimeData()
    mime.setUrls([*(QUrl.fromLocalFile(str(p)) for p in names),QUrl("https://example.invalid/video.mp4")])
    event=QDropEvent(QPointF(20,20),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    table.dropEvent(event)
    assert [Path(p) for p in received] == names
    assert event.isAccepted()
    table.deleteLater()


def test_large_drop_deduplicates_and_keeps_running_snapshot(app,tmp_path):
    window=MainWindow()
    paths=[]
    for index in range(320):
        path=tmp_path/f"bölüm {index}.mkv"
        path.touch()
        paths.append(str(path))
    window.add_paths(paths+paths)
    for _ in range(10):
        app.processEvents()
    assert len(window.runner.jobs) == 320
    running=window.runner.jobs[0]
    running.state="ai"
    original=running.settings
    window.store.update("quality",17)
    assert running.settings == original
    assert window.runner.jobs[1].settings.quality == 17
    assert window.start_button.isEnabled()
    window.close()
    window.deleteLater()


def test_locale_catalog_fallback(app):
    assert Translator("tr")("Start queue") == "Kuyruğu başlat"
    assert Translator("not-installed")("Start queue") == "Start queue"


def test_setup_guide_applies_chosen_preset_and_cancel_keeps_settings(app):
    from PySide6.QtWidgets import QComboBox, QDialog, QApplication
    window = MainWindow()
    before = window.store.value
    def cancel():
        QApplication.activeModalWidget().reject()
    QTimer.singleShot(0, cancel)
    window.setup_wizard()
    assert window.store.value == before
    def accept():
        dialog = QApplication.activeModalWidget()
        content, hardware = dialog.findChildren(QComboBox)
        content.setCurrentIndex(content.findData("video"))
        hardware.setCurrentIndex(hardware.findData("nvidia"))
        dialog.accept()
    QTimer.singleShot(0, accept)
    window.setup_wizard()
    assert window.store.value.engine == "realesrgan"
    assert window.store.value.model == "realesrgan-plus"
    assert window.store.value.codec == "hevc_nvenc"
    window.close()


def test_queue_order_is_saved_and_running_queue_cannot_be_reordered(app, tmp_path):
    window = MainWindow()
    paths = [tmp_path / f"{i}.mp4" for i in range(3)]
    for path in paths:
        path.touch()
    window.add_paths([str(path) for path in paths])
    window.table.selectRow(1)
    window.move_selected(-1)
    assert [job.source for job in window.runner.jobs] == [paths[1], paths[0], paths[2]]
    window.runner.busy = True
    window.move_selected(1)
    assert [job.source for job in window.runner.jobs] == [paths[1], paths[0], paths[2]]
    window.runner.busy = False
    window.close()
    reopened = MainWindow()
    assert [job.source for job in reopened.runner.jobs] == [paths[1], paths[0], paths[2]]
    reopened.close()


def test_queue_preflight_reports_all_missing_sources_before_start(app, tmp_path, monkeypatch):
    window = MainWindow()
    paths = [tmp_path / f"{i}.mp4" for i in range(2)]
    for path in paths:
        path.touch()
    window.add_paths([str(path) for path in paths])
    for path in paths:
        path.unlink()
    errors = []
    monkeypatch.setattr(window, "error", lambda message: errors.append(str(message)))
    window.start()
    assert len(errors) == 1
    assert all(path.name in errors[0] for path in paths)
    assert not window.runner.busy
    assert all(job.state == "queued" for job in window.runner.jobs)
    window.close()


def test_cancel_before_process_started_does_not_start_a_job(app,tmp_path):
    source=tmp_path/"a.mp4"
    source.touch()
    runner=QueueRunner()
    runner.enqueue([source],Settings())
    runner.busy=True
    runner.cancel()
    for _ in range(4):
        app.processEvents()
    assert not runner.busy and runner.process is None


def test_help_covers_every_setting_and_buttons_share_its_text(app):
    from dataclasses import fields
    keys = [f.name for f in fields(Settings)]
    assert set(keys) == set(HELP)
    for language in ("en", "tr"):
        form=SettingsForm(SettingsStore(), keys, Translator(language))
        assert set(form.help_buttons) == set(keys)
        assert all(form.help_buttons[k].toolTip() and form.controls[k].toolTip() for k in keys)
        assert "NVENC" in form.controls["quality"].toolTip()
        assert "0" in form.controls["quality"].toolTip()
        form.deleteLater()


def test_clicking_quality_info_opens_a_readable_explanation(app):
    from PySide6.QtWidgets import QTextBrowser
    from anime_video_upscaler.gui import HelpDialog
    form=SettingsForm(SettingsStore(),["quality"],Translator("tr"))
    form.show()
    messages=[]
    def dismiss():
        dialog=app.activeModalWidget()
        assert isinstance(dialog,HelpDialog)
        body = dialog.findChild(QTextBrowser)
        assert body.document().blockCount() >= 3
        messages.append(body.toPlainText())
        dialog.accept()
    QTimer.singleShot(50,dismiss)
    form.help_buttons["quality"].click()
    assert len(messages) == 1 and "CQ 0" in messages[0] and "CRF" in messages[0]
    form.close()


def test_preview_explains_missing_tools_and_can_retry(app, tmp_path):
    from anime_video_upscaler.preview import PreviewPanel
    panel = PreviewPanel(SettingsStore(), {"ffmpeg": "", "ffprobe": ""}, Translator("tr"))
    panel.show()
    source = tmp_path / "video.mkv"
    source.touch()
    panel.set_source(source)
    assert "Önizleme kullanılamıyor" in panel.canvas.text()
    assert "eksik" in panel.canvas.text().lower()
    assert panel.retry_button.isVisible()
    panel.retry_button.click()
    assert panel.source == source and panel.retry_button.isVisible()
    panel.shutdown()
    panel.close()


def test_factory_presets_are_valid_and_preserve_local_preferences(app):
    for settings in FACTORY_PRESETS.values():
        settings.validate()
    window=MainWindow()
    assert window.preset_box.currentData() is None
    assert not window.apply_preset_button.isEnabled()
    window.store.update("recover_shutdown", True)
    window.store.update("gpu", 2)
    window.store.update("subtitle_mode", "burn")
    window.store.update("flicker", "mild")
    window.preset_box.setCurrentIndex(window.preset_box.findData("Anime · QHD balanced CPU"))
    assert window.apply_preset_button.isEnabled()
    assert "2560" in window.preset_description.text()
    window.apply_preset()
    assert window.store.value.target == "2k"
    assert window.store.value.recover_shutdown is True
    assert window.store.value.flicker == "off"
    assert window.store.value.gpu == 2 and window.store.value.subtitle_mode == "burn"
    window.close()
    window.deleteLater()


def test_existing_custom_factory_override_is_migrated_without_losing_it(app):
    altered=replace(FACTORY_PRESETS["Anime · 4K balanced"],quality=17)
    atomic_json(config_dir()/"settings.json", {"schema":1, "language":"en", "settings":Settings().export(),
        "tools":{}, "presets":[preset_document("Anime · 4K balanced",altered)]})
    window=MainWindow()
    assert window.presets["Anime · 4K balanced"].quality == 20
    assert window.presets["Anime · 4K balanced (Custom)"].quality == 17
    window._save()
    window.close()
    reopened=MainWindow()
    assert reopened.presets["Anime · 4K balanced (Custom)"].quality == 17
    reopened.close()


def test_changing_language_preserves_queue_and_visible_log(app,tmp_path):
    window=MainWindow()
    source=tmp_path/"file.mkv"
    source.touch()
    window.add_paths([str(source)])
    window._log("visible log")
    window.language_box.setCurrentIndex(window.language_box.findData("tr"))
    assert "visible log" in window.logs.toPlainText()
    assert len(window.runner.jobs) == 1
    assert "1" in window.count_label.text()
    window.close()


def test_catalogs_cover_all_existing_languages_and_help():
    from anime_video_upscaler.gui import LABELS, VALUE_LABELS
    from anime_video_upscaler.config import FACTORY_PRESET_DESCRIPTIONS
    required={"en","tr","de","fr","ja","ko","pt_PT","zh_CN"}
    assert required <= set(dict(available_languages()))
    expected=set(LABELS.values()) | set(VALUE_LABELS.values()) | set(FACTORY_PRESETS) | set(FACTORY_PRESET_DESCRIPTIONS.values()) | {"Start queue", "Supported options", "PRESETS"}
    for language in required:
        translation=Translator(language)
        for key in HELP:
            assert translation.help(key)
        if language != "en":
            assert expected <= set(translation.messages)
        if language not in ("en","tr"):
            assert set(translation.help_messages) == set(HELP)
            assert translation("Start queue") != "Start queue"
            assert "NVENC" in translation.help("quality")
            assert "CQ" in translation.help("quality")


def test_old_profile_is_imported_without_external_tool_paths(app,tmp_path,monkeypatch):
    old=tmp_path/"old-profile.json"
    old_settings=replace(Settings(),quality=17)
    atomic_json(old,{"schema":1,"language":"tr","settings":old_settings.export(),
        "tools":{"ffmpeg":r"C:\Program Files\Shutter Encoder\app\Library\ffmpeg.exe",
                 "video2x":r"C:\Program Files\Video2X Qt6\video2x.exe"},"presets":[]})
    original=old.read_bytes()
    monkeypatch.setattr('anime_video_upscaler.gui.legacy_config_path',lambda: old)
    monkeypatch.setattr('anime_video_upscaler.gui.detect_tools',lambda: {"ffmpeg":"", "ffprobe":"", "video2x":""})
    window=MainWindow()
    assert window.store.value.quality == 17 and window.language == "tr"
    assert window.tools == {"ffmpeg":"", "ffprobe":"", "video2x":""}
    window._save()
    assert old.read_bytes() == original and (config_dir()/"settings.json").exists()
    assert "Shutter Encoder" not in (config_dir()/"settings.json").read_text(encoding="utf-8")
    window.close()


def test_three_column_layout_places_queue_preview_and_settings_in_order(app):
    from PySide6.QtWidgets import QSplitter
    window = MainWindow()
    splitter = window.centralWidget().findChild(QSplitter)
    assert splitter.count() == 3
    assert splitter.widget(0).isAncestorOf(window.table)
    assert splitter.widget(1).isAncestorOf(window.preview)
    assert splitter.widget(2).isAncestorOf(window.basics)
    window.close()


def test_selecting_queued_video_sets_preview_source(app,tmp_path):
    window=MainWindow()
    one,two=tmp_path/'one.mkv',tmp_path/'two.mkv'
    one.touch()
    two.touch()
    window.add_paths([str(one),str(two)])
    window.table.selectRow(1)
    assert window.preview.source == two
    window.close()


def test_selected_video_settings_remain_independent(app, tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QSpinBox
    window = MainWindow()
    paths = [tmp_path/'first.mkv', tmp_path/'second.mkv']
    for path in paths: path.touch()
    window.add_paths([str(path) for path in paths])
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0)
    def edit_dialog():
        dialog = app.activeModalWidget()
        dialog.findChild(QSpinBox, 'quality').setValue(17)
        fields = [field for field in dialog.findChildren(QLineEdit) if field.placeholderText() == window.tr('Automatic')]
        fields[0].setText('first-custom')
        dialog.findChild(QDialogButtonBox).accepted.emit()
    QTimer.singleShot(50, edit_dialog)
    window.job_settings_dialog()
    first, second = window.runner.jobs
    assert first.settings_locked and first.settings.quality == 17 and first.output_name == 'first-custom'
    assert window.preview_store.value.quality == 17
    window.table.selectRow(1)
    window.store.update('quality', 25)
    assert first.settings.quality == 17 and second.settings.quality == 25
    assert window.preview_store.value.quality == 25
    window.table.selectRow(0)
    assert window.preview_store.value.quality == 17
    window.close()


def test_dragging_watermark_emits_free_position(app):
    from PySide6.QtCore import QPoint, QSize
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtTest import QTest
    from anime_video_upscaler.preview import FrameCanvas
    canvas = FrameCanvas('')
    canvas.resize(400, 200)
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor('black'))
    canvas.set_frame(image)
    canvas.set_placement(400, 200, 40, 20, 0.0, 0.0)
    canvas.show()
    values=[]
    canvas.watermark_moved.connect(lambda x,y: values.append((x,y)))
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(10,10))
    QTest.mouseMove(canvas, QPoint(190,100))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(190,100))
    assert len(values) == 1 and abs(values[0][0] - 0.5) < 0.03 and abs(values[0][1] - 0.5) < 0.03
    canvas.close()


def test_watermark_corner_drag_resizes_with_aspect_ratio(app):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtTest import QTest
    from anime_video_upscaler.preview import FrameCanvas
    canvas = FrameCanvas('')
    canvas.resize(400, 200)
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor('black'))
    canvas.set_frame(image)
    canvas.set_placement(400, 200, 40, 20, 0.0, 0.0)
    canvas.show()
    widths=[]
    canvas.watermark_resized.connect(widths.append)
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(38,18))
    QTest.mouseMove(canvas, QPoint(80,40))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(80,40))
    assert widths == [80]
    canvas.close()


def test_adding_files_does_not_keep_closed_window_alive(app, tmp_path):
    import gc
    import weakref
    from PySide6.QtCore import QCoreApplication, QEvent
    source=tmp_path/'video.mkv'
    source.touch()
    window=MainWindow()
    window.add_paths([str(source)])
    ref=weakref.ref(window)
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    del window
    gc.collect()
    assert ref() is None


def test_comparison_error_opens_copyable_log_and_model_does_not_change_global(app,tmp_path):
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.pipeline import Media
    from anime_video_upscaler.runner import Job
    source=tmp_path/'video.mkv'; source.touch()
    original=Settings()
    dialog=CompareDialog(source,Media(640,480,10,24),0,original,{},Translator('tr'),SettingsStore)
    dialog.show()
    assert dialog.recovery.isChecked()
    dialog.model_choice.setCurrentIndex(dialog.model_choice.findData('general'))
    chosen=dialog.sample_settings()
    assert chosen.model == 'realesrgan-plus' and chosen.scale == 4 and chosen.recover_shutdown
    assert dialog.settings == original
    dialog.log_lines.append('diagnostic output for test')
    job=Job(source,original,state='failed',error='ai exited with code -1073741819. See the job log.')
    dialog.job=job
    dialog._job_changed(job)
    assert dialog.log_dialog is not None and dialog.log_dialog.isVisible()
    assert 'diagnostic output for test' in dialog.log_dialog.text.toPlainText()
    assert 'beklenmedik' in dialog.log_dialog.summary.text().lower()
    dialog.log_dialog.close()
    dialog.reject()


def test_queue_failure_automatically_opens_its_diagnostics(app,tmp_path):
    source=tmp_path/'clip.mkv';source.touch()
    window=MainWindow()
    window.show()
    window.add_paths([str(source)])
    job=window.runner.jobs[0]
    window._log('example processing detail')
    job.state='failed';job.error='example failure'
    window._job_changed(job)
    assert window.error_log_dialog is not None and window.error_log_dialog.isVisible()
    assert 'example processing detail' in window.error_log_dialog.text.toPlainText()
    assert 'example failure' in window.error_log_dialog.summary.text()
    window.error_log_dialog.close()
    window.close()


def test_system_presets_are_grouped_and_gpu_fields_stay_in_sync(app):
    from anime_video_upscaler.gui import PRESET_GROUPS
    window=MainWindow()
    for title,_,_ in PRESET_GROUPS:
        assert any(title in window.preset_box.itemText(i) for i in range(window.preset_box.count()))
    name='Anime · 4K detail AMD HEVC'
    window.preset_box.setCurrentIndex(window.preset_box.findData(name))
    window.apply_preset()
    assert window.store.value.codec=='hevc_amf' and window.store.value.preset=='quality'
    assert window.basics.controls['codec'].currentData()=='hevc_amf'
    assert window.basics.controls['preset'].currentData()=='quality'
    window.basics.controls['codec'].setCurrentIndex(window.basics.controls['codec'].findData('hevc_nvenc'))
    assert window.store.value.preset=='p6'
    window.close()


def test_av1_quality_range_and_switching_codecs_remain_valid(app):
    store=SettingsStore()
    form=SettingsForm(store,['codec','preset','quality'],Translator())
    form.controls['codec'].setCurrentIndex(form.controls['codec'].findData('av1_amf'))
    assert form.controls['quality'].maximum()==255
    form.controls['quality'].setValue(180)
    assert store.value.quality==180
    form.controls['codec'].setCurrentIndex(form.controls['codec'].findData('hevc_nvenc'))
    assert store.value.quality==51 and form.controls['quality'].maximum()==51
    store.value.validate()
    form.deleteLater()


def test_comparison_frame_step_keeps_views_synchronized(app, tmp_path):
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.pipeline import Media
    source = tmp_path / 'sample.mkv'; source.touch()
    dialog = CompareDialog(source, Media(640,480,10,25), 0, Settings(), {}, Translator('tr'), SettingsStore)
    dialog.before.slider.setRange(0, 1000)
    dialog.after.slider.setRange(0, 1000)
    dialog.before.slider.setEnabled(True)
    dialog.next_frame_button.click()
    assert dialog.before.slider.value() == dialog.after.slider.value() == 40
    dialog.next_frame_button.click()
    assert dialog.before.slider.value() == dialog.after.slider.value() == 80
    dialog.previous_frame_button.click()
    assert dialog.before.slider.value() == dialog.after.slider.value() == 40
    dialog.reject()


def test_comparison_can_change_encoder_and_quality_without_changing_job_settings(app, tmp_path):
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.pipeline import Media
    source = tmp_path / 'sample.mkv'; source.touch()
    settings = replace(Settings(), quality=19, resume_segments=True)
    dialog = CompareDialog(source, Media(640,480,10,25), 0, settings, {}, Translator(), SettingsStore)
    dialog.encoder_choice.setCurrentIndex(dialog.encoder_choice.findData('hevc_nvenc'))
    dialog.sample_quality.setValue(24)
    draft = dialog.sample_settings()
    assert (draft.codec, draft.preset, draft.quality) == ('hevc_nvenc', 'p6', 24)
    assert not draft.resume_segments and settings.quality == 19 and settings.codec == 'libx264'
    dialog.reject()


def test_sync_checkbox_defaults_on_and_updates_all_waiting_jobs(app, tmp_path):
    window=MainWindow()
    paths=[tmp_path/'first.mkv',tmp_path/'second.mkv',tmp_path/'running.mkv']
    for path in paths: path.touch()
    window.add_paths([str(path) for path in paths])
    first,second,running=window.runner.jobs
    assert window.sync_checkbox.isChecked()
    running.state='ai'
    original=running.settings
    window.basics.controls['quality'].setValue(17)
    assert first.settings.quality == second.settings.quality == 17
    assert running.settings == original
    assert window.preview_store.value == first.settings
    window.close()


def test_unsynced_selected_video_updates_main_advanced_and_watermark_only(app,tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox, QSpinBox
    window=MainWindow()
    paths=[tmp_path/'first.mkv',tmp_path/'second.mkv']
    for path in paths: path.touch()
    window.add_paths([str(path) for path in paths])
    first,second=window.runner.jobs
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0)
    window.basics.controls['quality'].setValue(17)
    window.move_watermark(0.2,0.3)
    assert first.settings.quality == 17 and first.settings.watermark_x == 0.2
    assert second.settings.quality == 20 and second.settings.watermark_x == 0.5
    window.table.selectRow(1)
    assert window.basics.controls['quality'].value() == 20
    assert window.preview_store.value == second.settings
    def edit_advanced():
        dialog=app.activeModalWidget()
        dialog.findChild(QSpinBox,'quality').setValue(24)
        dialog.findChild(QDialogButtonBox).accepted.emit()
    QTimer.singleShot(50,edit_advanced)
    window.advanced_dialog()
    assert second.settings.quality == 24 and first.settings.quality == 17
    window.table.selectRow(0)
    assert window.basics.controls['quality'].value() == 17
    assert window.preview_store.value == first.settings
    window.close()


def test_resync_immediately_applies_shared_settings_and_preserves_running_snapshot(app,tmp_path):
    window=MainWindow()
    paths=[tmp_path/'first.mkv',tmp_path/'second.mkv',tmp_path/'running.mkv']
    for path in paths: path.touch()
    window.add_paths([str(path) for path in paths])
    first,second,running=window.runner.jobs
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0)
    window.store.update('quality',17)
    window.table.selectRow(1)
    window.store.update('quality',24)
    running.state='ai'; original=running.settings
    window.sync_checkbox.setChecked(True)
    assert (first.settings.quality,second.settings.quality)==(24,24)
    assert not first.settings_locked and not second.settings_locked
    extra=tmp_path/'added.mkv'; extra.touch()
    window.add_paths([str(extra)])
    assert window.runner.jobs[-1].settings.quality==24
    assert window.selected_job() is second
    window.basics.controls['quality'].setValue(22)
    assert (first.settings.quality,second.settings.quality)==(22,22)
    assert running.settings==original
    window.close()


def test_unsynced_selection_survives_restart_and_language_change(app,tmp_path):
    window=MainWindow()
    paths=[tmp_path/'first.mkv',tmp_path/'second.mkv']
    for path in paths: path.touch()
    window.add_paths([str(path) for path in paths])
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0); window.store.update('quality',17)
    window.table.selectRow(1); window.store.update('quality',24)
    window.language_box.setCurrentIndex(window.language_box.findData('tr'))
    assert not window.sync_checkbox.isChecked()
    assert [job.settings.quality for job in window.runner.jobs]==[17,24]
    assert window.selected_job().source==paths[1]
    assert window.basics.controls['quality'].value()==24
    window.close()
    reopened=MainWindow()
    assert not reopened.sync_checkbox.isChecked()
    assert [job.settings.quality for job in reopened.runner.jobs]==[17,24]
    reopened.table.selectRow(1)
    assert reopened.basics.controls['quality'].value()==24
    reopened.close()


def test_unsynced_finished_video_is_editable_and_retry_keeps_its_settings(app,tmp_path):
    window=MainWindow()
    path=tmp_path/'one.mkv'; path.touch()
    window.add_paths([str(path)])
    window.sync_checkbox.setChecked(False)
    job=window.runner.jobs[0]
    window.store.update('quality',17)
    job.state='done'
    window._job_changed(job)
    assert window.settings_tabs.isEnabled()
    assert window.advanced_button.isEnabled()
    window.basics.controls['quality'].setValue(23)
    assert job.state=='done' and job.settings.quality==23
    window.retry_selected()
    assert job.state=='queued' and job.settings.quality==23
    assert window.settings_tabs.isEnabled()
    assert window.basics.controls['quality'].value()==23
    window.close()


def test_unsynced_bulk_drop_preserves_selected_video_and_initial_settings(app,tmp_path):
    window=MainWindow()
    first,second=tmp_path/'first.mkv',tmp_path/'second.mkv'
    first.touch(); second.touch()
    window.add_paths([str(first),str(second)])
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0); window.store.update('quality',17)
    window.table.selectRow(1); window.store.update('quality',24)
    paths=[]
    for index in range(120):
        path=tmp_path/f'new-{index}.mkv'; path.touch(); paths.append(str(path))
    window.add_paths(paths)
    assert window.selected_job().source==second
    window.table.selectRow(0)
    for _ in range(5): app.processEvents()
    assert len(window.runner.jobs)==122
    assert all(job.settings.quality==24 for job in window.runner.jobs[2:])
    assert [job.settings.quality for job in window.runner.jobs[:2]]==[17,24]
    assert window.selected_job().source==first
    window.close()


def test_unsynced_preset_and_sidecars_are_specific_to_selected_video(app,tmp_path):
    window=MainWindow()
    paths=[tmp_path/'first.mkv',tmp_path/'second.mkv']
    for path in paths: path.touch()
    window.add_paths([str(path) for path in paths])
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0)
    window.store.update_many(subtitle_mode='burn',subtitle_path=str(tmp_path/'first.srt'),
                             watermark_path=str(tmp_path/'first.png'),output_dir=str(tmp_path/'first-output'))
    first,second=window.runner.jobs
    original=first.settings
    window.table.selectRow(1)
    assert second.settings.subtitle_mode=='off' and second.settings.watermark_path==''
    name='Anime · 480p to 4K detail NVIDIA'
    window.preset_box.setCurrentIndex(window.preset_box.findData(name))
    window.apply_preset()
    assert second.settings.scale==4 and second.settings.codec=='hevc_nvenc'
    assert first.settings==original and first.settings.subtitle_mode=='burn'
    window.table.selectRow(0)
    assert window.store.value==original and window.preview_store.value==original
    window.close()


def test_sync_guidance_is_available_in_all_supported_languages():
    keys=('Sync settings to waiting videos',
          'When checked, future setting changes apply to all waiting videos. When unchecked, changes apply only to the selected waiting video. Running jobs are never changed.',
          'Future changes apply to all waiting videos. Running videos keep their settings.',
          'Editing only: {name}',
          'Select a waiting video, or retry this video, to edit its settings.',
          'No video selected. Settings become defaults for new videos.',
          'Select a waiting video to edit its settings.')
    for language,_ in available_languages():
        translation=Translator(language)
        if language!='en': assert all(key in translation.messages for key in keys)
        assert 'sample.mkv' in translation('Editing only: {name}').format(name='sample.mkv')


@pytest.mark.parametrize('state',['queued','done','done_warning','failed','cancelled','interrupted'])
def test_sync_off_each_inactive_video_has_editable_independent_settings(app,tmp_path,state):
    window=MainWindow()
    paths=[tmp_path/'one.mkv',tmp_path/'two.mkv']
    for path in paths:path.touch()
    window.add_paths([str(path) for path in paths])
    first,second=window.runner.jobs
    first.state=state
    window.sync_checkbox.setChecked(False)
    window.table.selectRow(0)
    window._update_settings_scope()
    assert window.settings_tabs.isEnabled() and window.advanced_button.isEnabled()
    window.basics.controls['quality'].setValue(17)
    window.table.selectRow(1)
    window.basics.controls['quality'].setValue(25)
    assert first.settings.quality==17 and second.settings.quality==25
    assert first.state==state
    window.table.selectRow(0)
    assert window.basics.controls['quality'].value()==17
    window.sync_checkbox.setChecked(True)
    assert first.settings.quality==second.settings.quality==17
    window.table.selectRow(1)
    window.basics.controls['quality'].setValue(22)
    assert first.settings.quality==second.settings.quality==22
    window.close()


def test_unsynced_active_video_locked_then_reenabled_when_runner_becomes_idle(app,tmp_path):
    window=MainWindow();path=tmp_path/'active.mkv';path.touch()
    window.add_paths([str(path)]);window.sync_checkbox.setChecked(False)
    job=window.runner.jobs[0];original=job.settings
    window.runner.current=job;job.state='stream'
    window._update_settings_scope()
    assert not window.settings_tabs.isEnabled()
    window.move_watermark(.2,.3)
    assert job.settings==original
    job.state='done'
    window.runner.current=None
    window._busy_changed(False)
    assert window.settings_tabs.isEnabled()
    window.basics.controls['quality'].setValue(19)
    assert job.settings.quality==19 and job.state=='done'
    window.close()


def test_video_dialog_follows_checked_sync_scope_and_keeps_output_name_specific(app,tmp_path):
    from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QSpinBox
    window=MainWindow()
    paths=[tmp_path/'one.mkv',tmp_path/'two.mkv']
    for path in paths:path.touch()
    window.add_paths([str(path) for path in paths])
    window.runner.jobs[0].state='done'
    window.table.selectRow(0)
    def edit():
        dialog=app.activeModalWidget()
        dialog.findChild(QSpinBox,'quality').setValue(18)
        names=[field for field in dialog.findChildren(QLineEdit) if field.placeholderText()==window.tr('Automatic')]
        names[0].setText('only-first')
        dialog.findChild(QDialogButtonBox).accepted.emit()
    QTimer.singleShot(50,edit)
    window.job_settings_dialog()
    first,second=window.runner.jobs
    assert first.settings.quality==second.settings.quality==18
    assert first.output_name=='only-first' and second.output_name==''
    assert first.state=='done' and second.state=='queued'
    window.close()


@pytest.mark.parametrize('state',['done','done_warning','failed','cancelled','interrupted'])
def test_start_requeues_only_selected_inactive_videos_with_their_current_settings(app,tmp_path,monkeypatch,state):
    window=MainWindow()
    paths=[tmp_path/'one.mkv',tmp_path/'two.mkv']
    for path in paths:path.touch()
    window.add_paths([str(path) for path in paths])
    window.sync_checkbox.setChecked(False)
    first,second=window.runner.jobs
    first.state=second.state=state
    first.percent=second.percent=100
    window.table.selectRow(0)
    window.basics.controls['quality'].setValue(18)
    window._busy_changed(False)
    assert window.start_button.isEnabled()
    starts=[]
    monkeypatch.setattr(window.runner,'start',lambda tools:starts.append([job for job in window.runner.jobs if job.state=='queued']))
    window.start_button.click()
    assert starts==[[first]]
    assert first.state=='queued' and first.percent==0 and first.settings.quality==18
    assert second.state==state and second.percent==100
    window.close()


def test_start_existing_queue_does_not_restart_selected_completed_video(app,tmp_path,monkeypatch):
    window=MainWindow()
    paths=[tmp_path/'one.mkv',tmp_path/'two.mkv']
    for path in paths:path.touch()
    window.add_paths([str(path) for path in paths])
    first,second=window.runner.jobs
    first.state='done'
    window.table.selectRow(0)
    starts=[]
    monkeypatch.setattr(window.runner,'start',lambda tools:starts.append(True))
    window.start_button.click()
    assert starts==[True] and first.state=='done' and second.state=='queued'
    window.runner.busy=True
    window._busy_changed(True)
    assert not window.start_button.isEnabled()
    window.runner.busy=False
    window.close()


def test_start_refreshes_on_selection_and_stays_disabled_for_empty_queue(app,tmp_path):
    window=MainWindow()
    assert not window.start_button.isEnabled()
    source=tmp_path/'one.mkv';source.touch();window.add_paths([str(source)])
    window.runner.jobs[0].state='done'
    window.table.clearSelection()
    window._refresh_start_controls()
    assert not window.start_button.isEnabled()
    window.table.selectRow(0)
    assert window.start_button.isEnabled()
    window.close()


def test_subtitle_matching_skips_job_started_while_confirming(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QDialog
    video = tmp_path / "episode.mkv"
    video.touch()
    (tmp_path / "episode.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    window = MainWindow()
    window.add_paths([str(video)])
    job = window.runner.jobs[0]
    old = job.settings
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(tmp_path))
    monkeypatch.setattr(window, "error", lambda *args: None)
    def confirm(dialog):
        window.runner.current = job
        job.state = "stream"
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, "exec", confirm)
    window.match_subtitles_dialog()
    assert job.settings == old
    window.runner.current = None
    job.state = "queued"
    window.close()


def test_compare_window_destroyed_after_close(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from anime_video_upscaler.comparison import CompareDialog
    from anime_video_upscaler.pipeline import Media
    video = tmp_path / "episode.mkv"
    video.touch()
    window = MainWindow()
    window.add_paths([str(video)])
    window.table.selectRow(0)
    window.preview.media = Media(96, 64, 1, 12)
    monkeypatch.setattr(CompareDialog, "exec", lambda dialog: dialog.reject())
    window.compare_sample()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not window.findChildren(CompareDialog)
    window.close()
