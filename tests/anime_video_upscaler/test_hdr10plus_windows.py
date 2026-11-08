import os
import random
from pathlib import Path
import subprocess
from dataclasses import replace

import pytest

from anime_video_upscaler.hdr10plus_windows import (extract_hdr10plus_from_access_unit,
    inject_hdr10plus_into_access_unit, parse_hdr10plus_payload, replace_window_geometries,
    transform_window_geometries, _Bits)
from anime_video_upscaler.pipeline import Media
from anime_video_upscaler.config import Settings


def _payload(count):
    values = []
    def put(value, width):
        values.extend((value >> bit) & 1 for bit in range(width - 1, -1, -1))
    put(1, 8); put(count, 2)
    for index in range(count - 1):
        for value in (10 + index, 20, 200, 100, 60, 50): put(value, 16)
        put(25, 8)
        for value in (20, 40, 30): put(value, 16)
        put(index % 2, 1)
    put(1000, 27); put(1, 1); put(2, 5); put(2, 5)
    for value in (1, 2, 3, 4): put(value, 4)
    for index in range(count):
        for value in (100 + index, 200 + index, 300 + index, 400 + index): put(value, 17)
        put(1, 4); put(99, 7); put(500 + index, 17); put(index, 10)
    put(0, 1)
    for index in range(count):
        put(1, 1); put(100 + index, 12); put(200 + index, 12); put(2, 4)
        put(10 + index, 10); put(20 + index, 10)
        put(1, 1); put(index + 1, 6)
    values.extend([0] * (-len(values) % 8))
    return bytes.fromhex('b5003c000104') + bytes(
        sum(value << (7 - bit) for bit, value in enumerate(values[i:i + 8]))
        for i in range(0, len(values), 8))


def test_bit_reader_matches_independent_bit_string_at_unaligned_offsets():
    generator = random.Random(21)
    for _ in range(200):
        data = bytes(generator.randrange(256) for _ in range(32))
        expected = ''.join(f'{value:08b}' for value in data)
        reader = _Bits(data)
        while reader.position < len(expected):
            width = min(generator.randrange(1, 29), len(expected) - reader.position)
            start = reader.position
            assert reader.read(width) == int(expected[start:start + width], 2)
        assert reader.read(0) == 0
        with pytest.raises(ValueError, match='Truncated'):
            reader.read(1)


@pytest.mark.parametrize('count', [1, 2, 3])
def test_distinct_window_metadata_is_not_collapsed(count):
    payload = _payload(count)
    parsed = parse_hdr10plus_payload(payload)
    assert parsed.raw == payload and parsed.version == 1
    assert parsed.target_peak_grid == ((1, 2), (3, 4))
    assert len(parsed.geometries) == count - 1
    for index, window in enumerate(parsed.windows):
        assert window.max_scl == (100 + index, 200 + index, 300 + index)
        assert window.percentiles == ((99, 500 + index),)
        assert window.tone_mapping == (100 + index, 200 + index, (10 + index, 20 + index))
        assert window.saturation_weight == index + 1


def test_payload_reader_rejects_truncated_or_foreign_data():
    data = _payload(3)
    for offset in (1, 10, len(data) - 1):
        with pytest.raises(ValueError):
            parse_hdr10plus_payload(data[:offset])
    with pytest.raises(ValueError, match='Not an HDR10'):
        parse_hdr10plus_payload(b'not hdr10+')


def test_updating_geometry_preserves_all_per_window_image_data():
    data = _payload(3)
    original = parse_hdr10plus_payload(data)
    updated = (replace(original.geometries[0], left=25, angle=95), original.geometries[1])
    result = parse_hdr10plus_payload(replace_window_geometries(data, updated))
    assert result.geometries == updated
    assert result.windows == original.windows
    assert result.target_peak_grid == original.target_peak_grid
    with pytest.raises(ValueError, match='count changed'):
        replace_window_geometries(data, updated[:1])
    with pytest.raises(ValueError, match='out of range'):
        replace_window_geometries(data, (replace(updated[0], left=65536), updated[1]))


@pytest.mark.parametrize('orientation,rotation,width,height,expected', [
    ((), 0, 256, 144, (15, 114, 300, 234, 90, 159, 25)),
    ((0, -65536, 65536, 0), 90, 144, 256, (114, 83, 234, 368, 159, 293, 115)),
    ((-65536, 0, 0, 65536), 0, 256, 144, (83, 114, 368, 234, 293, 159, 155)),
])
def test_window_geometry_tracks_output_orientation_and_padding(orientation, rotation, width, height, expected):
    media = Media(width, height, 1, 5, rotation=rotation, display_orientation=orientation)
    settings = replace(Settings(), target='custom', width=384, height=384, aspect='pad')
    original = parse_hdr10plus_payload(_payload(2))
    result = parse_hdr10plus_payload(transform_window_geometries(original.raw, media, settings))
    g = result.geometries[0]
    assert (g.left, g.top, g.right, g.bottom, g.center_x, g.center_y, g.angle) == expected
    assert (g.inner_major, g.outer_major, g.outer_minor) == (30, 60, 45)
    assert result.windows == original.windows


def test_nonorthogonal_window_transform_is_rejected():
    media = Media(256, 144, 1, 5, display_orientation=(65537, 0, 0, 65536))
    with pytest.raises(ValueError, match='orientation is not supported'):
        transform_window_geometries(_payload(2), media, Settings())


@pytest.mark.integration
@pytest.mark.parametrize('count', [2, 3])
def test_multwindow_payload_is_decoded_independently_by_ffmpeg(tmp_path, count):
    ffmpeg = Path(os.environ.get('ANIME_TEST_FFMPEG', ''))
    ffprobe = Path(os.environ.get('ANIME_TEST_FFPROBE', ''))
    if not ffmpeg.is_file() or not ffprobe.is_file():
        pytest.skip('Set ANIME_TEST_FFMPEG and ANIME_TEST_FFPROBE')
    stream = tmp_path / 'input.hevc'
    subprocess.run([str(ffmpeg), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=256x144:r=1:d=1',
                    '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', 'log-level=error',
                    '-f', 'hevc', str(stream)], check=True, capture_output=True)
    data = stream.read_bytes()
    original = parse_hdr10plus_payload(_payload(count))
    geometry = (replace(original.geometries[0], center_x=72, angle=90), *original.geometries[1:])
    payload = replace_window_geometries(original.raw, geometry)
    updated = inject_hdr10plus_into_access_unit(data, payload)
    assert extract_hdr10plus_from_access_unit(data) is None
    assert extract_hdr10plus_from_access_unit(updated) == payload
    with pytest.raises(ValueError, match='already contains'):
        inject_hdr10plus_into_access_unit(updated, payload)
    stream.write_bytes(updated)
    probe = subprocess.run([str(ffprobe), '-v', 'error', '-show_frames', '-show_entries', 'frame=side_data_list',
                            '-of', 'json', str(stream)], capture_output=True, text=True, check=True).stdout
    assert f'"num_windows": {count}' in probe
    assert '"center_of_ellipse_x": 72' in probe
    assert '"rotation_angle": 90' in probe
    parsed = parse_hdr10plus_payload(payload)
    for index, window in enumerate(parsed.windows):
        assert f'"average_maxrgb": "{window.average_maxrgb}/100000"' in probe
        assert f'"knee_point_x": "{window.tone_mapping[0]}/4095"' in probe
        assert f'"color_saturation_weight": "{index + 1}/8"' in probe
