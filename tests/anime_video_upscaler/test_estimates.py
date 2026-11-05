from dataclasses import replace

from anime_video_upscaler.config import Settings
from anime_video_upscaler.estimates import add_observation, estimate
from anime_video_upscaler.pipeline import Media


def test_history_estimates_use_comparable_jobs_and_observed_range():
    history = []
    settings = Settings()
    media = Media(640, 480, 10, 24, audio_codecs=("aac",))
    add_observation(history, settings, media, 20, 1000)
    add_observation(history, settings, media, 30, 2000)
    longer = replace(media, duration=20)
    assert estimate(history, replace(settings, output_dir="another"), longer) == (40, 60, 2000, 4000, 2)
    assert estimate(history, replace(settings, quality=21), longer) is None
    assert estimate(history, settings, replace(longer, width=1280)) is None
    assert estimate(history, settings, replace(longer, audio_codecs=("flac",))) is None
    assert estimate(history, replace(settings, resume_segments=True), longer) is None


def test_history_ignores_invalid_observations_and_remains_bounded():
    history = []
    settings = Settings()
    media = Media(640, 480, 10, 24)
    add_observation(history, settings, media, float("nan"), 100)
    add_observation(history, settings, replace(media, duration=0), 10, 100)
    assert not history
    for _ in range(60):
        add_observation(history, settings, media, 10, 100)
    assert len(history) == 50
