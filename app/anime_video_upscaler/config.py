"""One typed, serializable source for basic and advanced controls."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".m2ts", ".wmv"}
ENGINES = ("realesrgan", "realcugan", "libplacebo", "rife", "none")
MODELS = ("realesr-animevideov3", "realesrgan-plus-anime", "realesrgan-plus", "realesr-generalv3")
SHADERS = ("anime4k-v4-a", "anime4k-v4-a+a", "anime4k-v4-b", "anime4k-v4-b+b", "anime4k-v4-c", "anime4k-v4-c+a", "anime4k-v4.1-gan")
RIFE_MODELS = ("rife-v4.26", "rife-v4.25", "rife-v4.25-lite", "rife-v4.6", "rife-v4", "rife-anime", "rife", "rife-HD", "rife-UHD", "rife-v2", "rife-v2.3", "rife-v2.4", "rife-v3.0", "rife-v3.1")
CPU_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
CODECS = ("libx264", "libx265", "h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_amf", "hevc_amf", "av1_amf", "libsvtav1")


def encoder_presets(codec: str) -> tuple[str, ...]:
    if codec.endswith("_nvenc"):
        return tuple(f"p{i}" for i in range(1, 8))
    if codec.endswith("_amf"):
        return ("speed", "balanced", "quality", "high_quality")
    if codec == "libsvtav1":
        return tuple(str(i) for i in range(14))
    return CPU_PRESETS


def quality_maximum(codec: str) -> int:
    # FFmpeg's AMF AV1 QP range is wider than H.264/HEVC and NVENC CQ.
    return 255 if codec == "av1_amf" else 63 if codec in ("av1_nvenc", "libsvtav1") else 51


def numeric_ranges(codec: str) -> dict:
    ranges = {"gpu": (0, 32), "quality": (0, quality_maximum(codec)), "noise": (-1, 3), "audio_bitrate": (32000, 512000),
              "width": (2, 16384), "height": (2, 16384), "cugan_threads": (1, 256),
              "cugan_syncgap": (0, 3), "fps_multiplier": (2, 8), "scene_threshold": (0, 100),
              "watermark_width": (8, 4096), "watermark_margin": (0, 2000), "watermark_opacity": (0.01, 1),
              "watermark_x": (0, 1), "watermark_y": (0, 1), "thread_count": (0, 256),
              "qmin": (-1, 69), "qmax": (-1, 69)}
    ranges.update({name: (0, 2_000_000_000) for name in ("bit_rate", "rc_buffer_size", "rc_min_rate", "rc_max_rate")})
    ranges.update({name: (-1, 10000) for name in ("gop_size", "max_b_frames", "keyint_min", "refs")})
    return ranges


def default_encoder_preset(codec: str) -> str:
    if codec.endswith("_nvenc"):
        return "p6"
    if codec.endswith("_amf"):
        return "quality"
    return "6" if codec == "libsvtav1" else "medium"


@dataclass(frozen=True)
class Settings:
    engine: str = "realesrgan"
    model: str = "realesr-animevideov3"
    scale: int = 2
    gpu: int = 0
    noise: int = 0
    cugan_model: str = "models-se"
    cugan_threads: int = 1
    cugan_syncgap: int = 3
    shader: str = "anime4k-v4-a"
    rife_model: str = "rife-v4.26"
    fps_multiplier: int = 2
    scene_threshold: float = 20.0
    rife_uhd: bool = False
    hwaccel: str = "none"
    recover_shutdown: bool = False
    resume_segments: bool = False
    target: str = "4k"
    width: int = 3840
    height: int = 2160
    aspect: str = "pad"
    codec: str = "libx264"
    quality: int = 20
    preset: str = "medium"
    pixel_format: str = "yuv420p"
    container: str = "mkv"
    audio: str = "copy"
    audio_bitrate: int = 192000
    restoration: str = "off"
    copy_subtitles: bool = True
    subtitle_mode: str = "off"
    subtitle_path: str = ""
    subtitle_language: str = "tur"
    watermark_path: str = ""
    watermark_corner: str = "bottom-right"
    watermark_x: float = 0.5
    watermark_y: float = 0.5
    watermark_width: int = 160
    watermark_margin: int = 24
    watermark_opacity: float = 0.75
    flicker: str = "off"
    bit_rate: int = 0
    rc_buffer_size: int = 0
    rc_min_rate: int = 0
    rc_max_rate: int = 0
    qmin: int = -1
    qmax: int = -1
    gop_size: int = -1
    max_b_frames: int = -1
    keyint_min: int = -1
    refs: int = -1
    thread_count: int = 0
    extra_options: str = ""
    output_dir: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> Settings:
        if not isinstance(raw, dict):
            raise ValueError("Settings must be a JSON object")
        defaults = cls()
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
        for key, value in raw.items():
            expected = type(getattr(defaults, key))
            if expected is float and type(value) in (int, float):
                continue
            if type(value) is not expected:
                raise ValueError(f"{key}: expected {expected.__name__}")
        settings = cls(**raw)
        settings.validate()
        return settings

    def validate(self) -> None:
        choices = {"engine": ENGINES, "model": MODELS, "scale": (2, 3, 4),
                   "cugan_model": ("models-se", "models-pro", "models-nose"),
                   "rife_model": RIFE_MODELS, "target": ("native", "2k", "4k", "custom"),
                   "aspect": ("pad", "fit"), "codec": CODECS,
                   "container": ("mkv", "mp4"), "audio": ("copy", "aac", "off"),
                   "subtitle_mode": ("off", "burn", "soft"), "subtitle_language": ("tur", "eng", "jpn", "deu", "fra", "spa", "ita", "por", "kor", "zho", "ara", "rus", "und"), "flicker": ("off", "mild"),
                   "restoration": ("off", "mild"),
                   "watermark_corner": ("top-left", "top-right", "bottom-left", "bottom-right", "free"),
                   "hwaccel": ("none", "cuda", "d3d11va", "dxva2", "vulkan", "qsv")}
        for name, options in choices.items():
            if getattr(self, name) not in options:
                raise ValueError(f"{name}: unsupported value")
        ranges = numeric_ranges(self.codec)
        for name, (low, high) in ranges.items():
            if not low <= getattr(self, name) <= high:
                raise ValueError(f"{name}: expected {low}–{high}")
        if self.target == "custom" and (self.width % 2 or self.height % 2):
            raise ValueError("Target width and height must be even")
        if self.engine == "realesrgan" and self.model != "realesr-animevideov3" and self.scale != 4:
            raise ValueError("This Real-ESRGAN model requires 4× scaling")
        if self.resume_segments and (self.engine not in ("realesrgan", "realcugan", "libplacebo", "rife") or self.restoration != "off"):
            raise ValueError("Resume by segments requires an AI engine and denoise off")
        if self.engine == "realcugan":
            if self.cugan_model == "models-nose" and (self.noise != 0 or self.scale != 2):
                raise ValueError("models-nose requires scale 2 and noise 0")
            if self.cugan_model == "models-pro" and (self.scale == 4 or self.noise not in (-1, 0, 3)):
                raise ValueError("models-pro supports scales 2/3 and noise -1/0/3")
            if self.cugan_model == "models-se" and self.scale != 2 and self.noise in (1, 2):
                raise ValueError("models-se noise 1/2 requires scale 2")
        if self.preset not in encoder_presets(self.codec):
            raise ValueError(f"{self.codec}: incompatible encoder preset {self.preset}")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.pixel_format):
            raise ValueError("Invalid pixel format")
        if self.rc_min_rate and self.rc_max_rate and self.rc_min_rate > self.rc_max_rate:
            raise ValueError("Minimum bitrate exceeds maximum bitrate")
        if self.qmin >= 0 and self.qmax >= 0 and self.qmin > self.qmax:
            raise ValueError("Minimum quantizer exceeds maximum quantizer")
        extra_encoder_options(self.extra_options)

    def export(self) -> dict:
        return asdict(self)


def extra_encoder_options(text: str) -> dict[str, str]:
    # Restrict to codec options: never allow input/output/filter/map injection.
    allowed = {"tune", "profile", "level", "rc", "rc-lookahead", "spatial-aq", "temporal-aq",
               "aq-strength", "b_ref_mode", "bf", "x264-params", "x265-params", "svtav1-params"}
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.strip().partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in allowed or not value or key in values or "\x00" in value:
            raise ValueError(f"Unsupported, duplicate or empty encoder option: {key}")
        if key == "bf":
            raise ValueError("Use the shared max_b_frames control instead of bf")
        if key.endswith("-params"):
            shared = {"crf", "qp", "preset", "bitrate", "vbv-bufsize", "vbv-maxrate", "keyint",
                      "min-keyint", "bframes", "ref", "fps", "input-res", "input-csp", "threads"}
            if any(part.partition("=")[0].strip() in shared for part in value.split(":")):
                raise ValueError("Use shared controls for quality, bitrate, GOP, timing and thread settings")
        values[key] = value
    return values


def config_dir() -> Path:
    if os.environ.get("ANIME_UPSCALER_HOME"):
        return Path(os.environ["ANIME_UPSCALER_HOME"])
    return Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "AnimeVideoUpscalerEncoder"


def legacy_config_path() -> Path | None:
    # Test or explicitly redirected profiles must not read a real user profile.
    if os.environ.get("ANIME_UPSCALER_HOME"):
        return None
    return Path(os.environ.get("APPDATA", str(Path.home() / ".config"))) / "AnimeUpscaler7" / "settings.json"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    if path.stat().st_size > 2_000_000:
        raise ValueError("Configuration file is too large")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def preset_document(name: str, settings: Settings) -> dict:
    settings.validate()
    if not name.strip() or len(name) > 100:
        raise ValueError("Preset name must contain 1–100 characters")
    # Device paths and output locations are deliberately not portable preset data.
    portable = settings.export()
    for key in ("subtitle_path", "watermark_path", "output_dir"):
        portable[key] = ""
    return {"schema": 1, "name": name.strip(), "settings": portable}


def load_preset(raw: dict) -> tuple[str, Settings]:
    if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("name"), str):
        raise ValueError("Unsupported preset schema")
    if not raw["name"].strip() or len(raw["name"]) > 100:
        raise ValueError("Invalid preset name")
    return raw["name"], Settings.from_dict(raw["settings"])


def runtime_root() -> Path:
    # A frozen onedir app stores the runtime beside its main executable. Source
    # development uses the checkout root. No other application's install is searched.
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def detect_tools() -> dict[str, str]:
    root = runtime_root() / "runtime"
    dev_engine = root / "native-engine" / "avue-engine.exe"
    engine = dev_engine if not getattr(sys, "frozen", False) and dev_engine.is_file() else root / "engine" / "avue-engine.exe"
    if not engine.is_file():
        engine = root / "engine" / "video2x.exe"  # Compatibility with existing development runtimes.
    candidates = {"video2x": engine,
                  "ffmpeg": root / "ffmpeg" / "ffmpeg.exe",
                  "ffprobe": root / "ffmpeg" / "ffprobe.exe"}
    return {name: str(path) if path.is_file() else "" for name, path in candidates.items()}


FACTORY_PRESETS = {
    "Anime · Max quality / small file · CPU HEVC": replace(Settings(), scale=4, codec="libx265", quality=20, preset="slow"),
    "Anime · Max quality / small file · NVIDIA HEVC": replace(Settings(), scale=4, codec="hevc_nvenc", quality=21, preset="p7", extra_options="rc-lookahead=16\nspatial-aq=1"),
    "Anime · Max quality / small file · AMD HEVC": replace(Settings(), scale=4, codec="hevc_amf", quality=21, preset="quality"),
    "Video · Max quality / small file · CPU HEVC": replace(Settings(), model="realesrgan-plus", scale=4, codec="libx265", quality=20, preset="slow"),
    "Video · Max quality / small file · NVIDIA HEVC": replace(Settings(), model="realesrgan-plus", scale=4, codec="hevc_nvenc", quality=21, preset="p7", extra_options="rc-lookahead=16\nspatial-aq=1"),
    "Video · Max quality / small file · AMD HEVC": replace(Settings(), model="realesrgan-plus", scale=4, codec="hevc_amf", quality=21, preset="quality"),
    "Anime · 4K balanced": Settings(),
    "Anime · NVIDIA HEVC": replace(Settings(), codec="hevc_nvenc", preset="p6", quality=22),
    "Anime · native 2×": replace(Settings(), target="native"),
    "Resize only · 4K": replace(Settings(), engine="none"),
    "Anime · 4K high quality CPU": replace(Settings(), quality=18, preset="slow"),
    "Anime · 4K NVIDIA HEVC": replace(Settings(), codec="hevc_nvenc", preset="p6", quality=20),
    "Anime · QHD balanced CPU": replace(Settings(), target="2k", quality=20),
    "Anime · QHD NVIDIA HEVC": replace(Settings(), target="2k", codec="hevc_nvenc", preset="p5", quality=22),
    "Anime · 1080p compatible CPU": replace(Settings(), target="custom", width=1920, height=1080, preset="fast", container="mp4", audio="aac", copy_subtitles=False),
    "Anime · 4K Real-CUGAN CPU": replace(Settings(), engine="realcugan", cugan_model="models-se", noise=0),
    "Anime · Real-CUGAN accurate CPU": replace(Settings(), engine="realcugan", cugan_model="models-se", noise=0, cugan_syncgap=1, codec="libx265", preset="slow", quality=18),
    "Anime · Real-CUGAN accurate NVIDIA": replace(Settings(), engine="realcugan", cugan_model="models-se", noise=0, cugan_syncgap=1, codec="hevc_nvenc", preset="p6", quality=20),
    "Anime · 480p to 4K detail NVIDIA": replace(Settings(), scale=4, codec="hevc_nvenc", preset="p5", quality=20),
    "Video · source quality": replace(Settings(), engine="none", target="native", quality=18, preset="slow"),
    "Video · low bitrate cleanup": replace(Settings(), engine="none", target="native", quality=18, preset="slow", restoration="mild"),
    "Video · 1080p compatible": replace(Settings(), engine="none", target="custom", width=1920, height=1080, quality=20, preset="fast", container="mp4", audio="aac", copy_subtitles=False),
    "Video · 4K general AI NVIDIA": replace(Settings(), model="realesrgan-plus", scale=4, codec="hevc_nvenc", preset="p5", quality=20),
    "Video · 4K general AI CPU encode": replace(Settings(), model="realesrgan-plus", scale=4, quality=18, preset="slow"),
    "Anime · 4K detail CPU HEVC": replace(Settings(), scale=4, codec="libx265", quality=18, preset="slow"),
    "Anime · 4K detail NVIDIA HEVC": replace(Settings(), scale=4, codec="hevc_nvenc", quality=18, preset="p7", extra_options="rc-lookahead=16\nspatial-aq=1"),
    "Anime · 4K detail NVIDIA AV1": replace(Settings(), scale=4, codec="av1_nvenc", quality=20, preset="p6"),
    "Anime · 4K detail AMD HEVC": replace(Settings(), scale=4, codec="hevc_amf", quality=20, preset="quality"),
    "Anime · 4K detail AMD AV1": replace(Settings(), scale=4, codec="av1_amf", quality=20, preset="quality"),
    "Anime · QHD AMD HEVC": replace(Settings(), target="2k", codec="hevc_amf", quality=20, preset="quality"),
    "Video · 4K detail CPU HEVC": replace(Settings(), model="realesrgan-plus", scale=4, codec="libx265", quality=18, preset="slow"),
    "Video · 4K detail NVIDIA HEVC": replace(Settings(), model="realesrgan-plus", scale=4, codec="hevc_nvenc", quality=18, preset="p7", extra_options="rc-lookahead=16\nspatial-aq=1"),
    "Video · 4K detail NVIDIA AV1": replace(Settings(), model="realesrgan-plus", scale=4, codec="av1_nvenc", quality=20, preset="p6"),
    "Video · 4K detail AMD HEVC": replace(Settings(), model="realesrgan-plus", scale=4, codec="hevc_amf", quality=20, preset="quality"),
    "Video · 4K detail AMD AV1": replace(Settings(), model="realesrgan-plus", scale=4, codec="av1_amf", quality=20, preset="quality"),
    "Video · source quality AMD H.264": replace(Settings(), engine="none", target="native", codec="h264_amf", quality=18, preset="quality"),
}

FACTORY_PRESET_DESCRIPTIONS = {
    **{name: "4K · 4× AI · HEVC quality/size tradeoff. Output size and quality vary by source; compare a short sample. AI requires a Vulkan GPU; hardware encoding requires the matching GPU." for name in FACTORY_PRESETS if "Max quality / small file" in name},
    "Anime · 4K balanced": "4K UHD · Real-ESRGAN 2× · H.264 CPU · CRF 20. A compatible starting point.",
    "Anime · NVIDIA HEVC": "4K UHD · Real-ESRGAN 2× · NVIDIA HEVC · CQ 22. Efficient output on supported NVIDIA hardware.",
    "Anime · native 2×": "Real-ESRGAN 2× at its natural output size · H.264 CPU · CRF 20. No fixed 4K canvas.",
    "Resize only · 4K": "4K UHD · no AI · H.264 CPU · CRF 20. Resize and encode without the AI engine.",
    "Anime · 4K high quality CPU": "4K UHD · Real-ESRGAN 2× · H.264 CPU · CRF 18 / slow. Larger files and longer encoding for a high-quality starting point.",
    "Anime · 4K NVIDIA HEVC": "4K UHD · Real-ESRGAN 2× · NVIDIA HEVC · CQ 20 / p6. Requires a supported NVIDIA GPU.",
    "Anime · QHD balanced CPU": "QHD 2560×1440 · Real-ESRGAN 2× · H.264 CPU · CRF 20. Balanced and compatible.",
    "Anime · QHD NVIDIA HEVC": "QHD 2560×1440 · Real-ESRGAN 2× · NVIDIA HEVC · CQ 22 / p5. Requires a supported NVIDIA GPU.",
    "Anime · 1080p compatible CPU": "1080p · H.264 CPU · MP4 / AAC · CRF 20. Source subtitles are not retained; external subtitle settings can still be chosen.",
    "Anime · 4K Real-CUGAN CPU": "4K UHD · Real-CUGAN 2× / no denoise · H.264 CPU · CRF 20. Compare fine line details against Real-ESRGAN.",
    "Anime · Real-CUGAN accurate CPU": "4K · Real-CUGAN 2× · accurate tile sync · CPU HEVC CRF 18 / slow. Uses extra disk; compare with faster sync modes.",
    "Anime · Real-CUGAN accurate NVIDIA": "4K · Real-CUGAN 2× · accurate tile sync · NVIDIA HEVC CQ 20 / p6. Requires NVIDIA encoding; uses extra disk.",
    "Anime · 480p to 4K detail NVIDIA": "4K UHD · Real-ESRGAN anime 4× · NVIDIA HEVC · CQ 20. For low-resolution anime; slower and uses more memory and disk. Compare with 2× on a short sample.",
    "Video · source quality": "Original dimensions · no AI · H.264 CRF 18. Helps limit further encoding loss; cannot restore missing source detail.",
    "Video · low bitrate cleanup": "Original dimensions · mild denoise · H.264 CRF 18. May smooth compression noise but can remove detail; compare a short clip.",
    "Video · 1080p compatible": "1080p · no AI · H.264 / AAC · MP4. Widely compatible; source bitmap subtitles are not retained.",
    "Video · 4K general AI NVIDIA": "4K UHD · Real-ESRGAN general 4× · NVIDIA HEVC CQ 20. For live-action footage; slower and memory intensive.",
    "Video · 4K general AI CPU encode": "4K UHD · Real-ESRGAN general 4× · H.264 CRF 18. AI still requires Vulkan GPU; CPU handles the final encode.",
    "Anime · 4K detail CPU HEVC": "4K · anime AI 4× · CPU HEVC CRF 18 / slow. High-quality starting point; AI requires a Vulkan GPU and can use much disk space.",
    "Anime · 4K detail NVIDIA HEVC": "4K · anime AI 4× · NVIDIA HEVC CQ 18 / p7 with lookahead and spatial AQ. Slow; compare detail and grain on a short clip.",
    "Anime · 4K detail NVIDIA AV1": "4K · anime AI 4× · NVIDIA AV1 CQ 20 / p6. Requires an AV1-capable NVIDIA GPU and compatible playback devices.",
    "Anime · 4K detail AMD HEVC": "4K · anime AI 4× · AMD HEVC CQP 20 / quality. Requires a compatible AMD GPU and driver; AMD hardware testing is pending.",
    "Anime · 4K detail AMD AV1": "4K · anime AI 4× · AMD AV1 CQP 20 / quality. Requires AV1-capable AMD hardware; AMD hardware testing is pending.",
    "Anime · QHD AMD HEVC": "QHD · anime AI 2× · AMD HEVC CQP 20 / quality. Requires a compatible AMD GPU and driver; AMD hardware testing is pending.",
    "Video · 4K detail CPU HEVC": "4K · general AI 4× · CPU HEVC CRF 18 / slow. AI needs a Vulkan GPU; slower encoding can improve efficiency.",
    "Video · 4K detail NVIDIA HEVC": "4K · general AI 4× · NVIDIA HEVC CQ 18 / p7 with lookahead and spatial AQ. Compare motion, texture and file size.",
    "Video · 4K detail NVIDIA AV1": "4K · general AI 4× · NVIDIA AV1 CQ 20 / p6. Requires AV1-capable NVIDIA hardware and playback support.",
    "Video · 4K detail AMD HEVC": "4K · general AI 4× · AMD HEVC CQP 20 / quality. Requires a compatible AMD GPU and driver; AMD hardware testing is pending.",
    "Video · 4K detail AMD AV1": "4K · general AI 4× · AMD AV1 CQP 20 / quality. Requires AV1-capable AMD hardware; AMD hardware testing is pending.",
    "Video · source quality AMD H.264": "Source dimensions · no AI · AMD H.264 CQP 18 / quality. Prevents unnecessary scaling; AMD hardware testing is pending.",
}
