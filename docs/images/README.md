# Documentation screenshots

Captured from the actual Qt development app on 2026-10-30, using an isolated local
profile. No personal queue or copyrighted episode was used. These are real widget
captures, not interface mockups. `demo-scene.svg` is an original vector scene made
for this project's documentation; a short pan/zoom version supplied the input.

`ai-comparison.png` shows a successful one-second Real-CUGAN reference-backend
sample: models-se, 2×, noise 0, sync gap 1, 640×360 at 12 fps to 1280×720,
CPU H.264 CRF 18 / fast. Verified crash recovery was disabled. The same sample's
actual diagnostic window is in `processing-log.png`. This illustrates operation;
it is not a quality benchmark or evidence of long-form temporal quality.

The main window uses the Real-CUGAN accurate NVIDIA preset. The separate per-video
view changes only the second queued video to QHD while the first retains 4K.
Finishing demonstrates the app's own icon as a PNG watermark and an original SRT
line. No outside application is needed for these previews.

When updating the interface, recapture the affected images from the real app and
review both READMEs. Keep private paths, media and logs out of public screenshots.

`ab-playback.png` was captured from the real Qt A/B player using the original demo
scene, a lossless YUV444 sample and its source-built Real-CUGAN 2× output.
