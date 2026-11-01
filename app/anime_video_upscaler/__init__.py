"""Anime Video Upscaler & Encoder desktop workflow, based on Video2X."""

APP_NAME = "Anime Video Upscaler & Encoder"
__version__ = "1.0.0"


def build_label():
    """Identify the packaged source snapshot without requiring Git at runtime."""
    import json
    import sys
    from pathlib import Path
    if not getattr(sys, "frozen", False):
        return "source"
    try:
        info = json.loads((Path(sys.executable).resolve().parent / "BUILD.json").read_text(encoding="utf-8-sig"))
        return f"{info['built_utc']} · {info['source_sha256'][:12]}"
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown build"
