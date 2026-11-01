from dataclasses import replace
from pathlib import Path

import pytest

from anime_video_upscaler.config import Settings, atomic_json, detect_tools, extra_encoder_options, load_preset, preset_document, read_json
from anime_video_upscaler.pipeline import (Media, ai_command, final_command, fitted_size, output_path,
                                    preview_command, progress_value, publish_output, target_size, validate_media, video_filter_graph)

TOOLS = {"video2x":"video2x.exe", "ffmpeg":"ffmpeg.exe"}
MEDIA = Media(1440,1080,10,24,1.0)


def test_preset_round_trip_is_typed_and_portable(tmp_path):
    settings = replace(Settings(), codec="hevc_nvenc", preset="p6", output_dir="private-path",watermark_path="private-logo.png")
    path = tmp_path / "ön ayar.json"
    atomic_json(path,preset_document("Türkçe",settings))
    name,loaded = load_preset(read_json(path))
    assert name == "Türkçe"
    assert loaded.codec == "hevc_nvenc" and loaded.preset == "p6"
    assert loaded.output_dir == loaded.watermark_path == ""


@pytest.mark.parametrize("raw",[{"gpu":True},{"quality":"20"},{"not_an_option":1},{"watermark_opacity":float("nan")}])
def test_rejects_bad_schema_types(raw):
    with pytest.raises(ValueError):
        Settings.from_dict(raw)


@pytest.mark.parametrize("settings",[
    replace(Settings(),codec="hevc_nvenc"),replace(Settings(),model="realesrgan-plus",scale=2),
    replace(Settings(),target="custom",width=1919),replace(Settings(),qmin=20,qmax=10),
    replace(Settings(),engine="realcugan",cugan_model="models-pro",scale=4),
    replace(Settings(),rc_min_rate=200,rc_max_rate=100)])
def test_rejects_conflicting_settings(settings):
    with pytest.raises(ValueError):
        settings.validate()


@pytest.mark.parametrize("text",["preset=p7","i=evil.mp4","filter_complex=evil","map=0", "tune=film\ntune=animation", "bf=4", "x264-params=crf=18"])
def test_extra_options_cannot_override_shared_or_pipeline_controls(text):
    with pytest.raises(ValueError):
        extra_encoder_options(text)


def test_anamorphic_and_portrait_fit():
    assert fitted_size(Settings(),MEDIA) == (2880,2160)
    assert fitted_size(Settings(),replace(MEDIA,sar=4/3)) == (3840,2160)
    assert fitted_size(Settings(),Media(1080,1920,1,24)) == (1214,2160)
    assert target_size(Settings(),MEDIA) == (3840,2160)
    assert target_size(replace(Settings(),target="2k"),MEDIA) == (2560,1440)


def test_gpu_probe_uses_final_output_dimensions():
    from anime_video_upscaler.pipeline import encoder_probe_command
    settings = replace(Settings(), codec='hevc_nvenc', preset='p6', aspect='fit')
    assert 'color=black:s=2880x2160:r=30' in encoder_probe_command(settings, TOOLS, MEDIA)


