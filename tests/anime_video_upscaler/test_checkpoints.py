import json

import pytest

from anime_video_upscaler.checkpoints import find_checkpoints, remove_checkpoint


def make_checkpoint(folder):
    checkpoint = folder / (".anime-resume-" + "a" * 32 + "-" + "b" * 16)
    checkpoint.mkdir()
    (checkpoint / "manifest.json").write_text(json.dumps({"version": 3, "source": "C:/video.mkv"}), encoding="utf-8")
    (checkpoint / "part-000000.mkv").write_bytes(b"data")
    return checkpoint


def test_checkpoint_scan_and_selective_removal(tmp_path):
    checkpoint = make_checkpoint(tmp_path)
    unrelated = tmp_path / "do-not-delete.txt"
    unrelated.write_text("keep")
    found = find_checkpoints(tmp_path)
    assert len(found) == 1 and found[0].segments == 1
    assert found[0].source == "C:/video.mkv"
    remove_checkpoint(found[0])
    assert not checkpoint.exists() and unrelated.read_text() == "keep"


def test_checkpoint_removal_refuses_changed_or_unexpected_files(tmp_path):
    checkpoint = make_checkpoint(tmp_path)
    found = find_checkpoints(tmp_path)[0]
    extra = checkpoint / "important.txt"
    extra.write_text("keep")
    assert find_checkpoints(tmp_path) == []
    with pytest.raises(ValueError):
        remove_checkpoint(found)
    assert extra.read_text() == "keep"
