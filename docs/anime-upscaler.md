# Anime Video Upscaler & Encoder 1.0.0 · Development guide

This is a local development preview by **@cumaduran**. It is not a release or installer.

## Start the local portable development build

Open `Anime Video Upscaler and Encoder.exe` from the development app folder and
keep its `runtime` and `_internal` support folders beside it. No separately installed
Shutter Encoder or Video2X app is needed at runtime. The app's **Tools** window shows
only its own bundled paths; saved paths from the previous prototype are ignored.

AI requires a compatible Vulkan driver; hardware encoders require the matching GPU
and driver. The packaged CLI and libvideo2x core are built from this project's
modified Video2X-based source. The bundled AI model weights and dependency DLLs
retain their original provenance. The reproduced NVIDIA shutdown crash was fixed
in the source build and tested locally; other hardware and drivers need testing.
The preview is not certified on a clean Windows installation.

## Preview / Önizleme

The queue is on the left, preview in the center and settings on the right. The divider handles can resize each area. Add videos, select one in the queue and use the timeline or **Play**. Videos longer than two seconds start preview at one second to avoid an all-black opening frame; drag the timeline back to see the start. The silent
preview samples up to two frames per second, depending on system speed; it is not
full-speed playback. The picture shows source frames, final target aspect/padding,
PNG watermark placement/size/opacity and external **burn** subtitle at the selected
time. Selectable **soft** subtitles and source subtitle tracks are not rendered in
the preview. Preview works independently of output codec, AI and subtitle-container validation; processing may still reject an incompatible output. AI enhancement, RIFE interpolation, final compression and flicker
reduction are not applied, so it cannot show final AI quality or timing artifacts.

Change watermark settings in **Finishing** or **Advanced settings**; the preview
refreshes automatically. The same FFmpeg filter-graph code is used for placement
in the preview and final output. Output colour and details may still differ after
AI and encoding. Use a short representative real clip to judge the actual result.

## Short AI comparison / Kısa AI karşılaştırması

Select a video, let its preview load and choose **Compare AI sample**. Start time
defaults to the preview position. Choose 0.5–10 seconds and process the sample.
The app cuts an isolated temporary clip and runs the selected AI and encoder through
the normal verification pipeline. The two sampled, silent previews use the same
timeline. The sample excludes source audio, subtitles and watermark, and is removed
when the window closes. Stop the main queue before running a comparison. No media is
uploaded. Choose a model for the sample without changing the queue settings. After a
result, use **Keep result as reference**, choose another model and process the same
time range again. The left and right panes then compare the two processed results;
**Compare with original** restores the source view. **Play A/B comparison** adds
full-speed playback with sound from the original source, approximate synchronization,
side-by-side or split view, linked pan/zoom and 100% output-pixel scale. The sample
clip uses lossless FFV1 YUV444 to avoid transparent RGB frames in Qt playback.
Temporary comparison recovery for legacy runtimes is
enabled by default and can be turned off in the comparison window. It is limited to
the known shutdown crash and verifies all intermediate frames before continuing; a
recovered result is marked with a warning and still requires visual review. The main
queue recovery remains an advanced opt-in. A comparison is not proof of error-free output.

## Quality controls, presets and logs

General-video presets cover source-size re-encoding, gentle low-bitrate cleanup,
1080p compatibility and 4K AI using the general Real-ESRGAN plus model. The 4K
AI presets still require a Vulkan GPU even when the final encoder is CPU based.
The bundled old engine does not accept every model included in the source tree;
unsupported choices stop before decoding and show an actionable diagnostic.

Video bitrate is in bits/s (192,000 = 192 kb/s; 8,000,000 = 8 Mb/s). Zero uses
CRF/CQ instead. AAC audio bitrate is a separate setting, default 192,000 bits/s,
and only applies when AAC is selected. Increasing either output bitrate cannot
restore source information already lost; it can reduce further encoding loss at
the cost of file size. Mild denoising is optional, applied before AI or before the
final encode when AI is off; it can smooth noise but also blur fine detail and motion.
The source preview does not show AI, denoise or flicker filtering; use a processed
sample to judge. Actual visual quality depends on the content and selected model.

Failures open a non-blocking log window with copy and folder buttons. The comparison
window also has **View log**. Logs are stored locally and can contain file paths;
review them before sharing. Only English and Turkish descriptions are reviewed;
other catalogs remain machine-assisted, and some new descriptions fall back to English.

## Per-video settings and watermark

Select a waiting item and choose **Settings for selected video**. The dialog can
apply any system or saved preset, choose a distinct subtitle or output folder, and
set an output name without its extension. The ✦ marker identifies an item with its
own settings. Shared changes no longer replace that item's settings; **Use shared
settings** removes the override. Existing output files are never overwritten.

