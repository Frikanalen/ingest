import subprocess
import sys
from pathlib import Path

import pytest
from frikanalen_django_api_client.models import VideoFileVariantEnum

from app.media.comand_template import (
    TEMPLATE_DIR,
    ProfileMetadata,
    ProfileTemplateArguments,
    TemplatedCommandGenerator,
    TemplateNotFound,
)
from app.media.loudness.loudness_measurement import LoudnessMeasurement

MEASURED = LoudnessMeasurement(
    integrated_lufs=-27.85,
    truepeak_lufs=-9.61,
    loudness_range=5.2,
    threshold_lufs=-38.2,
    target_offset=0.15,
)


def template_args(**overrides) -> ProfileTemplateArguments:
    return ProfileTemplateArguments(
        **{
            "input_file": Path("./hello"),
            "output_file": Path("./there"),
            "output_dir": Path("."),
            "scratch_dir": Path("./scratch"),
            "seek_s": 0.2,
            "has_audio": True,
            "loudness": None,
            "frame_rate": "25/1",
            "gop_frames": 150,
            "segment_duration_s": "6.000000",
            **overrides,
        }
    )


def test_large_thumb_command_looks_as_expected():
    template = TemplatedCommandGenerator(VideoFileVariantEnum.LARGE_THUMB)
    command = template.render(template_args(output_file=Path("./it would be weird for this to be a file huh")))
    expected_command = (
        'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=720:-1 -aspect 16:9 '
        '-frames:v 1 "it would be weird for this to be a file huh"'
    )
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_thumbnails_seek_before_they_decode():
    """`-ss` after `-i` is an output option: ffmpeg decodes and discards every
    frame up to the seek point, so three thumbnails a quarter of the way in
    cost most of a decode pass. Before `-i` it is an input seek instead."""
    for thumb in (VideoFileVariantEnum.LARGE_THUMB, VideoFileVariantEnum.MED_THUMB, VideoFileVariantEnum.SMALL_THUMB):
        command = TemplatedCommandGenerator(thumb).render(template_args())

        assert command.index("-ss ") < command.index("-i "), command


def test_large_thumb_is_a_single_pass_by_default():
    assert TemplatedCommandGenerator(VideoFileVariantEnum.LARGE_THUMB).metadata.passes == 1


def test_med_thumb_is_narrower_than_large_thumb():
    template = TemplatedCommandGenerator(VideoFileVariantEnum.MED_THUMB)
    command = template.render(template_args(output_file=Path("./out.jpg")))
    expected_command = 'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=320:-1 -aspect 16:9 -frames:v 1 "out.jpg"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_small_thumb_is_narrower_than_med_thumb():
    template = TemplatedCommandGenerator(VideoFileVariantEnum.SMALL_THUMB)
    command = template.render(template_args(output_file=Path("./out.jpg")))
    expected_command = 'ffmpeg -nostats -ss 0.2 -i "hello" -y -vf scale=120:-1 -aspect 16:9 -frames:v 1 "out.jpg"'
    assert command == expected_command, f"Expected: {expected_command}, but got: {command}"


def test_h264_med_reports_progress_on_stdout():
    # Not a VideoFileVariantEnum member -- this template exists but nothing wires it
    # up to a DESIRED_FORMATS entry yet.
    template = TemplatedCommandGenerator("h264_med")
    command = template.render(template_args())

    assert "-progress pipe:1" in command
    assert template.metadata.passes == 1


def test_dash_names_its_output_rather_than_following_the_source():
    """The manifest names its media, so those names must not carry a source stem
    that would need percent-encoding to survive as a URL."""
    metadata = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).metadata

    assert metadata.output_name_for(Path("/uploads/Some Show, Episode 3.mov")) == "manifest.mpd"


def test_dash_is_a_single_ffmpeg_invocation():
    """One decode feeding every rendition, so ffmpeg's progress needs no scaling."""
    template = TemplatedCommandGenerator(VideoFileVariantEnum.DASH)
    command = template.render(template_args())

    assert template.metadata.passes == 1
    assert command.count("ffmpeg") == 1
    assert command.count("-progress pipe:1") == 1


def test_dash_encodes_an_av1_ladder_and_never_upscales():
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args())

    assert command.count("libsvtav1") == 3
    # min(rung, ih) rather than a bare height: a 576i upload must not be
    # blown up to 1080p and charged three times for the privilege.
    for rung in (1080, 720, 360):
        assert f"scale=-2:min({rung}" in command


