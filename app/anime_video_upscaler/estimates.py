"""Session-only estimates from completed, comparable jobs."""
import json
import math


def comparison_key(settings, media):
    if media is None or media.duration <= 0 or media.fps <= 0 or settings.resume_segments:
        return None
    values = settings.export().copy()
    values.pop("output_dir", None)
    return (json.dumps(values, sort_keys=True), media.width, media.height,
            round(media.sar, 5), round(media.fps, 3), media.hdr,
            media.audio_codecs, media.subtitle_codecs)


def add_observation(history, settings, media, elapsed, output_bytes):
    key = comparison_key(settings, media)
    if key is None or not math.isfinite(elapsed) or elapsed <= 0 or output_bytes <= 0:
        return
    history.append((key, elapsed / media.duration, output_bytes / media.duration))
    del history[:-50]


def estimate(history, settings, media):
    key = comparison_key(settings, media)
    if key is None:
        return None
    matches = [(seconds, size) for item_key, seconds, size in history if item_key == key]
    if not matches:
        return None
    times = [seconds * media.duration for seconds, _ in matches]
    sizes = [size * media.duration for _, size in matches]
    return min(times), max(times), min(sizes), max(sizes), len(matches)
