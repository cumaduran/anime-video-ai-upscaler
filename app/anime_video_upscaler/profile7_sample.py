"""Prepare an honest short comparison of a Dolby Vision profile 7 source."""
from __future__ import annotations

from dataclasses import replace
import math
import os
from pathlib import Path
import subprocess
import tempfile

from PySide6.QtCore import QThread, Signal

from .dynamic_hdr import DynamicHDRWorker
from .pipeline import high_precision_output_filters


class Profile7SampleWorker(QThread):
    log = Signal(str)

    def __init__(self, tools, source, work, media, settings, start, duration, parent=None):
        super().__init__(parent)
        self.tools, self.source, self.work, self.media = tools, Path(source), Path(work), media
        self.settings, self.start_time, self.duration = settings, start, duration
        self.error = ''
        self.metadata_worker = None
        self.processes = []

    def stop(self):
        self.requestInterruption()
        if self.metadata_worker:
            self.metadata_worker.stop()
        for process in self.processes:
            if process.poll() is None:
                process.kill()

    def run(self):
        try:
            if self.media.variable_rate:
                raise ValueError('Profile 7 comparison currently requires constant frame rate; process a short copy for variable-frame-rate input.')
            settings = replace(self.settings, restoration='off', resume_segments=False)
            worker = DynamicHDRWorker('prepare', self.tools, self.source, self.work, self.media, settings)
            self.metadata_worker = worker
            worker.log.connect(self.log.emit)
            # The comparison clip is an HDR10 intermediate; it does not carry
            # HDR10+ or Dolby metadata. Skip an unnecessary full HDR10+ scan.
            metadata = worker._prepare(ignore_hdr10plus=True)
            if self.isInterruptionRequested():
                raise RuntimeError('Sample preparation cancelled')
            if metadata.timecodes:
                raise ValueError('Profile 7 comparison currently requires constant frame rate; process a short copy for variable-frame-rate input.')
            if not metadata.dovi or not metadata.converted_profile7:
                raise ValueError('Profile 7 sample metadata could not be verified')
            clip = self.work / 'sample.mkv'
            if not metadata.fel_bl:
                worker._call([self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-n', '-ss', str(self.start_time),
                              '-i', self.source, '-t', str(self.duration), '-map', f'0:{self.media.video_index}',
                              '-an', '-sn', '-dn', '-c:v', 'ffv1', '-pix_fmt', 'yuv420p10le',
                              '-fps_mode', 'passthrough', clip])
                return
            first = max(0, math.ceil(self.start_time * self.media.fps - 1e-7))
            end = min(metadata.source_frames, math.ceil((self.start_time + self.duration) * self.media.fps - 1e-7))
            if first >= end:
                raise ValueError('The selected sample does not contain a video frame')
            helper = Path(self.tools['ffmpeg']).resolve().parent.parent / 'fel-baker'
            bake = [helper / 'python/python.exe', '-u', helper / 'fel-bake.py', metadata.fel_bl, metadata.fel_el,
                    metadata.source_frames, self.media.width, self.media.height, helper,
                    ','.join(map(str, self.media.display_orientation)), self.media.rotation, first, end]
            encode = [self.tools['ffmpeg'], '-v', 'error', '-nostdin', '-n', '-f', 'rawvideo', '-pixel_format', 'gbrp16le',
                      '-video_size', f'{self.media.width}x{self.media.height}', '-framerate', str(self.media.fps),
                      '-i', 'pipe:0', '-vf', ','.join(high_precision_output_filters(self.media)),
                      '-color_primaries', 'bt2020', '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc',
                      '-color_range', 'tv', '-c:v', 'ffv1', '-pix_fmt', 'yuv420p10le', clip]
            self.log.emit('Preparing Dolby Vision FEL sample from both video layers.')
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            with tempfile.TemporaryFile() as errors:
                baker = subprocess.Popen([str(x) for x in bake], cwd=self.work, stdout=subprocess.PIPE, stderr=errors, creationflags=flags)
                self.processes.append(baker)
                writer = subprocess.Popen([str(x) for x in encode], cwd=self.work, stdin=baker.stdout,
                                          stdout=subprocess.DEVNULL, stderr=errors, creationflags=flags)
                self.processes.append(writer)
                baker.stdout.close()
                writer_code, bake_code = writer.wait(), baker.wait()
                errors.seek(0, os.SEEK_END); size = errors.tell(); errors.seek(max(0, size - 4000))
                detail = errors.read().decode('utf-8', 'replace')
            if self.isInterruptionRequested():
                raise RuntimeError('Sample preparation cancelled')
            if writer_code or bake_code:
                raise RuntimeError('Dolby Vision FEL sample preparation failed: ' + detail)
        except Exception as error:
            self.error = str(error)
        finally:
            for process in self.processes:
                if process.poll() is None:
                    process.kill()
            self.metadata_worker = None