def test_dash_carries_one_vp9_rung_for_players_that_cannot_decode_av1():
    """720p, and only the one: it exists to be watchable at all on a player
    with no AV1 decoder, not to be a second ladder alongside the first."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args())

    assert command.count("libvpx-vp9") == 1
    # Fed by the same scale as the AV1 720p rung rather than a second one, so
    # the two agree pixel for pixel about what 720p is.
    assert "scale=-2:min(720\\,ih),split=2[v1][v3]" in command


def test_dash_keeps_the_two_codecs_in_separate_adaptation_sets():
    """A player switches renditions within an adaptation set, so a set holding
    both codecs would ask it to switch from AV1 to VP9 mid-playback -- which
    is exactly what the VP9 rung exists to avoid needing."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args())

    assert "id=0,streams=0,1,2 id=1,streams=3" in command


def test_dash_sets_the_keyframe_interval_explicitly():
    """-force_key_frames does not stop libvpx placing keyframes of its own, and
    the muxer starts a segment at whichever keyframe it finds first. Pinning
    -g and -keyint_min together is what makes the segments come out even."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(gop_frames=360))

    assert "-g 360 -keyint_min 360" in command
    assert "-force_key_frames" not in command


def test_dash_asks_for_the_segment_length_it_will_actually_produce():
    """ffmpeg copies -seg_duration into the manifest as the segment length
    players do their seek arithmetic with. Asking for a round 6s while the
    keyframes land every 6.006s is what makes a seek miss."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(
        template_args(gop_frames=360, segment_duration_s="6.006000")
    )

    assert "-seg_duration 6.006000" in command


def test_dash_encodes_at_a_constant_frame_rate():
    """A GOP is a number of frames, so it is only a fixed length of time if the
    frames arrive at a fixed rate."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args())

    assert "-fps_mode cfr" in command


def test_dash_is_told_which_constant_frame_rate():
    """Left to infer it, ffmpeg may not settle on the rate the GOP length and
    the segment length were worked out from -- and then the manifest describes
    segments the encoder never produced."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(frame_rate="60000/1001"))

    assert "-r 60000/1001" in command


def test_dash_audio_is_opus_with_an_aac_set_beside_it():
    """Opus is the better codec and what a current player should get.

    AAC stays because Opus in an MP4 is *silence* on desktop WebKit -- and
    silence is worse than a worse codec. It is the same audience the VP9 rung
    serves: a Safari with no AV1 decoder is likely a Safari with no
    Opus-in-MP4 either, so dropping AAC would hand that viewer a picture with
    no sound. Two audio adaptation sets cost one more file and no measurable
    encode time; the video is what the CPU goes on.
    """
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args())

    assert "-c:a:0 libopus" in command
    assert "-c:a:1 aac" in command


def test_dash_gives_each_audio_codec_an_adaptation_set_of_its_own():
    """Same reason the two video codecs are kept apart: a player switches
    within a set, and Opus and AAC are not interchangeable mid-stream."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args())

    assert "id=2,streams=4 id=3,streams=5" in command


def test_dash_hands_opus_the_48khz_it_is_defined_at():
    """Opus is a 48kHz codec. Unlike the resample after loudnorm, this one is
    not conditional on anything having been measured."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(loudness=None))

    assert "loudnorm" not in command
    assert "-ar:a:0 48000" in command


def test_dash_normalizes_a_measured_source_to_the_web_target():
    """-16 LUFS is what the rest of a browser tab sounds like. Playout works
    to -23 from the figure stored against the original instead, which is why
    the measurement describes the upload rather than this output."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(loudness=MEASURED))

    assert "loudnorm=I=-16:TP=-1" in command
    for measured in ("measured_I=-27.85", "measured_TP=-9.61", "measured_LRA=5.2", "measured_thresh=-38.2"):
        assert measured in command, command


def test_dash_normalizes_in_one_linear_pass_rather_than_riding_the_gain():
    """Without the measurements loudnorm works dynamically, which pumps
    quiet passages up and audibly breathes on speech."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(loudness=MEASURED))

    assert "linear=true" in command
    assert "offset=0.15" in command


def test_dash_resamples_after_normalizing():
    """loudnorm outputs 192kHz, which neither encoder accepts -- without a
    resampler the encode fails outright rather than sounding wrong. Both audio
    outputs are normalized, so both need the resampler."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(loudness=MEASURED))

    for stream in ("0", "1"):
        chain = command[command.index(f"-c:a:{stream} ") :]
        assert chain.index("loudnorm") < chain.index(f"-ar:a:{stream} 48000")


def test_dash_leaves_the_level_alone_when_nothing_was_measured():
    """A wrong gain is worse than no gain."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(loudness=None))

    assert "loudnorm" not in command
    assert "-map 0:a:0 -c:a:0 libopus" in command
    assert "-map 0:a:0 -c:a:1 aac" in command


