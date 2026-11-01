@echo off
setlocal
cd /d "%~dp0app"
if exist "..\.venv-anime\Scripts\pythonw.exe" (
    start "" "..\.venv-anime\Scripts\pythonw.exe" -m anime_video_upscaler
) else (
    echo Create the development environment first. See docs/anime-upscaler.md.
    pause
)
