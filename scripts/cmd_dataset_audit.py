"""Read-only audit of a user-prepared CMD2X/CMD4X video dataset.

Run: python scripts/cmd_dataset_audit.py --root <dataset> --ffprobe <ffprobe> --output <manifest.json>
Video files remain in place. Rights are declared by the user, not verified by this tool.
"""
from __future__ import annotations

import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

EXTENSIONS = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.ts', '.m2ts', '.wmv'}
SPLITS = ('train', 'val', 'test')


def _ratio(value):
    try:
        number = float(Fraction(str(value).replace(':', '/')))
        return number if math.isfinite(number) and number > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_rights(root):
    path = root / 'provenance.csv'
    if not path.is_file():
        raise ValueError('Missing provenance.csv; see docs/cmd-dataset-guide.md')
    with path.open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'series', 'source', 'rights', 'training_permitted'}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError('provenance.csv requires series, source, rights, training_permitted')
        result = {}
        for row in reader:
            series = (row.get('series') or '').strip()
            source = (row.get('source') or '').strip()
            rights = (row.get('rights') or '').strip()
            permitted = (row.get('training_permitted') or '').strip().lower()
            if not series or not source or not rights or permitted not in ('yes', 'no'):
                raise ValueError('Every provenance row needs series, source, rights and yes/no permission')
            key = series.casefold()
            if key in result:
                raise ValueError(f'Duplicate provenance series: {series}')
            result[key] = {'series': series, 'source': source, 'rights': rights,
                           'training_permitted': permitted == 'yes'}
    return result


def audit(root, ffprobe, hashing=False):
    root = Path(root).expanduser().resolve(strict=True)
    ffprobe = Path(ffprobe).expanduser().resolve(strict=True)
    if not root.is_dir() or not ffprobe.is_file():
        raise ValueError('Dataset root must be a folder and ffprobe must be a file')
    rights = read_rights(root)
    seen_series = {}
    seen_hashes = {}
    records = []
    warnings = ['Training permissions are user declarations and have not been independently verified.',
                'Watermarks, subtitle burn-in and animation style require manual review.']
    if not hashing:
        warnings.append('Content hashing was not enabled; duplicate videos across splits were not checked.')
    for split in SPLITS:
        directory = root / split
        if not directory.is_dir():
            raise ValueError(f'Missing split folder: {split}')
        count = 0
        for candidate in sorted(directory.rglob('*')):
            if not candidate.is_file() or candidate.suffix.lower() not in EXTENSIONS:
                continue
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(directory.resolve()) or not resolved.is_relative_to(root):
                raise ValueError(f'Video resolves outside dataset split: {candidate}')
            relative = candidate.relative_to(root)
            if len(relative.parts) < 3:
                raise ValueError(f'Place video under {split}/<series>/filename: {relative}')
            series = relative.parts[1]
            key = series.casefold()
            if key in seen_series and seen_series[key] != split:
                raise ValueError(f'Series appears in multiple splits: {series}')
            seen_series[key] = split
            if key not in rights or not rights[key]['training_permitted']:
                raise ValueError(f'Missing declared training permission for series: {series}')
            result = subprocess.run([str(ffprobe), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(resolved)],
                                    capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError(f'ffprobe failed for {relative}: {result.stderr.decode("utf-8", "replace")[-500:]}')
            data = json.loads(result.stdout)
            video = next((v for v in data.get('streams', []) if v.get('codec_type') == 'video' and not v.get('disposition', {}).get('attached_pic')), None)
            if not video:
                raise ValueError(f'No video stream: {relative}')
            width, height = int(video.get('width', 0)), int(video.get('height', 0))
            if width < 1 or height < 1:
                raise ValueError(f'Invalid dimensions: {relative}')
            duration = _ratio(video.get('duration')) or _ratio(data.get('format', {}).get('duration'))
            avg, nominal = _ratio(video.get('avg_frame_rate')), _ratio(video.get('r_frame_rate'))
            rotation = int(float(video.get('tags', {}).get('rotate', 0)))
            for side in video.get('side_data_list', []):
                rotation = int(float(side.get('rotation', rotation)))
            if height < 1080:
                warnings.append(f'{relative}: below 1080p; review suitability for high-resolution targets.')
            if video.get('color_transfer') in ('smpte2084', 'arib-std-b67') or rotation % 360:
                warnings.append(f'{relative}: HDR or rotation metadata requires explicit preprocessing.')
            if avg and nominal and abs(avg - nominal) > 0.1:
                warnings.append(f'{relative}: frame-rate metadata may indicate variable timing; inspect manually.')
            record = {'path': relative.as_posix(), 'split': split, 'series': series, 'width': width,
                      'height': height, 'duration_seconds': duration, 'fps': avg, 'codec': video.get('codec_name'),
                      'size_bytes': resolved.stat().st_size}
            if hashing:
                digest = _sha256(resolved)
                if digest in seen_hashes and seen_hashes[digest][0] != split:
                    raise ValueError(f'Identical files appear in different splits: {seen_hashes[digest][1]} and {relative}')
                seen_hashes[digest] = (split, relative.as_posix())
                record['sha256'] = digest
            records.append(record)
            count += 1
        if not count:
            raise ValueError(f'No videos found in split: {split}')
    return {'schema': 1, 'purpose': 'CMD experimental dataset audit', 'dataset_root': str(root),
            'rights_declarations': list(rights.values()), 'files': records, 'warnings': warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, help='Dataset directory with train/val/test and provenance.csv')
    parser.add_argument('--ffprobe', required=True, help='Path to the bundled ffprobe executable')
    parser.add_argument('--output', required=True, help='New manifest path; existing files are not overwritten')
    parser.add_argument('--hash', action='store_true', help='Hash files and reject exact duplicates across splits (slow)')
    args = parser.parse_args(argv)
    try:
        result = audit(args.root, args.ffprobe, args.hash)
        output = Path(args.output).expanduser().resolve()
        if output.is_relative_to(Path(args.root).expanduser().resolve()):
            raise ValueError('Save the audit manifest outside the original dataset folder')
        with output.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        print(f"Audited {len(result['files'])} videos. Manifest: {output}")
        for warning in result['warnings']:
            print('Warning:', warning)
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f'Audit failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
