"""Pure workflow planning; no shell commands and no model modifications."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import os
from pathlib import Path
import re

from .config import Settings, extra_encoder_options


@dataclass(frozen=True)
class Media:
    width: int
    height: int
    duration: float
    fps: float
    sar: float = 1.0
    video_index: int = 0
    subtitle_codecs: tuple[str, ...] = ()
    hdr: bool = False
    rotation: int = 0
    variable_rate: bool = False
    video_start_offset: float = 0.0
    audio_codecs: tuple[str, ...] = ()
    uncertain_hdr: bool = False

    @classmethod
    def from_probe(cls, data: dict) -> Media:
        video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
        if not video:
            raise ValueError("No video stream found")
        def ratio(value, fallback):
            try:
                result = float(Fraction(str(value).replace(":", "/")))
                return result if math.isfinite(result) and result > 0 else fallback
            except (ValueError, ZeroDivisionError):
                return fallback
        duration = ratio(video.get("duration", data.get("format", {}).get("duration", 0)), 0.0)
        # ffmpeg resets each input against its own container start time. Preserve
        # the source video's relative offset when AI uses a separate intermediate.
        video_start_offset = 0.0
        try:
            video_start = float(video["start_time"])
            format_start = float(data["format"]["start_time"])
            if math.isfinite(video_start) and math.isfinite(format_start):
                video_start_offset = video_start - format_start
        except (KeyError, TypeError, ValueError):
            pass
        rotation = int(float(video.get("tags", {}).get("rotate", 0)))
        for side in video.get("side_data_list", []):
            rotation = int(float(side.get("rotation", rotation)))
        width, height = int(video["width"]), int(video["height"])
        sar = ratio(video.get("sample_aspect_ratio", "1:1"), 1.0)
        if rotation % 180 == 90:
            width, height, sar = height, width, 1 / sar
        media = cls(width, height, duration,
                    ratio(video.get("avg_frame_rate"), ratio(video.get("r_frame_rate"), 0)),
                    sar, int(video["index"]),
                    tuple(s.get("codec_name", "unknown") for s in data.get("streams", []) if s.get("codec_type") == "subtitle"),
                    video.get("color_transfer") in ("smpte2084", "arib-std-b67"), rotation,
                    abs(ratio(video.get("avg_frame_rate"),0) - ratio(video.get("r_frame_rate"),0)) > 0.1,
                    video_start_offset,
                    tuple(s.get("codec_name", "unknown") for s in data.get("streams", []) if s.get("codec_type") == "audio"),
                    (video.get("color_space") in ("bt2020nc", "bt2020c") or video.get("color_primaries") == "bt2020")
                    and video.get("color_transfer") in (None, "unknown", "unspecified"))
        if media.width < 1 or media.height < 1:
            raise ValueError("Invalid input dimensions")
        return media


def target_size(s: Settings, media: Media) -> tuple[int, int]:
    if s.target == "4k":
        return 3840, 2160
    if s.target == "2k":
        return 2560, 1440
    if s.target == "custom":
        return s.width, s.height
    factor = s.scale if s.engine in ("realesrgan", "realcugan", "libplacebo") else 1
    return max(2, round(media.width * media.sar * factor / 2) * 2), max(2, round(media.height * factor / 2) * 2)


def fitted_size(s: Settings, media: Media) -> tuple[int, int]:
    width, height = target_size(s, media)
    ratio = media.width * media.sar / media.height
    if width / height > ratio:
        width = max(2, int(height * ratio / 2) * 2)
    else:
        height = max(2, int(width / ratio / 2) * 2)
    return width, height


def lossless_working_bytes(s: Settings, media: Media) -> int | None:
    """Uncompressed pixel-data reference, not a prediction of compressed FFV1 size."""
    if s.engine == "none" or media.duration <= 0 or media.fps <= 0:
        return None
    frames = math.ceil(media.duration * media.fps)
    source_pixels = media.width * media.height
    if s.engine in ("realesrgan", "realcugan"):
        output_pixels = source_pixels * s.scale * s.scale
        output_frames = frames
    elif s.engine == "libplacebo":
        width, height = fitted_size(s, media)
        output_pixels = max(2, round(width / media.sar / 2) * 2) * height
        output_frames = frames
    else:
        output_pixels = source_pixels
        output_frames = frames * s.fps_multiplier
    # Real-CUGAN's reference backend temporarily keeps the upscaled PNG set
    # alongside the assembled FFV1 output. This is only a raw-pixel reference;
    # actual compression and container overhead vary.
    output_copies = 2 if s.engine == "realcugan" else 1
    return 4 * (frames * source_pixels + output_copies * output_frames * output_pixels)


def validate_media(s: Settings, media: Media) -> None:
    if media.uncertain_hdr:
        raise ValueError("BT.2020 input has no reliable transfer metadata; normalize a copy before processing")
    if media.rotation % 90:
        raise ValueError("Only 90-degree rotation metadata is supported")
    if s.engine != "none" and media.video_index != 0:
        raise ValueError("AI processing currently requires the primary video at stream 0; remux a copy first")
    if s.engine != "none" and media.fps <= 0:
        raise ValueError("Cannot determine frame rate for the AI stage")
    if s.engine != "none" and media.variable_rate:
        raise ValueError("Input frame-rate metadata indicates VFR. Normalize a copy to CFR before AI processing")
    if s.container == "mp4" and s.audio == "copy":
        supported = {"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac", "truehd"}
        unsupported = sorted(set(media.audio_codecs) - supported)
        if unsupported:
            raise ValueError("MP4 cannot safely copy source audio (" + ", ".join(unsupported) + "); choose AAC audio or MKV")
    text_codecs = {"subrip", "ass", "ssa", "webvtt", "mov_text", "text"}
    if s.container == "mp4" and s.copy_subtitles and any(c not in text_codecs for c in media.subtitle_codecs):
        raise ValueError("Bitmap subtitles cannot be copied to MP4; select MKV or disable source subtitles")


def ai_command(s: Settings, tools: dict, source: Path, intermediate: Path, media: Media, help_text: str) -> list[str]:
    s.validate()
    cmd = [tools["video2x"], "-i", str(source), "-o", str(intermediate), "-p", s.engine,
           "-d", str(s.gpu), "-c", "ffv1", "--pix-fmt", "bgr0"]
    # FFV1 preserves the RGB output without an additional chroma subsampling pass.
    if "--no-copy-audio-streams" in help_text and "--no-copy-subtitle-streams" in help_text:
        cmd += ["--no-copy-audio-streams", "--no-copy-subtitle-streams"]
    elif "--no-copy-streams" in help_text:
        cmd += ["--no-copy-streams"]
    else:
        raise ValueError("Unsupported Video2X CLI: no stream-copy controls")
    if s.hwaccel != "none":
        cmd += ["-a", s.hwaccel]
    if s.engine == "realesrgan":
        # Old bundled releases do not accept every model in the source tree.
        # Fail before lossless decoding rather than after a long preparation.
        if "--realesrgan-model" in help_text and s.model not in help_text:
            raise ValueError(f"Bundled AI engine does not support model {s.model}; choose a supported model or update the engine")
        cmd += ["-s", str(s.scale), "--realesrgan-model", s.model, "-n", str(s.noise)]
    elif s.engine == "realcugan":
        if s.noise == -1 and "--no-copy-streams" in help_text:
            raise ValueError("Conservative noise -1 needs a newer Video2X build")
        cmd += ["-s", str(s.scale), "-n", str(s.noise), "--realcugan-model", s.cugan_model,
                "--realcugan-threads", str(s.cugan_threads), "--realcugan-syncgap", str(s.cugan_syncgap)]
    elif s.engine == "libplacebo":
        width, height = fitted_size(s, media)
        # The engine retains input SAR. Scale coded width accordingly until final normalization.
        width = max(2, round(width / media.sar / 2) * 2)
        cmd += ["-w", str(width), "-h", str(height), "--libplacebo-shader", s.shader]
    elif s.engine == "rife":
        cmd += ["-m", str(s.fps_multiplier), "-t", str(s.scene_threshold), "--rife-model", s.rife_model]
        if s.rife_uhd:
            cmd += ["--rife-uhd"]
    return cmd


def hdr_to_sdr_filters(media: Media) -> list[str]:
    """Convert tagged HDR to SDR before 8-bit processing; never claim HDR output."""
    if not media.hdr:
        return []
    return ["zscale=transfer=linear:npl=100", "format=gbrpf32le", "zscale=primaries=bt709",
            "tonemap=tonemap=hable:desat=0", "zscale=transfer=bt709:matrix=bt709:range=tv", "format=yuv420p"]


def video_filter_graph(s: Settings, media: Media, video_selector: str,
                       watermark_index: int | None, subtitle_name: str,
                       *, preview: bool = False, timestamp: float = 0.0) -> str:
    """Shared placement and sizing for final output and source-frame preview."""
    width, height = fitted_size(s, media)
    filters = hdr_to_sdr_filters(media) if preview or s.engine == "none" else []
    if preview and subtitle_name:
        # Input seeking resets timestamps; restore the timeline for subtitle timing.
        filters.append(f"setpts=PTS+{max(0.0, timestamp):.6f}/TB")
    if not preview and s.engine == "none" and s.restoration == "mild":
        filters.append("hqdn3d=1.5:1.5:3:3")
    if not preview and s.engine == "rife":
        filters.append(f"tpad=stop_mode=clone:stop={s.fps_multiplier - 1}")
    if not preview and s.flicker == "mild":
        filters.append("deflicker=size=3:mode=am")
    filters += [f"scale={width}:{height}:flags=lanczos", "setsar=1"]
    if media.hdr:
        filters.append("setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709")
        filters += [f"sidedata=mode=delete:type={kind}" for kind in
                    ("MASTERING_DISPLAY_METADATA", "CONTENT_LIGHT_LEVEL", "DYNAMIC_HDR_PLUS", "DOVI_RPU_BUFFER", "DOVI_METADATA", "DYNAMIC_HDR_VIVID")]
    if s.aspect == "pad":
        tw, th = target_size(s, media)
        filters.append(f"pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color=black")
    if subtitle_name and s.subtitle_mode == "burn":
        filters.append(f"subtitles=filename={subtitle_name}")
    graph = f"[{video_selector}]" + ",".join(filters) + "[base]"
    if watermark_index is not None:
        margin = s.watermark_margin
        if s.watermark_corner == "free":
            x = f"(main_w-overlay_w)*{s.watermark_x:.6f}"
            y = f"(main_h-overlay_h)*{s.watermark_y:.6f}"
        else:
            x = str(margin) if s.watermark_corner.endswith("left") else f"main_w-overlay_w-{margin}"
            y = str(margin) if s.watermark_corner.startswith("top") else f"main_h-overlay_h-{margin}"
        graph += (f";[{watermark_index}:v]scale={min(s.watermark_width, width)}:-1,format=rgba,"
                  f"colorchannelmixer=aa={s.watermark_opacity}[wm];"
                  f"[base][wm]overlay=x={x}:y={y}:eof_action=repeat:repeatlast=1[outv]")
    else:
        graph += ";[base]null[outv]"
    return graph


def preview_command(s: Settings, tools: dict, source: Path, media: Media, timestamp: float,
                    subtitle_name: str = "", watermark_name: str = "") -> list[str]:
    # A source preview must not be blocked by AI, encoder, VFR or container
    # restrictions that apply only when the queue is processed.
    if media.uncertain_hdr:
        raise ValueError("BT.2020 input has no reliable transfer metadata")
    if media.rotation % 90:
        raise ValueError("Preview does not support non-right-angle rotation")
    if subtitle_name and not re.fullmatch(r"subtitle\.(srt|ass|ssa|vtt)", subtitle_name):
        raise ValueError("Invalid staged subtitle name")
    if watermark_name and watermark_name != "watermark.png":
        raise ValueError("Invalid staged watermark name")
    timestamp = max(0.0, min(timestamp, max(0.0, media.duration - 1 / (media.fps or 25)))) if media.duration else max(0.0,timestamp)
    cmd = [tools["ffmpeg"], "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{timestamp:.6f}",
           "-i", str(source)]
    wm_index = 1 if watermark_name else None
    if watermark_name:
        cmd += ["-i", watermark_name]
    graph = video_filter_graph(s, media, f"0:{media.video_index}", wm_index, subtitle_name,
                               preview=True, timestamp=timestamp)
    graph += ";[outv]scale=960:540:force_original_aspect_ratio=decrease:force_divisible_by=2[preview]"
    return cmd + ["-filter_complex", graph, "-map", "[preview]", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"]


def encoder_options(s: Settings) -> list[str]:
    """Identical final/compatibility-check encoder arguments, including rate control."""
    s.validate()
    cmd = ["-c:v", s.codec, "-pix_fmt", s.pixel_format, "-preset", s.preset]
    if s.bit_rate:
        if s.codec.endswith("_amf"):
            cmd += ["-rc", "vbr_peak"]
        cmd += ["-b:v", str(s.bit_rate)]
    elif s.codec.endswith("_nvenc"):
        cmd += ["-rc", "vbr", "-cq", str(s.quality), "-b:v", "0"]
    elif s.codec.endswith("_amf"):
        # AMF uses per-frame-type QP, not x264 CRF or NVENC CQ.
        cmd += ["-rc", "cqp", "-qp_i", str(s.quality), "-qp_p", str(s.quality)]
        if s.codec in ("h264_amf", "av1_amf"):
            cmd += ["-qp_b", str(s.quality)]
    else:
        cmd += ["-crf", str(s.quality)]
    for key, flag, minimum in (("rc_buffer_size", "bufsize", 0), ("rc_min_rate", "minrate", 0),
                               ("rc_max_rate", "maxrate", 0), ("gop_size", "g", -1),
                               ("max_b_frames", "bf", -1), ("keyint_min", "keyint_min", -1),
                               ("refs", "refs", -1), ("qmin", "qmin", -1), ("qmax", "qmax", -1),
                               ("thread_count", "threads", 0)):
        value = getattr(s, key)
        if value > minimum:
            cmd += [f"-{flag}:v", str(value)]
    for key, value in extra_encoder_options(s.extra_options).items():
        if key == "rc" and not s.bit_rate and s.codec.endswith(("_nvenc", "_amf")):
            raise ValueError("Custom rc requires explicit bitrate for GPU encoders")
        cmd += [f"-{key}", value]
    return cmd


def encoder_probe_command(s: Settings, tools: dict, media: Media | None = None) -> list[str]:
    """Probe real GPU initialization early; encoder listings alone prove nothing."""
    width, height = ((target_size(s, media) if s.aspect == "pad" else fitted_size(s, media)) if media else (256, 256))
    return [tools["ffmpeg"], "-hide_banner", "-nostdin", "-v", "error", "-f", "lavfi",
            "-i", f"color=black:s={width}x{height}:r=30", "-frames:v", "1", "-an"] + encoder_options(s) + ["-f", "null", "-"]


def validate_output_streams(s: Settings, source: dict, output: dict, external_subtitle: bool) -> None:
    """Check the streams that the final mapping is expected to preserve."""
    source_streams = source.get("streams", [])
    output_streams = output.get("streams", [])
    for kind, expected in (
        ("audio", sum(st.get("codec_type") == "audio" for st in source_streams) if s.audio != "off" else 0),
        ("subtitle", (sum(st.get("codec_type") == "subtitle" for st in source_streams) if s.copy_subtitles else 0)
         + int(external_subtitle and s.subtitle_mode == "soft")),
    ):
        actual = sum(st.get("codec_type") == kind for st in output_streams)
        if actual != expected:
            raise ValueError(f"Output {kind} stream count differs from expected ({actual} vs {expected}); output was not published")


def final_command(s: Settings, tools: dict, source: Path, video: Path, output: Path, media: Media,
                  subtitle_name: str = "", watermark_name: str = "") -> list[str]:
    """Sidecars are copied into the job folder under safe ASCII names before execution."""
    s.validate()
    validate_media(s, media)
    cmd = [tools["ffmpeg"], "-hide_banner", "-nostdin", "-n"]
    if s.engine != "none" and media.video_start_offset:
        cmd += ["-itsoffset", f"{media.video_start_offset:.9f}"]
    cmd += ["-i", str(video), "-i", str(source)]
    next_input = 2
    watermark_index = None
    subtitle_index = None
    if watermark_name:
        if watermark_name != "watermark.png":
            raise ValueError("Watermark must use the staged safe filename")
        watermark_index = next_input
        next_input += 1
        cmd += ["-i", watermark_name]
    if subtitle_name:
        if not re.fullmatch(r"subtitle\.(srt|ass|ssa|vtt)", subtitle_name):
            raise ValueError("Subtitle must use the staged safe filename")
        if s.subtitle_mode == "soft":
            subtitle_index = next_input
            cmd += ["-i", subtitle_name]
    video_selector = f"0:{media.video_index}" if s.engine == "none" else "0:v:0"
    graph = video_filter_graph(s, media, video_selector, watermark_index, subtitle_name)
    cmd += ["-filter_complex", graph, "-map", "[outv]"]
    if s.audio != "off":
        cmd += ["-map", "1:a?", "-c:a", s.audio]
        if s.audio == "aac":
            cmd += ["-b:a", str(s.audio_bitrate)]
    if s.copy_subtitles:
        cmd += ["-map", "1:s?"]
        if s.container == "mkv":
            cmd += ["-map", "1:t?", "-c:t", "copy"]
    if subtitle_index is not None:
        cmd += ["-map", f"{subtitle_index}:s:0"]
    if subtitle_index is not None:
        output_subtitle_index = len(media.subtitle_codecs) if s.copy_subtitles else 0
        cmd += [f"-metadata:s:s:{output_subtitle_index}", f"language={s.subtitle_language}"]
    if s.copy_subtitles or subtitle_index is not None:
        cmd += ["-c:s", "mov_text" if s.container == "mp4" else "copy"]
    cmd += ["-map_metadata", "1", "-map_chapters", "1"]
    cmd += encoder_options(s)
    if media.hdr:
        cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    if s.container == "mp4":
        cmd += ["-movflags", "+faststart"]
    cmd += ["-fps_mode", "passthrough", "-progress", "pipe:1", "-nostats", str(output)]
    return cmd


def output_path(source: Path, s: Settings, reserved: set[str], output_name: str = "") -> Path:
    directory = Path(s.output_dir).expanduser().resolve() if s.output_dir else source.parent
    if output_name:
        if (len(output_name) > 160 or output_name != output_name.strip() or output_name.endswith(".") or
                re.search(r'[<>:"/\\|?*\x00-\x1f]', output_name) or
                re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", output_name)):
            raise ValueError("Output name must be a simple, valid Windows filename without an extension")
        stem = output_name
    else:
        stem = source.stem[:160] + "_AnimeVideoUpscaler"
    for index in range(100000):
        candidate = directory / f"{stem}{'' if index == 0 else '_' + str(index)}.{s.container}"
        key = os.path.normcase(str(candidate.resolve()))
        if not candidate.exists() and key not in reserved and candidate.resolve() != source.resolve():
            reserved.add(key)
            return candidate
    raise ValueError("Cannot allocate a unique output name")


def publish_output(partial: Path, destination: Path) -> None:
    # Windows rename is atomic and fails if the destination exists. POSIX rename
    # overwrites, so use a same-filesystem hard link there instead.
    if os.name == "nt":
        os.rename(partial, destination)
    else:
        os.link(partial, destination)
        partial.unlink()


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
AI_PROGRESS = re.compile(r"frame\s*=\s*(\d+)\s*/\s*(\d+)")


def progress_value(line: str, stage: str, duration: float, has_ai: bool) -> float | None:
    line = ANSI.sub("", line)
    if stage == "ai":
        match = AI_PROGRESS.search(line)
        if match and int(match[2]) > 0:
            return min(79.0, 80 * int(match[1]) / int(match[2]))
    if stage == "encode" and line.startswith("out_time_us=") and duration > 0:
        try:
            fraction = max(0, float(line.partition("=")[2]) / 1_000_000 / duration)
            return min(99.0, (80 + 20 * fraction) if has_ai else 100 * fraction)
        except ValueError:
            pass
    return None
