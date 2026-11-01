"""Bounded OS-pipe processing: source -> lossless NUT -> AI -> final encode.

Only the final delivery file is stored. Each process owns its pipe endpoint;
closing the parent's duplicates is essential for EOF and cancellation to work.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
import os
import subprocess
import threading
import time

from PySide6.QtCore import QThread, Signal

from .pipeline import ai_command, final_command


class StreamingJob(QThread):
    progress = Signal(float)
    fps = Signal(float)
    log = Signal(str)

    def __init__(self, settings, tools, source, output, work, media, help_text,
                 subtitle_name='', watermark_name='', parent=None):
        super().__init__(parent)
        self.settings, self.tools = settings, tools
        self.source, self.output, self.work, self.media = source, output, work, media
        self.help_text = help_text
        self.subtitle_name, self.watermark_name = subtitle_name, watermark_name
        self.error = ''
        self.source_frames = self.encoded_frames = 0
        self._stop_reason = ''
        self.processes = []
        self._readers = []
        self._tail = deque(maxlen=120)

    def stop(self, reason=''):
        self._stop_reason = reason
        self.requestInterruption()

    def _read_lines(self, pipe, label, progress=False):
        try:
            for raw in iter(pipe.readline, b''):
                line = raw.decode('utf-8', 'replace').strip()
                if not line:
                    continue
                if progress:
                    if label == 'encode' and line.startswith('fps='):
                        try:
                            self.fps.emit(float(line.partition('=')[2]))
                        except ValueError:
                            pass
                    if line.startswith('frame='):
                        try:
                            value = int(line.partition('=')[2])
                            if label == 'decode': self.source_frames = value
                            else: self.encoded_frames = value
                        except ValueError:
                            pass
                    if label == 'encode' and line.startswith('out_time_us=') and self.media.duration:
                        try:
                            self.progress.emit(min(99, max(0, 100 * int(line.partition('=')[2]) / 1e6 / self.media.duration)))
                        except ValueError:
                            pass
                    if line.startswith(('frame=','fps=','bitrate=','total_size=','out_time','dup_frames=','drop_frames=','speed=','progress=','stream_')):
                        continue
                self._tail.append(f'{label}: {line}')
                self.log.emit(f'{label}: {line}')
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    def _reader(self, pipe, label, progress=False):
        thread = threading.Thread(target=self._read_lines, args=(pipe,label,progress), daemon=True)
        self._readers.append(thread)
        thread.start()

    def _start(self, command, label, **kwargs):
        self.log.emit(subprocess.list2cmdline(command))
        process = subprocess.Popen(command, cwd=self.work,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            stderr=subprocess.PIPE, **kwargs)
        self.processes.append((label,process))
        return process

    def run(self):
        try:
            from .pipeline import hdr_to_sdr_filters
            filters = hdr_to_sdr_filters(self.media)
            if self.settings.restoration == 'mild':
                filters.append('hqdn3d=1.5:1.5:3:3')
            filters.append('setpts=PTS-STARTPTS')
            decode = [self.tools['ffmpeg'],'-hide_banner','-v','error','-nostdin',
                '-i',str(self.source),'-map',f'0:{self.media.video_index}','-an','-sn','-dn',
                '-vf',','.join(filters),'-c:v','rawvideo','-pix_fmt','bgr0','-fps_mode','passthrough',
                '-progress','pipe:2','-nostats','-f','nut','pipe:1']
            first = self._start(decode,'decode',stdout=subprocess.PIPE)
            self._reader(first.stderr,'decode',True)
            native = ai_command(self.settings,self.tools,Path('pipe:0'),Path('pipe:1.nut'),self.media,self.help_text)
            native[native.index('-c')+1] = 'rawvideo'
            native += ['--no-progress','--log-level','warn']
            # Resolve model resources beside the engine, while staging finishing
            # sidecars in our own working directory for the final encoder.
            self.log.emit(subprocess.list2cmdline(native))
            second = subprocess.Popen(native,cwd=Path(self.tools['video2x']).parent,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,
                stdin=first.stdout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.processes.append(('AI',second))
            first.stdout.close()
            self._reader(second.stderr,'AI')
            encode = final_command(self.settings,self.tools,self.source,Path('pipe:0'),self.output,
                                   self.media,self.subtitle_name,self.watermark_name)
            third = self._start(encode,'encode',stdin=second.stdout,stdout=subprocess.PIPE)
            second.stdout.close()
            self._reader(third.stderr,'encode')
            self._reader(third.stdout,'encode',True)
            while True:
                if self.isInterruptionRequested():
                    raise RuntimeError(self._stop_reason or 'Processing cancelled')
                failed = [(name,p.returncode) for name,p in self.processes if p.poll() not in (None,0)]
                if failed:
                    raise RuntimeError('; '.join(f'{name} exited with code {code}' for name,code in failed))
                if all(p.poll() is not None for _,p in self.processes):
                    break
                time.sleep(.05)
            for thread in self._readers:
                thread.join(5)
            expected = self.source_frames * (self.settings.fps_multiplier if self.settings.engine=='rife' else 1)
            if expected < 1 or self.encoded_frames != expected:
                raise RuntimeError(f'Stream frame verification failed: input {self.source_frames}, encoded {self.encoded_frames}, expected {expected}')
            self.log.emit(f'Stream verified: {self.source_frames} source frames, {self.encoded_frames} encoded frames; no full-video AI intermediate.')
        except Exception as error:
            self.error = str(error)
        finally:
            for _,process in reversed(self.processes):
                if process.poll() is None:
                    process.kill()
            for _,process in self.processes:
                try: process.wait(5)
                except subprocess.TimeoutExpired: process.kill()
            for thread in self._readers:
                thread.join(5)
            if self.error and self._tail:
                self.error += '\n' + '\n'.join(list(self._tail)[-12:])
