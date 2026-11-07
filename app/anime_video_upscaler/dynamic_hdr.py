"""Transfer frame-aligned HEVC dynamic HDR metadata without modifying source files.

The external tools parse and serialize HDR metadata; this module checks the
frame mapping, keeps media timestamps, and verifies the remux before publication.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from PySide6.QtCore import QThread, Signal

from .pipeline import fitted_size, target_size


@dataclass(frozen=True)
class DynamicMetadata:
    hdr10plus: Path | None = None
    dovi: Path | None = None
    source_frames: int = 0
    converted_profile7: bool = False
    timecodes: Path | None = None
    converted_profile5: bool = False
    fel_bl: Path | None = None
    fel_el: Path | None = None
    hdr10plus_raw: bool = False

    @property
    def present(self):
        return bool(self.hdr10plus or self.dovi or self.timecodes)


def metadata_tools(ffmpeg: str) -> dict[str, Path]:
    root = Path(ffmpeg).resolve().parent.parent / 'hdr-metadata'
    return {'hdr10plus': root / 'hdr10plus_tool.exe', 'dovi': root / 'dovi_tool.exe'}


def duplicate_hdr10plus(document: dict, factor: int) -> dict:
    """Use the preceding source frame's metadata for each interpolated frame."""
    if factor < 1 or not isinstance(document.get('SceneInfo'), list) or not document['SceneInfo']:
        raise ValueError('Invalid HDR10+ frame metadata')
    result = deepcopy(document)
    entries, first, lengths = [], [], []
    current_scene = object()
    for original in document['SceneInfo']:
        scene = original.get('SceneId')
        if scene != current_scene:
            first.append(len(entries)); lengths.append(0); current_scene = scene
        for _ in range(factor):
            item = deepcopy(original)
            item['SequenceFrameIndex'] = len(entries)
            item['SceneFrameIndex'] = lengths[-1]
            entries.append(item); lengths[-1] += 1
    result['SceneInfo'] = entries
    result['SceneInfoSummary'] = {'SceneFirstFrameIndex': first, 'SceneFrameNumbers': lengths}
    return result


def _hdr10_signatures(document):
    entries = document.get('SceneInfo')
    if not isinstance(entries, list) or not entries:
        raise ValueError('HDR10+ metadata has no frame entries')
    # Frame indices can legitimately change during interpolation. Every other
    # field describes the image and must survive the metadata round trip.
    indices = {'SceneId', 'SceneFrameIndex', 'SequenceFrameIndex'}
    return [{key: value for key, value in item.items() if key not in indices}
            for item in entries]


def _canonical_dovi(value):
    if isinstance(value, dict):
        return {key: _canonical_dovi(item) for key, item in value.items() if key != 'rpu_data_crc32'}
    if isinstance(value, list):
        items = [_canonical_dovi(item) for item in value]
        if items and all(isinstance(item, dict) and any(key.startswith('Level') for key in item) for item in items):
            items.sort(key=lambda item: json.dumps(item, sort_keys=True, separators=(',', ':')))
        return items
    return value


def _validate_dovi_display_matrix(stream):
    for side in stream.get('side_data_list', []):
        if side.get('side_data_type') != 'Display Matrix':
            continue
        rows = re.findall(r'^\s*[0-9a-fA-F]+:\s+(-?\d+)\s+(-?\d+)\s+(-?\d+)',
                          side.get('displaymatrix', ''), re.M)
        if len(rows) != 3:
            raise ValueError('Dolby Vision display transform cannot be verified')
        matrix = [[int(value) for value in row] for row in rows]
        a, b = matrix[0][:2]; c, d = matrix[1][:2]
        if (a * d - b * c == 0 or sorted(map(abs, (a, b, c, d))) != [0, 0, 65536, 65536] or
                a * b or c * d or a * c or b * d or matrix[0][2] or matrix[1][2] or
                matrix[2][2] != 1073741824):
            raise ValueError('Dolby Vision non-orthogonal display transforms are not supported')


