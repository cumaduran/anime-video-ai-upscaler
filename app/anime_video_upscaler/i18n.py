"""English source strings, JSON catalogs, and fallback for incomplete new locales."""
import json
from pathlib import Path
from .helptext import HELP

CATALOGS = Path(__file__).with_name("locales")


class Translator:
    def __init__(self, language="en"):
        self.language = language
        self.messages = {}
        self.help_messages = {}
        path = CATALOGS / f"{language}.json"
        if path.is_file():
            catalog = json.loads(path.read_text(encoding="utf-8"))
            self.messages = catalog["messages"]
            self.help_messages = catalog.get("help", {})

    def __call__(self, source):
        return self.messages.get(source, {"stream": "AI + encoding"}.get(source, source))

    def help(self, key):
        if self.language == "tr":
            value = HELP[key][1]
        else:
            value = self.help_messages.get(key, HELP[key][0])
        if key == "extra_options":
            value += "\n\n" + self("Supported options") + ": tune, profile, level, rc, rc-lookahead, spatial-aq, temporal-aq, aq-strength, b_ref_mode, x264-params, x265-params, svtav1-params."
        return value


def available_languages():
    result = [("en", "English")]
    for path in sorted(CATALOGS.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if path.stem != "en" and isinstance(data.get("name"), str):
                result.append((path.stem, data["name"]))
        except (OSError, ValueError):
            continue
    return result