Choose a PNG watermark in Finishing. Drag it within the preview to position it;
drag its lower-right corner to resize while preserving the image proportions. The
free coordinates apply to the selected overridden item or to shared settings for
other items. The preview updates after release. Processing and preview use the same
placement expressions. Existing corner and margin controls remain available.

For low-resolution anime, the optional **Anime · 480p to 4K detail NVIDIA** preset
uses the existing Real-ESRGAN anime model at 4× and HEVC NVENC. It may require much
more temporary disk and GPU memory. It was exercised on a short 640×480 source
with the known shutdown-recovery warning; compare visually with 2× and inspect the
full output before choosing. A larger scaling factor is not a guarantee of better
quality, and it does not recreate all lost source detail.

## Source development

On a fresh Windows checkout, use 64-bit Python 3.12 and prepare the runtime:

```powershell
py -3.12 -m venv .venv-anime
.\.venv-anime\Scripts\python.exe -m pip install -r app\anime_video_upscaler\requirements-ci.txt
.\.venv-anime\Scripts\python.exe scripts\prepare-independent-deps.py
.\.venv-anime\Scripts\python.exe scripts\prepare-native-toolchain.py
.\.venv-anime\Scripts\python.exe scripts\build-independent-engine.py
.\scripts\prepare-ci-runtime.ps1
.\scripts\start-anime-upscaler.ps1
```

The bundled Real-CUGAN path applies syncgap 0/1/2/3,
processes lossless PNG frames, verifies every output frame and reconstructs a CFR
timeline. It needs additional disk and long-form A/V sync testing. `models-nose`
supports only 2× and noise 0. Model files are copied from this repository. The
preparation scripts verify the pinned dependency hashes, build the native engine
and AI wrapper libraries from source, and keep build files inside `build/`.
`runtime/` is local and ignored by Git; the preparation script refuses to overwrite it.

For a **local development EXE** only, after preparing the runtime:

```powershell
.\scripts\build-dev-portable.ps1
```

The script creates a new `build/anime-video-upscaler-dev` folder and refuses to
overwrite an existing build. It creates neither an installer nor a public release.
Packaging/sharing later requires confirming all binary source versions, notices,
AGPL/GPL/LGPL obligations and Qt/PySide6 distribution requirements.

## Settings and output

### Subtitle matching and processing checks

Choose **Match subtitles from folder** to review proposed matches for inactive
queue items. Exact filenames take priority; otherwise a unique episode number is
used. Ambiguous matches are left unchanged. Confirm the list before applying it:
the match is a suggestion and must be reviewed, especially when the folder holds
multiple series. Applying matches switches to per-video settings and defaults
external subtitles to selectable tracks when the previous mode was off.
External subtitles are decoded to UTF-8 in the temporary job folder; UTF-8 and
Unicode BOMs are supported, with Windows-1254 as the legacy Turkish fallback.
SRT timestamps and decodable external/source text tracks are checked before AI
processing. The original subtitle is not changed. Non-Turkish legacy encodings
may require conversion to UTF-8 first.

### Resume completed AI segments

Under **Advanced settings → AI**, enable **Resume using saved AI segments**.
This optional mode supports Real-ESRGAN, Real-CUGAN with the bundled engine,
Anime4K and RIFE on constant-frame-rate inputs, with denoise off. RIFE segments
overlap by one source frame to preserve interpolation at boundaries. It stores
verified, lossless one-minute AI segments in a hidden
checkpoint folder on the output drive. This can need many gigabytes, unlike the
default streaming mode. Stop the queue or restart after an interruption, select
the video and choose **Retry selected**; completed segments are verified again
and reused. The interrupted segment and final encode are restarted. Successful
publication removes the generated checkpoint files. Changing the source,
settings or engine creates a new checkpoint; prior checkpoint folders remain on
disk for possible reuse or manual removal. This feature has short GPU tests;
test long sources and inspect audio synchronization before relying on it.

The maximum-quality/small-file presets use HEVC and quality-based encoding in
each anime/video and CPU/NVIDIA/AMD category. They target a useful tradeoff,
not a guaranteed maximum quality or minimum size: actual results vary by source,
hardware and settings. CPU refers to the final encoder; AI still requires a
compatible Vulkan GPU. AMD hardware validation remains outstanding.

