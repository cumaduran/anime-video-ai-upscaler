"""Prepare external text subtitles without changing the user's original file."""
from __future__ import annotations

import codecs
from pathlib import Path
import re


def read_subtitle(source: Path) -> tuple[str, str]:
    """Decode text for FFmpeg/libass; legacy Turkish releases use Windows-1254.

    Honor Unicode BOMs first, then strict UTF-8. CP1254 is an explicit fallback
    for legacy Turkish/Western subtitles, not a universal encoding detector.
    Some files combine CP1254 and UTF-8 on separate lines; recover those only
    when the whole file fails both decoders, and require every line to decode.
    Never discard undecodable bytes or substitute replacement characters.
    """
    data = source.read_bytes()
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8-sig"
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError as error:
        if encoding != "utf-8-sig" or data.startswith(codecs.BOM_UTF8):
            raise ValueError("Invalid Unicode subtitle file; save it as UTF-8") from error
        encoding = "cp1254"
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as error:
            try:
                decoded_lines = []
                for line in data.splitlines(keepends=True):
                    try:
                        decoded_lines.append(line.decode("utf-8"))
                    except UnicodeDecodeError:
                        decoded_lines.append(line.decode("cp1254"))
                text = "".join(decoded_lines)
                encoding = "mixed UTF-8 / cp1254 (line by line)"
            except UnicodeDecodeError:
                raise ValueError("Unsupported subtitle encoding; save the file as UTF-8") from error
    if not text.strip() or any(ord(c) < 32 and c not in "\r\n\t" for c in text):
        raise ValueError("Invalid text subtitle file; save it as UTF-8")
    if source.suffix.lower() == ".srt":
        lines = [line for line in text.splitlines() if "-->" in line]
        pattern = re.compile(r"^\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?\s*$")
        if not lines:
            raise ValueError("Invalid SRT subtitle: no cue timestamps found")
        for line in lines:
            match = pattern.fullmatch(line)
            if not match:
                raise ValueError("Invalid SRT subtitle timestamp: " + line[:120])
            h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, match.groups())
            if max(m1, m2, s1, s2) >= 60 or (h1, m1, s1, ms1) >= (h2, m2, s2, ms2):
                raise ValueError("Invalid SRT subtitle timing: " + line[:120])
    return text, encoding


def stage_subtitle(source: Path, destination: Path) -> str:
    text, encoding = read_subtitle(source)
    destination.write_bytes(text.encode("utf-8"))
    return encoding


def match_subtitles(sources, folder: Path):
    """Suggest only unique matches, never assign one file to multiple videos."""
    candidates = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".srt", ".ass", ".ssa", ".vtt"}), key=lambda p: p.name.casefold())
    def normalized(path):
        return re.sub(r"[^\w]", "", path.stem.casefold())
    def episode(path):
        numbers = re.findall(r"\d+", path.stem)
        return int(numbers[-1]) if numbers else None
    suggestions = {}
    ambiguous = []
    for source in sources:
        name = normalized(source)
        exact = [p for p in candidates if normalized(p) == name or (len(name) >= 5 and normalized(p).endswith(name))]
        options = exact or [p for p in candidates if episode(source) is not None and episode(p) == episode(source)]
        if len(options) == 1:
            suggestions[source] = options[0]
        else:
            ambiguous.append(source)
    duplicates = {p for p in suggestions.values() if list(suggestions.values()).count(p) > 1}
    for source, subtitle in list(suggestions.items()):
        if subtitle in duplicates:
            del suggestions[source]
            ambiguous.append(source)
    return suggestions, ambiguous
