"""Generate reproducible synthetic clips for manual model comparison."""
from pathlib import Path
import argparse
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', type=Path, default=ROOT / 'runtime/ffmpeg/ffmpeg.exe')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/quality-samples')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = 'testsrc2=size=640x360:rate=24:duration=3'
    cases = {
        '01-lines.mp4': (source, 'drawgrid=width=16:height=16:thickness=1:color=white@0.8', ['-crf', '14']),
        '02-motion.mp4': (source, 'null', ['-crf', '14']),
        '03-dark.mp4': (source, 'eq=brightness=-0.25:saturation=0.7', ['-crf', '14']),
        '04-compressed.mp4': (source, 'null', ['-b:v', '120k', '-maxrate', '120k', '-bufsize', '240k']),
    }
    for name, (generator, filter_graph, quality) in cases.items():
        subprocess.run([str(args.ffmpeg), '-hide_banner', '-v', 'error', '-nostdin', '-n',
                        '-f', 'lavfi', '-i', generator, '-vf', filter_graph, '-an',
                        '-c:v', 'libx264', '-threads', '1', *quality, '-pix_fmt', 'yuv420p',
                        str(args.output / name)], check=True)
    subprocess.run([str(args.ffmpeg), '-hide_banner', '-v', 'error', '-nostdin', '-n',
                    '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=1.5',
                    '-f', 'lavfi', '-i', 'smptebars=size=640x360:rate=24:duration=1.5',
                    '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]',
                    '-c:v', 'libx264', '-threads', '1', '-crf', '14', '-pix_fmt', 'yuv420p',
                    str(args.output / '05-scene-cut.mp4')], check=True)
    print(f'Created five synthetic samples in {args.output}')


if __name__ == '__main__':
    main()