System presets are grouped by anime/general video and final encoder: CPU, NVIDIA
NVENC and AMD AMF. AI still requires a compatible Vulkan GPU even for CPU-encode
presets; CPU refers to the final encoder. The app tests GPU encoder initialization
before AI processing, but codec/resolution/driver support still needs real validation.
AMD AMF H.264/HEVC/AV1 profiles have not yet been tested on AMD hardware. The tested
RTX 3070 Ti does not support AV1 NVENC; a newer supported NVIDIA GPU is required.
AMD/NVIDIA drivers are operating-system dependencies, not bundled apps. AMD quality
mode uses constant QP, not CRF or NVIDIA CQ, so numerical values are not comparable.
Quality presets use 4K and conservative temporal defaults, while source-size, QHD and
compatibility choices remain available; the best file size and visual quality vary
by content. AI 4× and 4K do not guarantee more faithful detail and may increase size.
Restoration, flicker smoothing and frame interpolation remain optional; test motion,
scene cuts and line shimmer rather than assuming these filters remove all artifacts.
Hover over any setting or click its `i` button for values and guidance.
**Sync settings to waiting videos** is enabled by default. While enabled, future
changes in the main screen or advanced dialog apply to all waiting videos; running
jobs retain their snapshots. Turn it off and select a waiting video to edit only
that video, including subtitle, watermark, encoder, AI and output folder settings.
The editor and preview show the selected video's values. Re-enabling sync keeps
existing settings until the next edit, which then applies to all waiting videos.
New videos inherit the current shared settings, or the displayed settings when
sync is off. The sync preference and queue are saved locally. Presets preserve
machine-specific paths and device preferences. Existing output files are not overwritten.

Audio can be copied, re-encoded to AAC or disabled. MKV is generally more flexible
for subtitles; MP4 soft subtitles may lose ASS styling and bitmap subtitles cannot be
copied. Flicker reduction only targets luminance and may affect flashes/scene cuts.

English, Turkish, Simplified Chinese, Japanese, Portuguese (Portugal), French, German
and Korean are selectable. The six newer translations are machine-assisted and need
native review. Engine logs and some errors remain English; native file dialogs can
use the operating-system language. Qt common dialogs use Brazilian Portuguese when
European Portuguese is unavailable in the installed Qt catalog.

Settings and logs live under `%APPDATA%\AnimeVideoUpscalerEncoder`. On first run,
if no new settings exist, the old `%APPDATA%\AnimeUpscaler7\settings.json` is read
without removing it; prior external tool paths are not used. Tests can redirect the
profile with `ANIME_UPSCALER_HOME`.

The source-built engine streams uncompressed 8-bit RGB frames through bounded pipes
to the final encoder; it does not store a full-length AI intermediate. The final file
and temporary comparison clips still need disk space. A 1 GiB reserve is monitored.
Legacy engine builds fall back to lossless files and require more disk. Exporting
10-bit does not restore precision lost in the 8-bit AI path.
PQ/HLG HDR input is tone-mapped to BT.709 SDR, so HDR output is not preserved.
BT.2020 sources without reliable transfer metadata are rejected. Right-angle
rotation is normalized; ambiguous VFR and long-form A/V sync need further testing.

## Tests / Gerçek video testi

```powershell
$env:ANIME_TEST_FFMPEG = "$PWD\runtime\ffmpeg\ffmpeg.exe"
$env:ANIME_TEST_FFPROBE = "$PWD\runtime\ffmpeg\ffprobe.exe"
$env:ANIME_TEST_VIDEO2X = "$PWD\runtime\engine\avue-engine.exe"
.\.venv-anime\Scripts\python.exe -m pytest -q
```

Synthetic tests cover preview positioning/subtitle timing, queues, output collision,
local runtime discovery, system presets, translations and short GPU workflows. They
do not establish anime visual quality, eliminate temporal artifacts or replace a clean
machine test. Test a 10–30 second clip with motion, scene cuts and subtitles first;
check watermark placement, final frames and audio sync, then try a multi-file queue.

## Release boundary

Local development does not itself push or publish. The Windows Actions workflow
builds an EXE and a portable ZIP on branch pushes, pull requests and manual runs;
it verifies downloaded archive hashes and executes tests without a GPU. It uploads
an Actions artifact. Publishing a GitHub release with a tag beginning with `v`
starts the build and attaches the ZIP and SHA-256 file to that release. Failed tests
block attachment. The release job needs repository contents write permission.
The workflow has not yet been run on GitHub; GPU and clean Windows tests remain necessary.

The queue is saved locally alongside settings. An interrupted job is restored as
interrupted, never started automatically; select it and choose Retry selected. The
activity log reports available disk space. Streaming jobs monitor a 1 GiB reserve;
legacy runtimes also require 5% of the raw-pixel reference before processing. Neither
threshold predicts the final size or guarantees sufficient space. A one-second monitor
stops the job if free space drops below 1 GiB; use a drive with ample free space. Temporary files and final
output use the same drive. Windows Error Reporting is disabled for the app and its
child processes to prevent blocking crash dialogs; in-app logs remain available.