def test_output_stream_validation_rejects_missing_audio_and_subtitles():
    from anime_video_upscaler.pipeline import validate_output_streams
    source = {'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}, {'codec_type': 'subtitle'}]}
    output = {'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}, {'codec_type': 'subtitle'}]}
    validate_output_streams(Settings(), source, output, False)
    with pytest.raises(ValueError, match='audio stream count'):
        validate_output_streams(Settings(), source, {'streams': output['streams'][::2]}, False)
    with pytest.raises(ValueError, match='subtitle stream count'):
        validate_output_streams(replace(Settings(), subtitle_mode='soft'), source, output, True)


def test_input_stream_selection_and_safety_guards():
    media=Media.from_probe({"streams":[{"codec_type":"video","index":0,"disposition":{"attached_pic":1}},
        {"codec_type":"video","index":2,"width":640,"height":480,"avg_frame_rate":"24/1"}],"format":{"duration":"2.5"}})
    assert media.video_index == 2 and media.duration == 2.5
    with pytest.raises(ValueError):
        validate_media(Settings(),media)
    for bad in (replace(MEDIA,uncertain_hdr=True),replace(MEDIA,rotation=45)):
        with pytest.raises(ValueError):
            validate_media(Settings(),bad)
    validate_media(Settings(),replace(MEDIA,hdr=True))
    validate_media(Settings(),replace(MEDIA,rotation=90))


def test_bt2020_without_transfer_is_rejected():
    media = Media.from_probe({"streams": [{"codec_type": "video", "index": 0, "width": 96, "height": 64,
                                          "avg_frame_rate": "24/1", "r_frame_rate": "24/1", "color_space": "bt2020nc"}]})
    assert media.uncertain_hdr
    with pytest.raises(ValueError, match="transfer metadata"):
        validate_media(Settings(), media)


def test_ai_legacy_and_current_capabilities():
    args=(Settings(),TOOLS,Path("source ü.mkv"),Path("stage.mkv"),MEDIA)
    old=ai_command(*args,"--no-copy-streams")
    new=ai_command(*args,"--no-copy-audio-streams --no-copy-subtitle-streams")
    assert "--no-copy-streams" in old
    assert "--no-copy-audio-streams" in new
    assert "--no-copy-streams" not in new
    assert old[old.index("-c")+1] == "ffv1"


def test_combined_plan_has_one_final_video_encoder_and_safe_sidecars():
    settings=replace(Settings(),subtitle_mode="burn",watermark_path="a.png",flicker="mild")
    command=final_command(settings,TOOLS,Path("C:/weird name/'x,[x].mkv"),Path("stage.mkv"),Path("out.mkv"),MEDIA,"subtitle.ass","watermark.png")
    graph=command[command.index("-filter_complex")+1]
    assert "subtitles=filename=subtitle.ass" in graph
    assert "deflicker=size=3:mode=am" in graph
    assert "overlay=" in graph and "pad=3840:2160" in graph
    assert command.count("-c:v") == 1 and "-n" in command
    assert "'x" not in graph


def test_soft_mp4_and_attachment_mapping():
    command=final_command(replace(Settings(),subtitle_mode="soft",container="mp4"),TOOLS,Path("src"),Path("stage"),Path("out.mp4"),MEDIA,"subtitle.srt")
    assert "mov_text" in command and "2:s:0" in command and "1:t?" not in command
    with pytest.raises(ValueError):
        validate_media(replace(Settings(),container="mp4"),replace(MEDIA,subtitle_codecs=("hdmv_pgs_subtitle",)))


def test_output_collision_never_overwrites(tmp_path):
    source=tmp_path/"clip.mkv"
    source.write_text("input")
    used=set()
    a=output_path(source,Settings(),used)
    b=output_path(source,Settings(),used)
    assert a != b and a != source
    partial=tmp_path/"partial.mkv"
    partial.write_text("new")
    a.write_text("existing")
    with pytest.raises(FileExistsError):
        publish_output(partial,a)
    assert a.read_text() == "existing" and partial.read_text() == "new"
    publish_output(partial,b)
    assert b.read_text() == "new" and not partial.exists()


def test_progress_never_reports_success_early():
    assert progress_value("\x1b[Kframe=50/100 (50%)","ai",10,True) == 40
    assert progress_value("frame=5/0 (0%)","ai",10,True) is None
    assert progress_value("out_time_us=10000000","encode",10,True) == 99
    assert progress_value("out_time_us=N/A","encode",10,True) is None


def test_recovery_is_off_by_default_and_vfr_is_rejected_for_ai():
    assert Settings().recover_shutdown is False
    with pytest.raises(ValueError, match="VFR"):
        validate_media(Settings(),replace(MEDIA,variable_rate=True))
    validate_media(replace(Settings(),engine="none"),replace(MEDIA,variable_rate=True))


def test_runtime_lookup_never_uses_other_installed_programs(tmp_path,monkeypatch):
    monkeypatch.setattr('anime_video_upscaler.config.runtime_root',lambda: tmp_path)
    assert detect_tools() == {"video2x":"", "ffmpeg":"", "ffprobe":""}
    for folder,name in (("engine","video2x.exe"),("ffmpeg","ffmpeg.exe"),("ffmpeg","ffprobe.exe")):
        path=tmp_path/"runtime"/folder/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.touch()
    result=detect_tools()
    assert all(str(tmp_path) in value for value in result.values())
    assert "Shutter Encoder" not in str(result) and "Program Files" not in str(result)
    branded = tmp_path / "runtime" / "engine" / "avue-engine.exe"
    branded.touch()
    assert detect_tools()["video2x"] == str(branded)


def test_preview_does_not_apply_ai_and_container_restrictions():
    settings = replace(Settings(), container="mp4", model="realesrgan-plus", scale=2)
    media = replace(MEDIA, variable_rate=True, video_index=2, subtitle_codecs=("hdmv_pgs_subtitle",))
    with pytest.raises(ValueError):
        validate_media(settings, media)
    args = preview_command(settings, TOOLS, Path("source.mkv"), media, 0)
    assert "[0:2]" in args[args.index("-filter_complex") + 1]


def test_preview_uses_identical_placement_but_no_temporal_ai_filters():
    settings=replace(Settings(),watermark_path="logo.png",subtitle_mode="burn",flicker="mild")
    final=video_filter_graph(settings,MEDIA,"0:v:0",1,"subtitle.srt")
    preview=video_filter_graph(settings,MEDIA,"0:v:0",1,"subtitle.srt",preview=True,timestamp=5)
    for marker in ("scale=2880:2160:flags=lanczos","pad=3840:2160", "subtitles=filename=subtitle.srt", "overlay=x=main_w-overlay_w-24:y=main_h-overlay_h-24"):
        assert marker in final and marker in preview
    assert "deflicker" in final and "deflicker" not in preview
    assert "setpts=PTS+5.000000/TB" in preview
    args=preview_command(settings,TOOLS,Path("Türkçe video.mkv"),MEDIA,5,"subtitle.srt","watermark.png")
    assert "pipe:1" in args and "-frames:v" in args


def test_free_watermark_and_custom_output_name(tmp_path):
    settings = replace(Settings(), watermark_corner='free', watermark_x=0.25, watermark_y=0.75)
    settings.validate()
    graph = video_filter_graph(settings, MEDIA, '0:v:0', 1, '')
    assert 'x=(main_w-overlay_w)*0.250000' in graph
    assert 'y=(main_h-overlay_h)*0.750000' in graph
    source = tmp_path/'source.mkv'
    source.touch()
    target = output_path(source, settings, set(), 'Naruto Bölüm 1')
    assert target.name == 'Naruto Bölüm 1.mkv'
    for name in ('../bad', 'CON', 'bad:name', 'bad ', 'bad.'):
        with pytest.raises(ValueError):
            output_path(source, settings, set(), name)


def test_audio_video_bitrates_and_restoration_are_independent():
    settings=replace(Settings(),engine='none',audio='aac',audio_bitrate=320000,bit_rate=4_000_000,restoration='mild')
    settings.validate()
    command=final_command(settings,TOOLS,Path('src'),Path('src'),Path('out.mkv'),MEDIA)
    assert command[command.index('-b:a')+1] == '320000'
    assert command[command.index('-b:v')+1] == '4000000'
    assert '-crf' not in command and 'hqdn3d=1.5:1.5:3:3' in command[command.index('-filter_complex')+1]
    with pytest.raises(ValueError): replace(settings,audio_bitrate=1000).validate()


def test_legacy_engine_rejects_unsupported_model_before_decoding():
    settings=replace(Settings(),model='realesr-generalv3',scale=4)
    help_text='--no-copy-streams --realesrgan-model (realesr-animevideov3, realesrgan-plus)'
    with pytest.raises(ValueError,match='does not support model'):
        ai_command(settings,TOOLS,Path('src'),Path('output'),MEDIA,help_text)


@pytest.mark.parametrize('codec', ['h264_amf','hevc_amf','av1_amf'])
def test_amd_uses_valid_constant_qp_or_explicit_video_bitrate(codec):
    from anime_video_upscaler.pipeline import encoder_options,encoder_probe_command
    settings=replace(Settings(),codec=codec,preset='quality',quality=20)
    options=encoder_options(settings)
    assert options[options.index('-rc')+1]=='cqp'
    assert options[options.index('-qp_i')+1]=='20' and options[options.index('-qp_p')+1]=='20'
    assert '-qp_b' in options if codec in ('h264_amf','av1_amf') else '-qp_b' not in options
    assert '-crf' not in options and '-cq' not in options
    probe=encoder_probe_command(settings,TOOLS)
    assert probe[-3:]==['-f','null','-'] and '-qp_i' in probe
    rate=encoder_options(replace(settings,bit_rate=8_000_000))
    assert rate[rate.index('-rc')+1]=='vbr_peak' and rate[rate.index('-b:v')+1]=='8000000'
    assert '-qp_i' not in rate


def test_gpu_preset_names_and_factories_are_valid():
    from anime_video_upscaler.config import encoder_presets
    assert 'quality' in encoder_presets('hevc_amf') and 'p7' in encoder_presets('hevc_nvenc')
    assert any(s.codec.endswith('_amf') for s in FACTORY_PRESETS.values())
    for settings in FACTORY_PRESETS.values(): settings.validate()

from anime_video_upscaler.config import FACTORY_PRESETS


def test_av1_encoder_quality_ranges_are_codec_specific():
    replace(Settings(),codec='av1_amf',preset='quality',quality=200).validate()
    replace(Settings(),codec='av1_nvenc',preset='p6',quality=63).validate()
    with pytest.raises(ValueError): replace(Settings(),codec='hevc_amf',preset='quality',quality=52).validate()
    with pytest.raises(ValueError): replace(Settings(),codec='av1_nvenc',preset='p6',quality=64).validate()


def test_cugan_nose_only_accepts_available_model_dimensions():
    replace(Settings(), engine='realcugan', cugan_model='models-nose', scale=2, noise=0).validate()
    with pytest.raises(ValueError, match='scale 2'):
        replace(Settings(), engine='realcugan', cugan_model='models-nose', scale=4, noise=0).validate()


def test_ai_output_keeps_video_offset_relative_to_original_audio():
    probe = {'streams': [{'codec_type':'video', 'index':0, 'width':640, 'height':480,
                          'avg_frame_rate':'24/1', 'r_frame_rate':'24/1', 'start_time':'0.35'}],
             'format': {'duration':'1.5', 'start_time':'0.10'}}
    media = Media.from_probe(probe)
    assert media.video_start_offset == pytest.approx(0.25)
    command = final_command(Settings(), TOOLS, Path('source'), Path('intermediate'), Path('output'), media)
    assert command[command.index('-itsoffset')+1] == '0.250000000'
    no_ai = final_command(replace(Settings(),engine='none'), TOOLS, Path('source'), Path('source'), Path('output'), media)
    assert '-itsoffset' not in no_ai


def test_mp4_copy_audio_rejects_unsupported_codec_before_processing():
    from dataclasses import replace
    from anime_video_upscaler.pipeline import Media, validate_media
    media = Media(96, 64, 1, 12, audio_codecs=("wmav2",))
    with pytest.raises(ValueError, match="AAC audio or MKV"):
        validate_media(replace(Settings(), container="mp4", audio="copy"), media)
    validate_media(replace(Settings(), container="mp4", audio="aac"), media)
    validate_media(replace(Settings(), container="mkv", audio="copy"), media)


def test_external_subtitle_language_is_tagged_after_copied_tracks():
    media = replace(MEDIA, subtitle_codecs=("subrip", "ass"))
    settings = replace(Settings(), subtitle_mode="soft", subtitle_language="eng")
    command = final_command(settings, TOOLS, Path("src"), Path("stage"), Path("out.mkv"), media, "subtitle.srt")
    index = command.index("-metadata:s:s:2")
    assert command[index + 1] == "language=eng"
    assert Settings.from_dict({}).subtitle_language == "tur"
    command = final_command(replace(settings, copy_subtitles=False), TOOLS, Path("src"), Path("stage"), Path("out.mkv"), media, "subtitle.srt")
    assert "-metadata:s:s:0" in command
