"""Read SMPTE ST 2094-40 window data without collapsing per-window values.

This module reads registered ITU-T T.35 payloads, not HEVC NAL units. The
payload remains available verbatim so callers can compare every metadata bit.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re


_T35_HEADER = bytes.fromhex('b5003c000104')


class _Bits:
    def __init__(self, data: bytes):
        self.data, self.position = data, 0

    def read(self, count: int) -> int:
        if count < 0 or self.position + count > len(self.data) * 8:
            raise ValueError('Truncated HDR10+ metadata')
        value = 0
        for offset in range(self.position, self.position + count):
            value = (value << 1) | ((self.data[offset // 8] >> (7 - offset % 8)) & 1)
        self.position += count
        return value


@dataclass(frozen=True)
class WindowGeometry:
    left: int
    top: int
    right: int
    bottom: int
    center_x: int
    center_y: int
    angle: int
    inner_major: int
    outer_major: int
    outer_minor: int
    overlap: int


@dataclass(frozen=True)
class WindowStatistics:
    max_scl: tuple[int, int, int]
    average_maxrgb: int
    percentiles: tuple[tuple[int, int], ...]
    fraction_bright_pixels: int
    tone_mapping: tuple[int, int, tuple[int, ...]] | None
    saturation_weight: int | None


@dataclass(frozen=True)
class HDR10PlusPayload:
    raw: bytes
    version: int
    geometries: tuple[WindowGeometry, ...]
    target_luminance: int
    target_peak_grid: tuple[tuple[int, ...], ...] | None
    mastering_peak_grid: tuple[tuple[int, ...], ...] | None
    windows: tuple[WindowStatistics, ...]


def _grid(bits: _Bits):
    if not bits.read(1):
        return None
    rows, columns = bits.read(5), bits.read(5)
    if not (2 <= rows <= 25 and 2 <= columns <= 25):
        raise ValueError('Invalid HDR10+ peak luminance grid')
    return tuple(tuple(bits.read(4) for _ in range(columns)) for _ in range(rows))


def parse_hdr10plus_payload(data: bytes) -> HDR10PlusPayload:
    """Parse all windows, rejecting incomplete or incorrectly identified payloads."""
    if not isinstance(data, bytes) or not data.startswith(_T35_HEADER):
        raise ValueError('Not an HDR10+ registered T.35 payload')
    bits = _Bits(data[len(_T35_HEADER):])
    version, count = bits.read(8), bits.read(2)
    if version != 1:
        raise ValueError('Unsupported HDR10+ application version')
    if count not in (1, 2, 3):
        raise ValueError('Invalid HDR10+ processing window count')
    geometries = []
    for _ in range(count - 1):
        values = [bits.read(16) for _ in range(6)] + [bits.read(8)]
        values += [bits.read(16) for _ in range(3)] + [bits.read(1)]
        geometries.append(WindowGeometry(*values))
    target = bits.read(27)
    target_grid = _grid(bits)
    stats = []
    for _ in range(count):
        maximum = tuple(bits.read(17) for _ in range(3))
        average = bits.read(17)
        percentiles = tuple((bits.read(7), bits.read(17)) for _ in range(bits.read(4)))
        bright = bits.read(10)
        stats.append((maximum, average, percentiles, bright))
    master_grid = _grid(bits)
    windows = []
    for maximum, average, percentiles, bright in stats:
        tone = None
        if bits.read(1):
            x, y, anchors = bits.read(12), bits.read(12), bits.read(4)
            tone = (x, y, tuple(bits.read(10) for _ in range(anchors)))
        saturation = bits.read(6) if bits.read(1) else None
        windows.append(WindowStatistics(maximum, average, percentiles, bright, tone, saturation))
    # The payload is byte-aligned with zero bits; unexpected trailing bytes may
    # indicate an unsupported extension and must not be silently discarded.
    remaining = len(bits.data) * 8 - bits.position
    if remaining >= 8 or bits.read(remaining):
        raise ValueError('Unsupported trailing HDR10+ metadata')
    return HDR10PlusPayload(data, version, tuple(geometries), target, target_grid, master_grid, tuple(windows))


def replace_window_geometries(data: bytes, geometries: tuple[WindowGeometry, ...]) -> bytes:
    """Replace only the spatial fields and retain every other payload bit."""
    original = parse_hdr10plus_payload(data)
    if len(geometries) != len(original.geometries):
        raise ValueError('HDR10+ window geometry count changed')
    output = bytearray(data)
    position = len(_T35_HEADER) * 8 + 8 + 2
    for geometry in geometries:
        values = (geometry.left, geometry.top, geometry.right, geometry.bottom,
                  geometry.center_x, geometry.center_y, geometry.angle, geometry.inner_major,
                  geometry.outer_major, geometry.outer_minor, geometry.overlap)
        if (any(not isinstance(value, int) or not 0 <= value < (1 << width)
                for value, width in zip(values, (16, 16, 16, 16, 16, 16, 8, 16, 16, 16, 1)))):
            raise ValueError('HDR10+ window geometry value is out of range')
        if (geometry.left > geometry.right or geometry.top > geometry.bottom or geometry.angle > 180 or
                not 1 <= geometry.inner_major <= geometry.outer_major or not 1 <= geometry.outer_minor <= geometry.outer_major):
            raise ValueError('Invalid HDR10+ window geometry')
        for value, width in zip(values, (16, 16, 16, 16, 16, 16, 8, 16, 16, 16, 1)):
            for shift in range(width - 1, -1, -1):
                mask = 1 << (7 - position % 8)
                if (value >> shift) & 1:
                    output[position // 8] |= mask
                else:
                    output[position // 8] &= ~mask
                position += 1
    result = bytes(output)
    parsed = parse_hdr10plus_payload(result)
    if (parsed.geometries != geometries or parsed.windows != original.windows or
            parsed.target_luminance != original.target_luminance or
            parsed.target_peak_grid != original.target_peak_grid or parsed.mastering_peak_grid != original.mastering_peak_grid):
        raise ValueError('HDR10+ window geometry update could not be verified')
    return result


def transform_window_geometries(data: bytes, media, settings) -> bytes:
    """Map window rectangles and ellipses into the final display canvas."""
    from .pipeline import fitted_size, target_size

    source = parse_hdr10plus_payload(data)
    if not source.geometries:
        return data
    if media.display_orientation:
        if sorted(map(abs, media.display_orientation)) != [0, 0, 65536, 65536]:
            raise ValueError('HDR10+ window orientation is not supported')
        a, b, c, d = (v // 65536 for v in media.display_orientation)
        if sorted(map(abs, (a, b, c, d))) != [0, 0, 1, 1] or a * b or c * d or a * c or b * d:
            raise ValueError('HDR10+ window orientation is not supported')
    else:
        matrix = {0: (1, 0, 0, 1), 90: (0, -1, 1, 0),
                  180: (-1, 0, 0, -1), 270: (0, 1, -1, 0)}
        if media.rotation % 360 not in matrix:
            raise ValueError('HDR10+ window orientation is not supported')
        a, b, c, d = matrix[media.rotation % 360]
    coded_w, coded_h = (media.height, media.width) if b and c else (media.width, media.height)
    fw, fh = fitted_size(settings, media)
    ow, oh = target_size(settings, media) if settings.aspect == 'pad' else (fw, fh)
    px = ((ow - fw) // 2) // 2 * 2
    py = ((oh - fh) // 2) // 2 * 2
    sx, sy = fw / media.width, fh / media.height
    dx = (coded_w - 1 if a < 0 else 0) + (coded_h - 1 if c < 0 else 0)
    dy = (coded_w - 1 if b < 0 else 0) + (coded_h - 1 if d < 0 else 0)

    def point(x, y):
        return ((a*x + c*y + dx + .5) * sx - .5 + px,
                (b*x + d*y + dy + .5) * sy - .5 + py)

    result = []
    for g in source.geometries:
        if not (0 <= g.left <= g.right < coded_w and 0 <= g.top <= g.bottom < coded_h and
                0 <= g.center_x < coded_w and 0 <= g.center_y < coded_h and 0 <= g.angle <= 180 and
                1 <= g.inner_major <= g.outer_major and 1 <= g.outer_minor <= g.outer_major):
            raise ValueError('HDR10+ processing window is outside the source image')
        corners = [point(x, y) for x in (g.left, g.right) for y in (g.top, g.bottom)]
        left = max(0, round(min(x for x, _ in corners))); right = min(ow - 1, round(max(x for x, _ in corners)))
        top = max(0, round(min(y for _, y in corners))); bottom = min(oh - 1, round(max(y for _, y in corners)))
        cx, cy = point(g.center_x, g.center_y)
        radians = math.radians(g.angle)
        co, si = math.cos(radians), math.sin(radians)
        # Transform the ellipse's quadratic form and recover its principal axes.
        ux, uy = sx * (a*co + c*si), sy * (b*co + d*si)
        vx, vy = sx * (-a*si + c*co), sy * (-b*si + d*co)
        major2, minor2 = g.outer_major ** 2, g.outer_minor ** 2
        xx = major2*ux*ux + minor2*vx*vx
        yy = major2*uy*uy + minor2*vy*vy
        xy = major2*ux*uy + minor2*vx*vy
        delta = math.hypot(xx - yy, 2*xy)
        major = math.sqrt(max(0., (xx + yy + delta) / 2))
        minor = math.sqrt(max(0., (xx + yy - delta) / 2))
        angle = math.degrees(math.atan2(2*xy, xx - yy)) / 2 % 180
        outer_major, outer_minor = max(1, round(major)), max(1, round(minor))
        inner = max(1, min(outer_major, round(g.inner_major * major / g.outer_major)))
        result.append(WindowGeometry(left, top, right, bottom, round(cx), round(cy), round(angle) % 180,
                                     inner, outer_major, min(outer_major, outer_minor), g.overlap))
    return replace_window_geometries(data, tuple(result))


_START_CODE = re.compile(b'\x00\x00\x00\x01|\x00\x00\x01')


def _units(access_unit: bytes):
    starts = list(_START_CODE.finditer(access_unit))
    if not starts or any(access_unit[:starts[0].start()]):
        raise ValueError('Invalid Annex B HEVC access unit')
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(access_unit)
        nal = access_unit[match.end():end]
        if len(nal) < 2 or nal[0] & 128 or not (nal[1] & 7):
            raise ValueError('Invalid HEVC NAL unit')
        yield match.start(), nal


def _unescape(data: bytes) -> bytes:
    output = bytearray()
    zeros = 0
    index = 0
    while index < len(data):
        value = data[index]
        if zeros >= 2 and value == 3:
            if index + 1 == len(data) or data[index + 1] > 3:
                raise ValueError('Invalid HEVC emulation prevention byte')
            zeros = 0
            index += 1
            continue
        output.append(value)
        zeros = zeros + 1 if value == 0 else 0
        index += 1
    return bytes(output)


def _escape(data: bytes) -> bytes:
    output = bytearray()
    zeros = 0
    for value in data:
        if zeros >= 2 and value <= 3:
            output.append(3)
            zeros = 0
        output.append(value)
        zeros = zeros + 1 if value == 0 else 0
    return bytes(output)


def _sei_messages(rbsp: bytes):
    position = 0
    while position < len(rbsp):
        if rbsp[position:] == b'\x80' or (rbsp[position] == 0x80 and not any(rbsp[position + 1:])):
            return
        fields = []
        for _ in range(2):
            total = 0
            while position < len(rbsp) and rbsp[position] == 255:
                total += 255
                position += 1
            if position == len(rbsp):
                raise ValueError('Truncated HEVC SEI header')
            total += rbsp[position]
            position += 1
            fields.append(total)
        kind, length = fields
        if position + length > len(rbsp):
            raise ValueError('Truncated HEVC SEI payload')
        yield kind, rbsp[position:position + length]
        position += length
    raise ValueError('Missing HEVC SEI trailing bits')


def extract_hdr10plus_from_access_unit(access_unit: bytes) -> bytes | None:
    """Find one complete HDR10+ payload in an Annex B HEVC access unit."""
    found = []
    for _, nal in _units(access_unit):
        if (nal[0] >> 1) & 63 not in (39, 40):
            continue
        for kind, data in _sei_messages(_unescape(nal[2:])):
            if kind == 4 and data.startswith(_T35_HEADER):
                parse_hdr10plus_payload(data)
                found.append(data)
    if len(found) > 1:
        raise ValueError('Multiple HDR10+ messages in one HEVC access unit')
    return found[0] if found else None


def inject_hdr10plus_into_access_unit(access_unit: bytes, payload: bytes) -> bytes:
    """Prepend an HDR10+ SEI while leaving every existing NAL byte unchanged."""
    parse_hdr10plus_payload(payload)
    if extract_hdr10plus_from_access_unit(access_unit) is not None:
        raise ValueError('HEVC access unit already contains HDR10+ metadata')
    first_vcl = next(((offset, nal) for offset, nal in _units(access_unit) if (nal[0] >> 1) & 63 <= 31), None)
    if first_vcl is None:
        raise ValueError('HEVC access unit has no picture')
    offset, vcl = first_vcl
    size = len(payload)
    size_bytes = b'\xff' * (size // 255) + bytes([size % 255])
    sei = b'\x00\x00\x00\x01' + bytes([0x4e | (vcl[0] & 1), vcl[1]])
    sei += _escape(b'\x04' + size_bytes + payload + b'\x80')
    return access_unit[:offset] + sei + access_unit[offset:]
