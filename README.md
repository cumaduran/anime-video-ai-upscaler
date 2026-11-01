# Anime Video Upscaler & Encoder

A Windows desktop application for video upscaling, frame interpolation and encoding.
It includes a queue, preview, presets and a comparison tool. A Vulkan-capable GPU
and a current driver are required for AI processing.

[Türkçe kullanım kılavuzu](README.tr.md)

The native processing engine is based on [Video2X](https://github.com/k4yt3x/video2x),
and this project uses model weights distributed by that project. It also uses
Real-ESRGAN, Real-CUGAN, RIFE and Anime4K. See [NOTICE](NOTICE) and [LICENSE](LICENSE)
for attribution and licensing.

The application is a development preview. Test a short sample before processing
important videos. Portable packages need the entire extracted folder, including
the runtime and supporting files; the executable cannot be copied on its own.
