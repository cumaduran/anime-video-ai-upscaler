"""Convert a demuxed Dolby Vision FEL pair to full-range planar PQ RGB."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys


def main():
    if os.name != 'nt' or not ctypes.windll.kernel32.IsProcessorFeaturePresent(40):
        raise RuntimeError('Dolby Vision FEL requires a Windows x64 processor with AVX2 support')
    if len(sys.argv) != 9:
        raise ValueError('Expected BL, EL, frame count, width, height, plugin directory, orientation and rotation')
    bl_path, el_path, count, width, height, plugin_dir, orientation, rotation = sys.argv[1:]
    count, width, height = int(count), int(width), int(height)
    import vapoursynth as vs
    core = vs.core
    core.max_cache_size = 1024
    core.num_threads = 2
    core.std.LoadPlugin(str(Path(plugin_dir) / 'ffms2.dll'))
    core.std.LoadPlugin(str(Path(plugin_dir) / 'felbaker.dll'))
    bl = core.ffms2.Source(bl_path, cachefile=bl_path + '.ffindex', threads=2)
    el = core.ffms2.Source(el_path, cachefile=el_path + '.ffindex', threads=2)
    if bl.num_frames != count or el.num_frames != count:
        raise ValueError('Dolby Vision FEL base and enhancement layer frame counts do not match')
    baked = core.fel.Bake(bl, el, dovi_metadata=1)
    if orientation:
        a, b, c, d = map(int, orientation.split(','))
        if a and d:
            if a < 0: baked = core.std.FlipHorizontal(baked)
            if d < 0: baked = core.std.FlipVertical(baked)
        elif b and c:
            baked = core.std.Transpose(baked)
            if c < 0: baked = core.std.FlipHorizontal(baked)
            if b < 0: baked = core.std.FlipVertical(baked)
        else:
            raise ValueError('Dolby Vision FEL display orientation is invalid')
    else:
        rotation = int(rotation) % 360
        if rotation in (90, 270): baked = core.std.Transpose(baked)
        if rotation in (180, 270): baked = core.std.FlipHorizontal(baked)
        if rotation in (90, 180): baked = core.std.FlipVertical(baked)
    if baked.width != width or baked.height != height or baked.format.id != vs.RGB48:
        raise ValueError('Dolby Vision FEL output format or dimensions are invalid')
    out = sys.stdout.buffer
    for index in range(count):
        if 'DolbyVisionRPU' not in el.get_frame(index).props:
            raise ValueError(f'Dolby Vision FEL metadata is missing at frame {index}')
        frame = baked.get_frame(index)
        # VapourSynth stores R, G, B; FFmpeg's gbrp16le expects G, B, R.
        for plane in (1, 2, 0):
            out.write(memoryview(frame[plane]).tobytes())
    out.flush()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