def test_dash_does_not_normalize_a_source_with_no_measurable_peak():
    """loudnorm has no syntax for an unknown true peak, and rendering the
    null into the filter would produce a command ffmpeg cannot parse."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(
        template_args(loudness=MEASURED.model_copy(update={"truepeak_lufs": None}))
    )

    assert "loudnorm" not in command
    assert "None" not in command


def test_dash_leaves_out_the_audio_adaptation_set_when_there_is_no_audio():
    """An adaptation set with no representation in it is not valid DASH."""
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(has_audio=False))

    assert "libopus" not in command
    assert "-c:a" not in command
    assert '-adaptation_sets "id=0,streams=0,1,2 id=1,streams=3"' in command


def test_dash_includes_the_audio_adaptation_set_when_there_is_audio():
    command = TemplatedCommandGenerator(VideoFileVariantEnum.DASH).render(template_args(has_audio=True))

    assert "-map 0:a:0 -c:a:0 libopus" in command
    assert "-map 0:a:0 -c:a:1 aac" in command
    assert '-adaptation_sets "id=0,streams=0,1,2 id=1,streams=3 id=2,streams=4 id=3,streams=5"' in command


def test_a_template_must_say_how_its_output_is_named():
    with pytest.raises(ValueError):
        ProfileMetadata()

    with pytest.raises(ValueError):
        ProfileMetadata(output_file_extension="webm", output_file_name="manifest.mpd")


def test_revision_defaults_to_the_first_one():
    """A template that says nothing is revision 1, not revision 0.

    Zero is reserved for files registered before revisions were recorded at
    all, so a template must never be able to claim it.
    """
    assert ProfileMetadata(output_file_extension="jpg").revision == 1


def test_revision_cannot_claim_the_untracked_sentinel():
    with pytest.raises(ValueError):
        ProfileMetadata(output_file_extension="jpg", revision=0)


def test_revision_is_read_from_the_header():
    assert ProfileMetadata(output_file_name="manifest.mpd", revision=4).revision == 4


@pytest.mark.parametrize(
    "format_name", [f.value for f in (VideoFileVariantEnum.DASH, VideoFileVariantEnum.LARGE_THUMB)]
)
def test_shipped_templates_declare_a_usable_revision(format_name):
    assert TemplatedCommandGenerator(format_name).metadata.revision >= 1


def test_templates_are_found_with_no_git_and_from_somewhere_else_entirely(tmp_path):
    """Neither a git checkout nor any particular working directory is something
    the templates should depend on.

    They were previously located by forking `git rev-parse --show-toplevel`
    and falling back, silently, to a hardcoded `/app`. In the image that came
    out right whichever branch ran -- `/app` is equally the WORKDIR and the
    root of the checkout copied in with the source -- so neither the fork nor
    the fallback was ever the part being relied on, and a wrong answer would
    have surfaced as a missing template rather than as a broken lookup.
    """
    repo_root = Path(__file__).resolve().parent.parent
    read_a_template = (
        "from app.media.comand_template import TemplatedCommandGenerator as G; print(G('dash').metadata.revision)"
    )

    result = subprocess.run(
        [sys.executable, "-c", read_a_template],
        cwd=tmp_path,
        env={"PATH": "", "PYTHONPATH": str(repo_root)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().isdigit(), result.stdout


def test_a_format_with_no_template_says_where_it_looked():
    """Loudly, rather than resolving to a directory that happens to exist: the
    templates decide what `current_revision` reports, and so what the
    reconciler thinks is stale."""
    with pytest.raises(TemplateNotFound) as raised:
        TemplatedCommandGenerator("no_such_format")

    assert "no_such_format.j2" in str(raised.value)
    assert str(TEMPLATE_DIR) in str(raised.value)


def test_a_template_is_read_and_compiled_once_however_many_generators_want_it():
    """`FormatProducer.produce` builds a generator per format per video, and a
    catalogue-wide backfill runs that thousands of times on the event loop.
    Sharing the compiled template is what keeps that from being thousands of
    blocking reads."""
    first = TemplatedCommandGenerator(VideoFileVariantEnum.DASH)
    second = TemplatedCommandGenerator(VideoFileVariantEnum.DASH)

    assert second.template is first.template
    # The enum member and its value must not be two cache entries.
    assert TemplatedCommandGenerator("dash").template is first.template