def adjust_dovi_active_area(document, media, settings):
    """Scale source active-area offsets and add any final encoder padding."""
    result = deepcopy(document)
    active = result.get('active_area')
    if not isinstance(active, dict) or not isinstance(active.get('presets'), list):
        raise ValueError('Cannot read Dolby Vision active-area metadata')
    fw, fh = fitted_size(settings, media)
    ow, oh = target_size(settings, media) if settings.aspect == 'pad' else (fw, fh)
    left_pad = ((ow - fw) // 2) // 2 * 2
    top_pad = ((oh - fh) // 2) // 2 * 2
    pads = {'left': left_pad, 'right': ow - fw - left_pad,
            'top': top_pad, 'bottom': oh - fh - top_pad}
    changed = False
    for preset in active['presets']:
        # Level 5 describes the coded frame before FFmpeg autorotates it.
        # Rotate the edges with the image before scaling into display space.
        original = {key: int(preset.get(key, 0)) for key in ('left', 'right', 'top', 'bottom')}
        rotation = media.rotation % 360
        if media.display_orientation:
            a, b, c, d = media.display_orientation
            if a and d:
                oriented = {'left': original['left' if a > 0 else 'right'],
                            'right': original['right' if a > 0 else 'left'],
                            'top': original['top' if d > 0 else 'bottom'],
                            'bottom': original['bottom' if d > 0 else 'top']}
            elif b and c:
                oriented = {'left': original['top' if c > 0 else 'bottom'],
                            'right': original['bottom' if c > 0 else 'top'],
                            'top': original['left' if b > 0 else 'right'],
                            'bottom': original['right' if b > 0 else 'left']}
            else:
                raise ValueError('Dolby Vision non-orthogonal display transforms are not supported')
        elif rotation == 90:
            oriented = {'left': original['top'], 'right': original['bottom'],
                        'top': original['right'], 'bottom': original['left']}
        elif rotation == 180:
            oriented = {'left': original['right'], 'right': original['left'],
                        'top': original['bottom'], 'bottom': original['top']}
        elif rotation == 270:
            oriented = {'left': original['bottom'], 'right': original['top'],
                        'top': original['left'], 'bottom': original['right']}
        else:
            oriented = original
        for key in ('left', 'right', 'top', 'bottom'):
            scale = fw / media.width if key in ('left', 'right') else fh / media.height
            value = int(round(oriented[key] * scale / 2)) * 2 + pads[key]
            if not 0 <= value <= 8191:
                raise ValueError('Dolby Vision active-area offset exceeds the supported range')
            changed = changed or value != original[key]
            preset[key] = value
        if preset['left'] + preset['right'] >= ow or preset['top'] + preset['bottom'] >= oh:
            raise ValueError('Dolby Vision active-area metadata is outside the output image')
    if changed:
        active['crop'] = False
    return result, changed


class DynamicHDRWorker(QThread):
    log = Signal(str)

    def __init__(self, phase, tools, source, work, media, settings, metadata=None, partial=None, parent=None):
        super().__init__(parent)
        self.phase, self.tools, self.source, self.work = phase, tools, Path(source), Path(work)
        self.media, self.settings, self.metadata, self.partial = media, settings, metadata, partial
        self.error = ''
        self.result = None
        self.processes = []

    def stop(self, reason=''):
        if reason:
            self.error = reason
        self.requestInterruption()
        for process in self.processes:
            if process.poll() is None:
                process.kill()

    def _call(self, args, *, allow_missing=None, capture_all=False):
        if self.isInterruptionRequested():
            raise RuntimeError('Processing cancelled')
        self.log.emit(subprocess.list2cmdline([str(arg) for arg in args]))
        with tempfile.TemporaryFile() as capture:
            proc = subprocess.Popen([str(arg) for arg in args], cwd=self.work, stdout=capture, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes.append(proc)
            code = proc.wait()
            capture.seek(0, os.SEEK_END); size = capture.tell()
            if capture_all and size > 4_000_000:
                raise ValueError('Unexpectedly large metadata probe response')
            capture.seek(0 if capture_all else max(0, size - 16000))
            output = capture.read().decode('utf-8', 'replace')
        if self.isInterruptionRequested():
            raise RuntimeError('Processing cancelled')
        if code and allow_missing and allow_missing.lower() in output.lower():
            return None
        if code or 'Warning: mismatched lengths' in output:
            raise RuntimeError('HDR metadata tool failed: ' + output[-3000:])
        return output

    def _probe(self, path, *options):
        out = self._call([self.tools['ffprobe'], '-v', 'error', *options, '-of', 'json', path], capture_all=True)
        return json.loads(out)

    def _stream_extract(self, executable, action, output, missing):
        """Pipe source packets to the parser instead of storing another video."""
        bitstream = 'dovi_rpu=compression=none,hevc_mp4toannexb' if action == 'dovi' else 'hevc_mp4toannexb'
        producer_cmd = [self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-i', self.source,
                        '-map', f'0:{self.media.video_index}', '-c:v', 'copy', '-bsf:v', bitstream, '-f', 'hevc', '-']
        consumer_cmd = ([executable, 'extract', '-', '-o', output] if action == 'hdr10plus' else
                        [executable, 'extract-rpu', '-', '-o', output])
        self.log.emit(subprocess.list2cmdline([str(arg) for arg in producer_cmd]) + ' | ' + subprocess.list2cmdline([str(arg) for arg in consumer_cmd]))
        with tempfile.TemporaryFile() as producer_log, tempfile.TemporaryFile() as consumer_log:
            producer = subprocess.Popen([str(arg) for arg in producer_cmd], cwd=self.work, stdout=subprocess.PIPE, stderr=producer_log,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes.append(producer)
            consumer = subprocess.Popen([str(arg) for arg in consumer_cmd], cwd=self.work, stdin=producer.stdout,
                                        stdout=consumer_log, stderr=subprocess.STDOUT,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes.append(consumer); producer.stdout.close()
            consumer_code = consumer.wait(); producer_code = producer.wait()
            consumer_log.seek(0, os.SEEK_END); size = consumer_log.tell(); consumer_log.seek(max(0, size - 16000))
            output_text = consumer_log.read().decode('utf-8', 'replace')
        if self.isInterruptionRequested():
            raise RuntimeError('Processing cancelled')
        if action == 'hdr10plus' and re.search(r'invalid num_windows|invalid number.of.windows', output_text, re.I):
            raise ValueError('HDR10+ metadata with multiple processing windows is not supported by the bundled metadata tool')
        if consumer_code and action == 'dovi' and 'invalid pps index' in output_text.lower():
            # Some ordinary HEVC streams trigger this parser error even without
            # Dolby Vision. Only treat them as metadata-free after inspecting
            # every NAL unit for type 62, which carries the Dolby Vision RPU.
            if not self._has_dovi_rpu_nal():
                return False
        if consumer_code and missing.lower() in output_text.lower() and producer_code == 0:
            return False
        if consumer_code and missing.lower() in output_text.lower() and producer_code:
            # Some parsers close stdin as soon as they conclude that metadata
            # is absent, leaving FFmpeg with a broken pipe. Retry from a fully
            # written stream so a genuine read error is never mistaken for that.
            temporary = self.work / ('dynamic-source-' + action + '.hevc')
            self._call(producer_cmd[:-1] + [temporary])
            try:
                args = ([executable, 'extract', '-i', temporary, '-o', output] if action == 'hdr10plus' else
                        [executable, 'extract-rpu', '-i', temporary, '-o', output])
                return self._call(args, allow_missing=missing) is not None
            finally:
                temporary.unlink(missing_ok=True)
        if consumer_code or producer_code:
            raise RuntimeError('Could not extract dynamic HDR metadata: ' + output_text[-3000:])
        return True

    def _has_dovi_rpu_nal(self):
        command = [self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-i', self.source,
                   '-map', f'0:{self.media.video_index}', '-c:v', 'copy', '-bsf:v', 'hevc_mp4toannexb', '-f', 'hevc', '-']
        self.log.emit(subprocess.list2cmdline([str(arg) for arg in command]))
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen([str(arg) for arg in command], cwd=self.work, stdout=subprocess.PIPE, stderr=errors,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes.append(process)
            tail = b''
            found = False
            while True:
                block = process.stdout.read(1024 * 1024)
                if not block:
                    break
                data = tail + block
                found = found or bool(re.search(b'\x00\x00\x01[\x7c\x7d]', data))
                tail = data[-4:]
            process.stdout.close()
            code = process.wait()
            errors.seek(0, os.SEEK_END); size = errors.tell(); errors.seek(max(0, size - 3000))
            message = errors.read().decode('utf-8', 'replace')
        if self.isInterruptionRequested():
            raise RuntimeError('Processing cancelled')
        if code:
            raise RuntimeError('Could not inspect HEVC metadata: ' + message)
        return found

    def _frame_count(self, path, selector='v:0'):
        probe = self._probe(path, '-select_streams', selector, '-count_frames', '-show_entries', 'stream=nb_read_frames')
        try:
            count = int(probe['streams'][0]['nb_read_frames'])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError('Cannot count video frames for HDR metadata alignment') from error
        if count < 1:
            raise ValueError('Cannot count video frames for HDR metadata alignment')
        return count

    def _packet_ranges(self, path, name):
        csv = self.work / (name + '-packets.csv')
        self._call([self.tools['ffprobe'], '-v', 'error', '-select_streams', 'v:0',
                    '-show_entries', 'packet=size,pos', '-of', 'csv=p=0', '-o', csv, path])
        result, offset = [], 0
        for line in csv.read_text(encoding='utf-8').splitlines():
            fields = line.split(',')
            if len(fields) < 2 or not fields[0].isdigit() or not fields[1].isdigit():
                raise ValueError('Cannot verify HEVC packet boundaries for HDR10+')
            size, position = int(fields[0]), int(fields[1])
            if size <= 0 or position != offset:
                raise ValueError('Cannot verify HEVC packet boundaries for HDR10+')
            result.append((position, size)); offset += size
        if not result or offset != path.stat().st_size:
            raise ValueError('Cannot verify HEVC packet boundaries for HDR10+')
        return result

    def _extract_multwindow_hdr(self, output):
        from .hdr10plus_windows import extract_hdr10plus_from_access_unit, transform_window_geometries
        if shutil.disk_usage(self.work).free < self.source.stat().st_size + 1024 ** 3:
            raise ValueError('Multiple-window HDR10+ needs more temporary disk space')
        elementary = self.work / 'hdr10plus-window-source.hevc'
        self._call([self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-n', '-i', self.source,
                    '-map', f'0:{self.media.video_index}', '-c:v', 'copy', '-bsf:v',
                    'hevc_mp4toannexb', '-f', 'hevc', elementary])
        ranges = self._packet_ranges(elementary, 'hdr10plus-source')
        packets = self.work / 'hdr10plus-source-pts.csv'
        frames = self.work / 'hdr10plus-frame-pts.csv'
        for entries, path in (('packet=pts', packets), ('frame=best_effort_timestamp', frames)):
            self._call([self.tools['ffprobe'], '-v', 'error', '-select_streams', str(self.media.video_index),
                        '-show_entries', entries, '-of', 'csv=p=0', '-o', path, self.source])
        def timestamps(path):
            values = []
            for line in path.read_text(encoding='utf-8').splitlines():
                first = line.split(',', 1)[0]
                if first:
                    try: values.append(int(first))
                    except ValueError as error: raise ValueError('Cannot align HDR10+ source timestamps') from error
            return values
        pts, display = timestamps(packets), timestamps(frames)
        if len(ranges) != len(pts) or len(pts) != len(display) or len(set(pts)) != len(pts) or sorted(pts) != display:
            raise ValueError('Cannot align HDR10+ source timestamps')
        ordered = []
        with elementary.open('rb') as source:
            for (offset, size), timestamp in zip(ranges, pts):
                if self.isInterruptionRequested():
                    raise RuntimeError('Processing cancelled')
                source.seek(offset)
                payload = extract_hdr10plus_from_access_unit(source.read(size))
                if payload is None:
                    raise ValueError('HDR10+ metadata is missing from a source frame')
                ordered.append((timestamp, transform_window_geometries(payload, self.media, self.settings).hex()))
        ordered.sort(key=lambda item: item[0])
        output.write_text(json.dumps([payload for _, payload in ordered], separators=(',', ':')), encoding='utf-8')
        elementary.unlink()
        return len(ordered)

    def _inject_multwindow_hdr(self, elementary, output, payloads, name):
        from .hdr10plus_windows import inject_hdr10plus_into_access_unit
        ranges = self._packet_ranges(elementary, name)
        if len(ranges) != len(payloads):
            raise ValueError('HDR10+ output packet count differs from metadata')
        with elementary.open('rb') as source, output.open('xb') as target:
            for (offset, size), payload in zip(ranges, payloads):
                if self.isInterruptionRequested():
                    raise RuntimeError('Processing cancelled')
                source.seek(offset)
                target.write(inject_hdr10plus_into_access_unit(source.read(size), bytes.fromhex(payload)))

    def _verify_multwindow_hdr(self, stream, expected):
        from .hdr10plus_windows import extract_hdr10plus_from_access_unit
        ranges = self._packet_ranges(stream, 'hdr10plus-verified')
        if len(ranges) != len(expected):
            raise ValueError('HDR10+ metadata differs after remux; output was not written')
        with stream.open('rb') as source:
            for (offset, size), expected_hex in zip(ranges, expected):
                source.seek(offset)
                actual = extract_hdr10plus_from_access_unit(source.read(size))
                if actual is None or actual.hex() != expected_hex:
                    raise ValueError('HDR10+ metadata differs after remux; output was not written')

    def _dovi_count(self, path, executable):
        output = self._call([executable, 'info', '-i', path, '-s'])
        match = re.search(r'^\s*Frames:\s*(\d+)\s*$', output, re.M)
        if not match:
            raise ValueError('Cannot count Dolby Vision metadata frames')
        return int(match.group(1))

    def _source_frame_count(self):
        # Equal nominal and average frame rates alone do not prove CFR. Check
        # every decoded presentation timestamp before assigning per-frame HDR.
        timestamps = self.work / 'dynamic-source-timestamps.csv'
        self._call([self.tools['ffprobe'], '-v', 'error', '-select_streams', str(self.media.video_index),
                    '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'csv=p=0',
                    '-o', timestamps, self.source])
        first, count = None, 0
        with timestamps.open(encoding='utf-8') as lines:
            for line in lines:
                field = line.strip().split(',', 1)[0]
                if not field:
                    continue
                try:
                    timestamp = float(field)
                except ValueError as error:
                    raise ValueError('Cannot verify source timestamps for HDR metadata alignment') from error
                if not math.isfinite(timestamp):
                    raise ValueError('Cannot verify source timestamps for HDR metadata alignment')
                if first is None:
                    first = timestamp
                if abs(timestamp - (first + count / self.media.fps)) > .002:
                    raise ValueError('Dynamic HDR metadata requires constant frame rate.')
                count += 1
        timestamps.unlink()
        if not count:
            raise ValueError('Cannot count video frames for HDR metadata alignment')
        return count

    def _variable_timecodes(self):
        """Assign output frames to their original or interpolated times."""
        timestamps = []
        last_duration = None
        source_times = self.work / 'rife-source.csv'
        if self.settings.engine != 'rife':
            source_times = self.work / 'dynamic-vfr-source.csv'
            self._call([self.tools['ffprobe'], '-v', 'error', '-select_streams', str(self.media.video_index),
                        '-show_entries', 'frame=best_effort_timestamp_time,duration_time', '-of', 'csv=p=0',
                        '-o', source_times, self.source])
        with source_times.open(encoding='utf-8') as lines:
            for line in lines:
                columns = line.strip().split(',')
                if not columns[0]:
                    continue
                try:
                    value = Fraction(columns[0])
                    duration = Fraction(columns[1]) if len(columns) > 1 and columns[1] not in ('', 'N/A') else None
                except (ValueError, ZeroDivisionError) as error:
                    raise ValueError('Cannot read source frame timing for variable-frame-rate HDR') from error
                if timestamps and value <= timestamps[-1]:
                    raise ValueError('Variable-frame-rate HDR requires strictly increasing source frame times')
                timestamps.append(value)
                last_duration = duration
        if not timestamps or last_duration is None or last_duration <= 0:
            raise ValueError('Cannot determine the final source frame duration for variable-frame-rate HDR')
        factor = self.settings.fps_multiplier if self.settings.engine == 'rife' else 1
        path = self.work / 'rife-vfr-timecodes.txt'
        origin = timestamps[0]
        with path.open('w', encoding='utf-8', newline='\n') as output:
            output.write('# timestamp format v2\n')
            for index, start in enumerate(timestamps):
                end = timestamps[index + 1] if index + 1 < len(timestamps) else start + last_duration
                for step in range(factor):
                    output.write(f'{float((start - origin + (end - start) * step / factor) * 1000):.6f}\n')
            # One extra timestamp fixes the last frame's duration in Matroska.
            output.write(f'{float((timestamps[-1] + last_duration - origin) * 1000):.6f}\n')
        return len(timestamps), path

    def _prepare(self):
        executables = metadata_tools(self.tools['ffmpeg'])
        if any(not path.is_file() for path in executables.values()):
            raise ValueError('HDR10+ and Dolby Vision tools are missing from the application runtime')
        plus = self.work / 'hdr10plus-source.json'
        rpu = self.work / 'dovi-source.bin'
        raw_plus = False
        try:
            has_plus = self._stream_extract(executables['hdr10plus'], 'hdr10plus', plus, "File doesn't contain dynamic metadata")
        except (ValueError, RuntimeError) as error:
            if self.isInterruptionRequested():
                raise
            self.log.emit('Standard HDR10+ parser could not read the source; checking individual window metadata.')
            self._extract_multwindow_hdr(plus)
            has_plus = raw_plus = True
        has_dovi = self._stream_extract(executables['dovi'], 'dovi', rpu, 'No RPU was found in input file')
        needs_retime = self.media.variable_rate and (self.settings.engine == 'rife' or has_plus or has_dovi)
        if not (has_plus or has_dovi or needs_retime):
            return DynamicMetadata()
        if self.settings.codec not in ('libx265', 'hevc_nvenc'):
            raise ValueError('HDR10+ and Dolby Vision require HEVC output. Select libx265 or hevc_nvenc.')
        if needs_retime:
            mkvmerge = Path(self.tools['ffmpeg']).resolve().parent.parent / 'hdr-metadata' / 'mkvmerge.exe'
            if not mkvmerge.is_file():
                raise ValueError('Variable-frame-rate HDR requires the bundled timestamp tool; update the application runtime.')
            source_count, timecodes = self._variable_timecodes()
        else:
            try:
                source_count, timecodes = self._source_frame_count(), None
            except ValueError as error:
                if str(error) != 'Dynamic HDR metadata requires constant frame rate.' or self.settings.engine == 'rife':
                    raise
                if self.settings.resume_segments:
                    raise ValueError('Variable-frame-rate input cannot use saved AI segments. Turn off Resume using saved AI segments.') from error
                mkvmerge = Path(self.tools['ffmpeg']).resolve().parent.parent / 'hdr-metadata' / 'mkvmerge.exe'
                if not mkvmerge.is_file():
                    raise ValueError('Variable-frame-rate HDR requires the bundled timestamp tool; update the application runtime.') from error
                source_count, timecodes = self._variable_timecodes()
        if has_plus:
            content = json.loads(plus.read_text(encoding='utf-8'))
            plus_frames = content if raw_plus else _hdr10_signatures(content)
            if not raw_plus and (len(plus_frames) != source_count or
                                 any(frame.get('NumberOfWindows') != 1 for frame in plus_frames)):
                self._extract_multwindow_hdr(plus)
                raw_plus = True
                plus_frames = json.loads(plus.read_text(encoding='utf-8'))
            if len(plus_frames) != source_count:
                raise ValueError('HDR10+ metadata count differs from the source frame count; output was not written')
        converted_profile7 = converted_profile5 = False
        fel_bl = fel_el = None
        if has_dovi:
            if self._dovi_count(rpu, executables['dovi']) != source_count:
                raise ValueError('Dolby Vision metadata count differs from the source frame count; output was not written')
            if self.media.color_transfer != 'smpte2084':
                raise ValueError('Only Dolby Vision profile 8.1 with an HDR10 base layer is currently supported')
            probe = self._probe(self.source, '-select_streams', str(self.media.video_index), '-show_streams')
            stream = probe['streams'][0]
            _validate_dovi_display_matrix(stream)
            config = next((side for side in stream.get('side_data_list', [])
                           if side.get('side_data_type') == 'DOVI configuration record'), {})
            info = self._call([executables['dovi'], 'info', '-i', rpu, '-s'])
            if (config.get('bl_present_flag') != 1 or config.get('rpu_present_flag') != 1):
                raise ValueError('Dolby Vision source is missing its base layer or metadata')
            if config.get('dv_profile') == 5:
                if (config.get('el_present_flag') != 0 or config.get('dv_bl_signal_compatibility_id') != 0 or
                        not re.search(r'^\s*Profile:\s*5\s*$', info, re.M)):
                    raise ValueError('Dolby Vision profile 5 configuration is not supported')
                source_json = self.work / 'dovi-profile5-source.json'
                self._call([executables['dovi'], 'export', '-i', rpu, '-d', f'all={source_json.name}'])
                source_metadata = json.loads(source_json.read_text(encoding='utf-8'))
                mode = self.work / 'dovi-profile5-convert.json'
                mode.write_text('{"mode":3}', encoding='utf-8')
                converted = self.work / 'dovi-profile5-converted.bin'
                self._call([executables['dovi'], 'editor', '-i', rpu, '-j', mode, '-o', converted])
                converted_json = self.work / 'dovi-profile5-converted.json'
                self._call([executables['dovi'], 'export', '-i', converted, '-d', f'all={converted_json.name}'])
                converted_metadata = json.loads(converted_json.read_text(encoding='utf-8'))
                def display_metadata(frame):
                    data = frame.get('vdr_dm_data')
                    if not isinstance(data, dict):
                        raise ValueError('Dolby Vision profile 5 display metadata is missing')
                    # Mode 3 replaces the color-space conversion coefficients.
                    # All scene, trim, luminance and active-area data must survive.
                    return {key: value for key, value in data.items()
                            if not key.startswith(('ycc_to_rgb_', 'rgb_to_lms_')) and key != 'signal_color_space'}
                if (len(source_metadata) != source_count or len(converted_metadata) != source_count or any(
                        source.get('dovi_profile') != 5 or frame.get('dovi_profile') != 8 or
                        display_metadata(frame) != display_metadata(source)
                        for source, frame in zip(source_metadata, converted_metadata))):
                    raise ValueError('Dolby Vision profile 5 metadata conversion could not be verified')
                rpu = converted
                converted_profile5 = True
            elif config.get('dv_profile') == 7:
                if not re.search(r'^\s*Profile:\s*7\s*\((?:MEL|FEL)\)\s*$', info, re.M):
                    raise ValueError('Dolby Vision profile 7 enhancement layer could not be identified')
                source_json = self.work / 'dovi-profile7-source.json'
                self._call([executables['dovi'], 'export', '-i', rpu, '-d', f'all={source_json.name}'])
                source_metadata = json.loads(source_json.read_text(encoding='utf-8'))
                if (len(source_metadata) != source_count or any(frame.get('dovi_profile') != 7 or
                        frame.get('el_type') not in ('MEL', 'FEL') for frame in source_metadata)):
                    raise ValueError('Dolby Vision profile 7 enhancement layer could not be identified')
                if any(frame.get('el_type') == 'FEL' for frame in source_metadata):
                    if os.name != 'nt':
                        raise ValueError('Dolby Vision FEL requires a Windows x64 processor with AVX2 support')
                    import ctypes
                    if not ctypes.windll.kernel32.IsProcessorFeaturePresent(40):
                        raise ValueError('Dolby Vision FEL requires a Windows x64 processor with AVX2 support')
                    helper = Path(self.tools['ffmpeg']).resolve().parent.parent / 'fel-baker'
                    if any(not path.is_file() for path in (helper / 'python' / 'python.exe', helper / 'fel-bake.py',
                                                           helper / 'ffms2.dll', helper / 'felbaker.dll')):
                        raise ValueError('Dolby Vision FEL processing tools are missing; update the application runtime')
                    if self.settings.resume_segments or self.settings.restoration != 'off':
                        raise ValueError('Dolby Vision FEL requires saved AI segments and restoration to be turned off')
                    if timecodes and self.settings.subtitle_mode == 'burn':
                        raise ValueError('Variable-frame-rate Dolby Vision FEL cannot burn subtitles; use selectable subtitles instead')
                    if shutil.disk_usage(self.work).free < self.source.stat().st_size * 2 + 1024 ** 3:
                        raise ValueError('Dolby Vision FEL needs more free temporary disk space for its two video layers')
                    raw = self.work / 'fel-source.hevc'
                    fel_bl, fel_el = self.work / 'fel-base.hevc', self.work / 'fel-enhancement.hevc'
                    self._call([self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-i', self.source,
                                '-map', f'0:{self.media.video_index}', '-c:v', 'copy', '-bsf:v',
                                'hevc_mp4toannexb', '-f', 'hevc', raw])
                    try:
                        self._call([executables['dovi'], 'demux', '-i', raw, '--bl-out', fel_bl, '--el-out', fel_el])
                    finally:
                        raw.unlink(missing_ok=True)
                    if not fel_bl.is_file() or not fel_el.is_file() or not fel_bl.stat().st_size or not fel_el.stat().st_size:
                        raise ValueError('Dolby Vision FEL base and enhancement layers could not be separated')
                mode = self.work / 'dovi-profile7-convert.json'
                mode.write_text('{"mode":2}', encoding='utf-8')
                converted = self.work / 'dovi-profile7-converted.bin'
                self._call([executables['dovi'], 'editor', '-i', rpu, '-j', mode, '-o', converted])
                converted_json = self.work / 'dovi-profile7-converted.json'
                self._call([executables['dovi'], 'export', '-i', converted, '-d', f'all={converted_json.name}'])
                converted_metadata = json.loads(converted_json.read_text(encoding='utf-8'))
                if (len(converted_metadata) != source_count or any(
                        frame.get('dovi_profile') != 8 or frame.get('vdr_dm_data') != source.get('vdr_dm_data')
                        for source, frame in zip(source_metadata, converted_metadata))):
                    raise ValueError('Dolby Vision profile 7 metadata conversion could not be verified')
                rpu = converted
                converted_profile7 = True
            elif (config.get('dv_profile') != 8 or config.get('dv_bl_signal_compatibility_id') != 1 or
                  config.get('el_present_flag') != 0 or not re.search(r'^\s*Profile:\s*8\s*$', info, re.M)):
                raise ValueError('Only single-layer Dolby Vision profile 8.1 or profile 7 MEL is supported')
            area = self.work / 'dovi-area.json'
            self._call([executables['dovi'], 'export', '-i', rpu, '-d', f'level5={area.name}'])
            data = json.loads(area.read_text(encoding='utf-8'))
            adjusted, changed = adjust_dovi_active_area(data, self.media, self.settings)
            if changed:
                config = self.work / 'dovi-area-adjusted.json'
                config.write_text(json.dumps(adjusted, separators=(',', ':')), encoding='utf-8')
                edited = self.work / 'dovi-area-adjusted.bin'
                self._call([executables['dovi'], 'editor', '-i', rpu, '-j', config, '-o', edited])
                checked = self.work / 'dovi-area-verified.json'
                self._call([executables['dovi'], 'export', '-i', edited, '-d', f'level5={checked.name}'])
                actual = json.loads(checked.read_text(encoding='utf-8')).get('active_area', {})
                if (actual.get('presets') != adjusted['active_area'].get('presets') or
                        actual.get('edits') != adjusted['active_area'].get('edits')):
                    raise ValueError('Dolby Vision active-area adjustment could not be verified')
                rpu = edited
        return DynamicMetadata(plus if has_plus else None, rpu if has_dovi else None, source_count,
                               converted_profile7, timecodes, converted_profile5, fel_bl, fel_el, raw_plus)

    def _finish_dynamic(self):
        executables = metadata_tools(self.tools['ffmpeg'])
        info = self.metadata
        factor = self.settings.fps_multiplier if self.settings.engine == 'rife' else 1
        expected_count = info.source_frames * factor
        if self._frame_count(self.partial) != expected_count:
            raise ValueError('Encoded frame count differs from HDR metadata; output was not written')

        plus = info.hdr10plus
        expected_plus = None
        if plus:
            document = json.loads(plus.read_text(encoding='utf-8'))
            if info.hdr10plus_raw:
                expected_plus = [payload for payload in document for _ in range(factor)]
            else:
                document = duplicate_hdr10plus(document, factor)
                expected_plus = _hdr10_signatures(document)
                plus = self.work / 'hdr10plus-aligned.json'
                plus.write_text(json.dumps(document, separators=(',', ':')), encoding='utf-8')

        rpu = info.dovi
        if rpu and factor > 1:
            edit = {'duplicate': [{'source': index, 'offset': index + 1, 'length': factor - 1}
                                  for index in range(info.source_frames)]}
            config = self.work / 'dovi-align.json'
            config.write_text(json.dumps(edit, separators=(',', ':')), encoding='utf-8')
            duplicated = self.work / 'dovi-duplicated.bin'
            self._call([executables['dovi'], 'editor', '-i', info.dovi, '-j', config, '-o', duplicated])
            # Editing scene flags precedes duplication in dovi_tool, so clear
            # flags on the inserted frames in a second, explicit pass.
            cuts = {f'{index * factor + 1}-{index * factor + factor - 1}': False
                    for index in range(info.source_frames)}
            cuts_config = self.work / 'dovi-scene-align.json'
            cuts_config.write_text(json.dumps({'scene_cuts': cuts}, separators=(',', ':')), encoding='utf-8')
            rpu = self.work / 'dovi-aligned.bin'
            self._call([executables['dovi'], 'editor', '-i', duplicated, '-j', cuts_config, '-o', rpu])
        if rpu and self._dovi_count(rpu, executables['dovi']) != expected_count:
            raise ValueError('Aligned Dolby Vision metadata count does not match the output frame count')

        elementary = self.work / 'dynamic-base.hevc'
        self._call([self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-n', '-i', self.partial,
                    '-map', '0:v:0', '-c:v', 'copy', '-bsf:v', 'hevc_mp4toannexb', '-f', 'hevc', elementary])
        if plus:
            injected = self.work / 'dynamic-hdr10plus.hevc'
            if info.hdr10plus_raw:
                self._inject_multwindow_hdr(elementary, injected, expected_plus, 'hdr10plus-output')
            else:
                self._call([executables['hdr10plus'], 'inject', '-i', elementary, '-j', plus, '-o', injected])
            elementary.unlink()
            elementary = injected
        if rpu:
            injected = self.work / 'dynamic-dovi.hevc'
            self._call([executables['dovi'], 'inject-rpu', '-i', elementary, '--rpu-in', rpu, '-o', injected])
            elementary.unlink()
            elementary = injected

        if info.timecodes:
            timed = self.work / 'dynamic-timed.mkv'
            mkvmerge = Path(self.tools['ffmpeg']).resolve().parent.parent / 'hdr-metadata' / 'mkvmerge.exe'
            self._call([mkvmerge, '-q', '-o', timed, '--timestamps', f'0:{info.timecodes}', elementary])
            elementary.unlink()
            elementary = timed

        original = self._probe(self.partial, '-show_streams', '-show_format')
        video = next(stream for stream in original['streams'] if stream.get('codec_type') == 'video')
        start = float(video.get('start_time') or 0)
        fps = Fraction(self.media.fps * factor).limit_denominator(1_000_000)
        muxed = self.work / ('dynamic-final.' + self.settings.container)
        cmd = [self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-n', '-copyts', '-fflags', '+genpts']
        if not info.timecodes:
            cmd += ['-r', f'{fps.numerator}/{fps.denominator}']
        cmd += ['-itsoffset', f'{start:.9f}', '-i', elementary,
               '-i', self.partial, '-map', '0:v:0', '-map', '1:a?', '-map', '1:s?', '-map', '1:t?',
               '-map_metadata', '1', '-map_metadata:s:v:0', '1:s:v:0', '-map_chapters', '1', '-c', 'copy']
        if rpu:
            cmd += ['-bsf:v', 'dovi_rpu=compression=none']
        if self.settings.container == 'mp4':
            cmd += ['-movflags', '+faststart']
            if rpu:
                # FFmpeg requires this to write the Dolby Vision configuration
                # record in MP4; verification below rejects missing signaling.
                cmd += ['-strict', 'unofficial']
        cmd += ['-avoid_negative_ts', 'disabled', muxed]
        self._call(cmd)
        elementary.unlink()

        result = self._probe(muxed, '-show_streams', '-show_format')
        final_video = next(stream for stream in result['streams'] if stream.get('codec_type') == 'video')
        if abs(float(final_video.get('start_time') or 0) - start) > .002:
            raise ValueError('Dynamic HDR remux changed video timing; output was not written')
        for kind in ('audio', 'subtitle'):
            before = [stream for stream in original['streams'] if stream.get('codec_type') == kind]
            after = [stream for stream in result['streams'] if stream.get('codec_type') == kind]
            if len(before) != len(after):
                raise ValueError('Dynamic HDR remux changed media streams; output was not written')
            for source, target in zip(before, after):
                if source.get('start_time') not in (None, 'N/A') and target.get('start_time') not in (None, 'N/A'):
                    if abs(float(source['start_time']) - float(target['start_time'])) > .002:
                        raise ValueError('Dynamic HDR remux changed audio or subtitle timing; output was not written')
        if self._frame_count(muxed) != expected_count:
            raise ValueError('Dynamic HDR output frame count differs from metadata; output was not written')
        if info.timecodes:
            checked_times = self.work / 'rife-vfr-verified.csv'
            self._call([self.tools['ffprobe'], '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'frame=best_effort_timestamp_time,duration_time', '-of', 'csv=p=0',
                        '-o', checked_times, muxed])
            with info.timecodes.open(encoding='utf-8') as expected_file, checked_times.open(encoding='utf-8') as actual_file:
                expected = [Fraction(line.strip()) / 1000 for line in expected_file if line.strip() and not line.startswith('#')]
                actual = []
                for line in actual_file:
                    columns = line.strip().split(',')
                    if columns[0]:
                        actual.append((Fraction(columns[0]), Fraction(columns[1]) if len(columns) > 1 and columns[1] not in ('', 'N/A') else None))
            if len(expected) != expected_count + 1 or len(actual) != expected_count:
                raise ValueError('Variable-frame-rate HDR output frame count differs from the source timeline')
            origin = actual[0][0]
            if any(abs(float((timestamp - origin) - expected[index])) > .002 for index, (timestamp, _) in enumerate(actual)):
                raise ValueError('Variable-frame-rate HDR output timestamps could not be verified')
            last_duration = actual[-1][1]
            if last_duration is None or abs(float(last_duration - (expected[-1] - expected[-2]))) > .002:
                raise ValueError('Variable-frame-rate HDR final frame duration could not be verified')
            if self.settings.audio == 'copy':
                source_streams = self._probe(self.source, '-show_streams')['streams']
                source_video = next(stream for stream in source_streams if stream.get('index') == self.media.video_index)
                source_start = float(source_video.get('start_time') or 0)
                for index, _ in enumerate(stream for stream in source_streams if stream.get('codec_type') == 'audio'):
                    options = ('-select_streams', f'a:{index}', '-read_intervals', '%+#1', '-show_entries', 'packet=pts_time')
                    source_packets = self._probe(self.source, *options).get('packets', [])
                    output_packets = self._probe(muxed, *options).get('packets', [])
                    if not source_packets or not output_packets:
                        raise ValueError('Variable-frame-rate HDR audio timing could not be verified')
                    try:
                        source_pts = float(source_packets[0]['pts_time'])
                        output_pts = float(output_packets[0]['pts_time'])
                    except (KeyError, ValueError, TypeError) as error:
                        raise ValueError('Variable-frame-rate HDR audio timing could not be verified') from error
                    if (not all(map(math.isfinite, (source_pts, output_pts, source_start))) or
                            abs((source_pts - source_start) - (output_pts - start)) > .003):
                        raise ValueError('Variable-frame-rate HDR audio timing could not be verified')
        if rpu:
            dovi_config = next((side for side in final_video.get('side_data_list', [])
                                if side.get('side_data_type') == 'DOVI configuration record'), {})
            if dovi_config.get('dv_profile') != 8 or dovi_config.get('dv_bl_signal_compatibility_id') != 1:
                raise ValueError('Dolby Vision profile 8.1 signaling could not be verified; output was not written')

        checked_stream = self.work / 'dynamic-verify.hevc'
        self._call([self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-n', '-i', muxed, '-map', '0:v:0',
                    '-c:v', 'copy', '-bsf:v', 'hevc_mp4toannexb', '-f', 'hevc', checked_stream])
        if plus:
            if info.hdr10plus_raw:
                self._verify_multwindow_hdr(checked_stream, expected_plus)
            else:
                checked_plus = self.work / 'hdr10plus-verified.json'
                self._call([executables['hdr10plus'], 'extract', '-i', checked_stream, '-o', checked_plus])
                if _hdr10_signatures(json.loads(checked_plus.read_text(encoding='utf-8'))) != expected_plus:
                    raise ValueError('HDR10+ metadata differs after remux; output was not written')
        if rpu:
            checked_rpu = self.work / 'dovi-verified.bin'
            self._call([executables['dovi'], 'extract-rpu', '-i', checked_stream, '-o', checked_rpu])
            if self._dovi_count(checked_rpu, executables['dovi']) != expected_count:
                raise ValueError('Dolby Vision metadata count differs after remux; output was not written')
            expected_json, actual_json = self.work / 'dovi-expected.json', self.work / 'dovi-verified.json'
            self._call([executables['dovi'], 'export', '-i', rpu, '-d', f'all={expected_json.name}'])
            self._call([executables['dovi'], 'export', '-i', checked_rpu, '-d', f'all={actual_json.name}'])
            expected_dovi = _canonical_dovi(json.loads(expected_json.read_text(encoding='utf-8')))
            actual_dovi = _canonical_dovi(json.loads(actual_json.read_text(encoding='utf-8')))
            if expected_dovi != actual_dovi:
                raise ValueError('Dolby Vision metadata differs after remux; output was not written')
        self.log.emit('Dynamic HDR frame count, metadata and output timing verified.')
        return muxed

    def run(self):
        try:
            self.result = self._prepare() if self.phase == 'prepare' else self._finish_dynamic()
        except Exception as error:
            self.error = str(error)

