import codecs

import pytest

from anime_video_upscaler.subtitles import match_subtitles, stage_subtitle


TEXT = "1\r\n00:00:00,000 --> 00:00:00,800\r\nTürkçe: ı İ ş Ş ğ Ğ ç ö ü\r\n"


@pytest.mark.parametrize("encoding", ["cp1254", "utf-8", "utf-8-sig", "utf-16", "utf-32"])
@pytest.mark.parametrize("extension", ["srt", "ass", "ssa", "vtt"])
def test_stage_preserves_text_and_original(tmp_path, encoding, extension):
    source = tmp_path / f"Türkçe altyazı.{extension}"
    original = TEXT.encode(encoding)
    source.write_bytes(original)
    destination = tmp_path / f"subtitle.{extension}"
    stage_subtitle(source, destination)
    assert destination.read_bytes() == TEXT.encode("utf-8")
    assert source.read_bytes() == original


def test_mixed_turkish_and_utf8_lines_are_staged_without_data_loss(tmp_path):
    source = tmp_path / "mixed.srt"
    original = ("1\r\n00:00:00,000 --> 00:00:00,800\r\nTürkçe: ışığ\r\n".encode("cp1254")
                + "2\r\n00:00:01,000 --> 00:00:01,800\r\nСантен Кесщин!\r\n".encode("utf-8"))
    source.write_bytes(original)
    destination = tmp_path / "subtitle.srt"
    encoding = stage_subtitle(source, destination)
    assert encoding == "mixed UTF-8 / cp1254 (line by line)"
    assert destination.read_bytes() == ("1\r\n00:00:00,000 --> 00:00:00,800\r\nTürkçe: ışığ\r\n"
                                        "2\r\n00:00:01,000 --> 00:00:01,800\r\nСантен Кесщин!\r\n").encode("utf-8")
    assert source.read_bytes() == original


@pytest.mark.parametrize("data", [b"", b"bad\x00text", b"bad\x81text", codecs.BOM_UTF16_LE + b"x",
                                  codecs.BOM_UTF8 + b"bad\xff"])
def test_invalid_subtitle_fails_before_encoding(tmp_path, data):
    source = tmp_path / "bad.srt"
    source.write_bytes(data)
    destination = tmp_path / "subtitle.srt"
    with pytest.raises(ValueError, match="subtitle"):
        stage_subtitle(source, destination)
    assert not destination.exists()


@pytest.mark.parametrize("timing", ["00:61:00,000 --> 00:62:00,000", "00:00:02,000 --> 00:00:01,000", "00:00:01,000 --> nonsense"])
def test_bad_srt_timing_fails_before_processing(tmp_path, timing):
    source = tmp_path / "bad.srt"
    source.write_text("1\n" + timing + "\nTest\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SRT subtitle"):
        stage_subtitle(source, tmp_path / "staged.srt")


def test_matching_requires_a_unique_subtitle_and_prevents_reuse(tmp_path):
    first, second, third = [tmp_path / f"ANKABL{i}.mkv" for i in (63, 64, 65)]
    one = tmp_path / "Bleach-063-ANKABL63.srt"
    one.touch()
    (tmp_path / "Bleach-064-a.srt").touch()
    (tmp_path / "Bleach-064-b.srt").touch()
    matches, unresolved = match_subtitles([first, second, third], tmp_path)
    assert matches == {first: one}
    assert set(unresolved) == {second, third}
    other = tmp_path / "other63.mkv"
    matches, unresolved = match_subtitles([first, other], tmp_path)
    assert not matches and set(unresolved) == {first, other}
