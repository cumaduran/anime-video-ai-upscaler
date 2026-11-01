# Contributing to Anime Video Upscaler & Encoder

Changes should support the desktop application and its bundled processing workflow. Describe the problem, the approach and the tests performed. Keep changes focused, and preserve third-party attribution and licenses.

The desktop application is in `app/anime_video_upscaler/`; the native engine is in `engine/`. Run `.venv-anime\Scripts\python.exe -m pytest` from the project root. For build and runtime setup see `docs/anime-upscaler.md`; the directory guide is `docs/project-structure.tr.md`. Test changes involving GPU processing on the relevant hardware and report the GPU, driver and model used.

Use clear commit messages such as `fix: correct queue progress` or `docs: explain presets`. Do not include private videos, credentials or personal information in reports.
